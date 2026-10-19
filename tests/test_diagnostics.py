"""P1-7: redacted diagnostic bundle (design §6.3–6.5)."""
import io
import json
import logging
import os
from pathlib import Path
import random
import string
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from leadworkbench import diagnostics, logging_setup
from leadworkbench.api import create_app

COMPANY = "Zeta Unique Roofing 9137"
CONTACT = "Marisol Quintero-Baptiste"
EMAIL = "buyer.zeta@example.com"
PHONE = "+1 408-555-0199"
SITE = "https://zeta-unique.example.com/contact"
KEY = "AIzaSyTESTKEY0123456789abcdefghijklmno"
PROXY = "http://user:pw@proxy.example:8080"
WHITELIST = {"manifest.json", "environment.json", "config.json", "worker.json", "runs.json",
             "logs/api.log", "logs/worker.log", "logs/export.log"}


@pytest.fixture(autouse=True)
def _restore_root():
    root = logging.getLogger()
    level = root.level
    yield
    for handler in [h for h in root.handlers if getattr(h, logging_setup.MARK, False)]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)


def client_for(app):
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    return client


def unzip(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="diagnostics-')
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def seed(client, data_dir: Path):
    company = client.post("/api/companies", json={"name": COMPANY, "website": SITE, "city": "San Jose"})
    assert company.status_code in (200, 201), company.text
    company = company.json()
    contact = client.post(f"/api/companies/{company['id']}/contacts",
                          json={"revision": company["revision"], "name": CONTACT, "email": EMAIL, "phone": PHONE})
    assert contact.status_code in (200, 201), contact.text
    (data_dir / "social-secrets.json").write_text(json.dumps({"youtube_api_key": KEY}))
    (data_dir / "social-connections.json").write_text(json.dumps({"proxy_url": PROXY}))
    # Rule-breaking messages, as if a future change logged business data.
    log = logging.getLogger("leadworkbench.test")
    log.warning("company %s at %s, mail %s, phone %s", COMPANY, SITE, EMAIL, PHONE)
    log.warning("contact %s; key %s via %s", CONTACT, KEY, PROXY)
    log.info('settings {"youtube_api_key": "%s", "proxy_url": "%s"}', KEY, PROXY)
    try:
        raise ValueError(f"failed to read {COMPANY} {SITE}")
    except ValueError:
        log.exception("adapter failed")
    with (data_dir / "logs/api.log").open("a") as handle:
        handle.write(f"plain text line {EMAIL}\n")


def assert_clean(files, token):
    text = "\n".join(files.values())
    for secret in (COMPANY, CONTACT, EMAIL, PHONE, SITE, "zeta-unique", KEY, PROXY, "proxy.example", token,
                   str(Path.home())):
        assert secret not in text, secret


def test_bundle_has_no_business_data_or_secrets(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with client_for(app) as client:
        token = client.headers["X-Local-Token"]
        seed(client, tmp_path)
        logging.getLogger("leadworkbench.test").warning("token %s", token)
        files = unzip(client.get("/api/diagnostics/bundle"))
    assert_clean(files, token)
    assert set(files) <= WHITELIST and "logs/api.log" in files
    assert not any(name.endswith(".sqlite3") for name in files)
    manifest = json.loads(files["manifest.json"])
    assert set(manifest["files"]) == set(files)
    assert manifest["name_redaction"] == "done" and manifest["app_version"] == "0.1.0"
    assert manifest["git_commit"] != "" and manifest["note"] == diagnostics.NOTE
    environment = json.loads(files["environment.json"])
    assert environment["counts"]["companies"] == 1 and environment["counts"]["contacts"] >= 1
    config = json.loads(files["config.json"])
    assert config["social_connections"]["proxy_url"] == "已设置"
    assert config["social_connections"]["youtube_api_key"] == "已设置"
    assert config["environment"]["LEAD_PORT"] in (True, False)
    lines = [json.loads(line) if line.startswith("{") else line for line in files["logs/api.log"].splitlines()]
    assert diagnostics.UNSTRUCTURED in lines
    messages = [line["msg"] for line in lines if isinstance(line, dict)]
    assert any("company <name> at <url> mail <email>, phone <phone>" == m for m in messages)
    # (b) a contact name from the database is replaced in msg
    assert any(m.startswith("contact <name>;") for m in messages)
    # (a) exc keeps frame lines and the exception type only
    failure = next(line for line in lines if isinstance(line, dict) and line["msg"] == "adapter failed")
    exc_lines = failure["exc"].splitlines()
    assert exc_lines[0].startswith("Traceback") and exc_lines[-1] == "ValueError"
    assert all(diagnostics.FRAME.match(line) for line in exc_lines[1:-1])
    assert "raise ValueError" not in failure["exc"]


def test_degraded_mode_bundle_skips_name_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "storage_unavailable")
    logs = tmp_path / "logs"
    logs.mkdir()
    record = {"ts": "2026-10-19T00:00:00.000+00:00", "level": "ERROR", "event": "unhandled", "msg": "boom",
              "exc": f'Traceback (most recent call last):\n  File "{Path.home()}/x.py", line 3, in run\n'
                     f'    do({COMPANY!r})\nValueError: {COMPANY}\n\nDuring handling of the above exception, '
                     'another exception occurred:\n\nTraceback (most recent call last):\n'
                     '  File "/tmp/y.py", line 9, in main\nKeyError: \'secret\''}
    (logs / "api.log").write_text(json.dumps(record) + "\n")
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    assert app.state.unavailable
    with client_for(app) as client:
        assert client.get("/api/settings").status_code == 503
        files = unzip(client.get("/api/diagnostics/bundle"))
    manifest = json.loads(files["manifest.json"])
    assert manifest["name_redaction"] == "skipped"
    assert manifest["name_redaction_note"] == diagnostics.SKIPPED_NOTE
    assert json.loads(files["runs.json"]) == {"error": "storage_unavailable"}
    exc = json.loads(files["logs/api.log"].splitlines()[0])["exc"]
    assert COMPANY not in exc and "secret" not in exc and str(Path.home()) not in exc
    assert exc.splitlines() == [
        "Traceback (most recent call last):", '  File "~/x.py", line 3, in run', "ValueError",
        "During handling of the above exception, another exception occurred:",
        "Traceback (most recent call last):", '  File "/tmp/y.py", line 9, in main', "KeyError"]


def test_log_budget_keeps_newest_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "LOG_BUDGET", 3000)
    logs = tmp_path / "logs"
    logs.mkdir()
    def line(i):
        return json.dumps({"level": "INFO", "event": "log", "msg": f"n{i:04d}"}) + "\n"
    (logs / "worker.log.1").write_text("".join(line(i) for i in range(0, 100)))
    (logs / "worker.log").write_text("".join(line(i) for i in range(100, 120)))
    (logs / "api.log").write_text(line(999))
    files = diagnostics.collect_logs(tmp_path, diagnostics.Redactor(), {"status": "skipped"})
    worker = [json.loads(l)["msg"] for l in files["logs/worker.log"].splitlines()]
    assert worker[-1] == "n0119" and worker == sorted(worker)
    assert sum(len(text.encode()) for text in files.values()) <= 3000
    assert files["logs/api.log"].strip()


def test_name_redaction_timing(tmp_path):
    """(d) 120k names x 5 MB log; the time is printed and kept as evidence (no threshold, §6.3 rule 8)."""
    rng = random.Random(7)
    names = ["".join(rng.choices(string.ascii_letters + " ", k=rng.randint(6, 30))) for _ in range(120_000)]
    redactor = diagnostics.Redactor(names=names)
    logs = tmp_path / "logs"
    logs.mkdir()
    lines, size = [], 0
    while size < 5_000_000:
        text = json.dumps({"level": "INFO", "event": "log", "msg": " ".join(rng.choice(names) for _ in range(3))
                           + " item 123 finished"})
        lines.append(text)
        size += len(text) + 1
    (logs / "worker.log").write_text("\n".join(lines) + "\n")
    state = {"status": "done"}
    started = time.perf_counter()
    files = diagnostics.collect_logs(tmp_path, redactor, state)
    elapsed = time.perf_counter() - started
    print(f"name redaction: {len(names)} names, {size / 1e6:.1f} MB log, {elapsed:.2f} s, status={state['status']}")
    out = os.environ.get("LEAD_PERF_OUT")
    if out:
        Path(out, "p1-7-redaction-timing.txt").write_text(
            f"names={len(names)} log_bytes={size} seconds={elapsed:.2f} status={state['status']}\n")
    first = json.loads(files["logs/worker.log"].splitlines()[0])["msg"]
    assert state["status"] in ("done", "partial")
    if state["status"] == "done":
        assert first == "<name> <name> <name> item 123 finished"
