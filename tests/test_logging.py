"""P1-7: JSON line logs with rotation (design §6.1–6.2, §6.5)."""
import json
import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from leadworkbench import logging_setup
from leadworkbench.api import create_app
from leadworkbench.errors import REQUEST_ID


@pytest.fixture(autouse=True)
def _restore_root():
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    for handler in [h for h in root.handlers if getattr(h, logging_setup.MARK, False)]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    assert [h for h in root.handlers if h not in handlers] == []


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def ours():
    return [h for h in logging.getLogger().handlers if getattr(h, logging_setup.MARK, False)]


def test_json_lines_with_context_fields(tmp_path):
    path = logging_setup.configure("worker", tmp_path)
    assert path == tmp_path / "logs" / "worker.log"
    log = logging.getLogger("leadworkbench.test")
    tokens = [REQUEST_ID.set("req_abc"), logging_setup.RUN_ID.set("run1"), logging_setup.ITEM_ID.set("item1"),
              logging_setup.STAGE.set("discovery")]
    try:
        log.info("hello %s", "world", extra={"event": "probe", "duration_ms": 12})
    finally:
        for var, token in zip((REQUEST_ID, logging_setup.RUN_ID, logging_setup.ITEM_ID, logging_setup.STAGE),
                              tokens):
            var.reset(token)
    log.warning("plain")
    first, second = lines(path)
    assert {"ts", "level", "logger", "process", "pid", "event", "msg"} <= set(first)
    assert first["msg"] == "hello world" and first["event"] == "probe" and first["process"] == "worker"
    assert (first["request_id"], first["run_id"], first["item_id"], first["stage"]) == \
        ("req_abc", "run1", "item1", "discovery")
    assert first["duration_ms"] == 12 and first["pid"] == os.getpid()
    assert second["event"] == "log" and "run_id" not in second
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 2048)
    path = logging_setup.configure("worker", tmp_path)
    log = logging.getLogger("leadworkbench.test")
    for index in range(100):
        log.info("line %d %s", index, "x" * 40)
    assert path.with_name("worker.log.1").exists()
    assert path.stat().st_size <= 2048
    for line in lines(path.with_name("worker.log.1")):
        assert line["event"] == "log"


def test_readonly_directory_falls_back_to_console(tmp_path, capsys):
    tmp_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert logging_setup.configure("worker", tmp_path) is None
        logging.getLogger("leadworkbench.test").warning("still visible")
    finally:
        tmp_path.chmod(0o700)
    assert not (tmp_path / "logs").exists()
    assert "still visible" in capsys.readouterr().err
    assert len(ours()) == 1


def test_configure_is_idempotent(tmp_path):
    for _ in range(3):
        logging_setup.configure("worker", tmp_path)
    assert len(ours()) == 2
    logging.getLogger("leadworkbench.test").info("once")
    assert [line["msg"] for line in lines(tmp_path / "logs/worker.log")] == ["once"]


def test_token_and_secrets_masked_on_write(tmp_path):
    logging_setup.configure("worker", tmp_path)
    logging_setup.hide_token("tok-ABCDEFGH1234")
    log = logging.getLogger("leadworkbench.test")
    log.info("header X-Local-Token: tok-ABCDEFGH1234 and %s", '{"YouTube_API_Key": "AIzaSySECRET", "empty_token": ""}')
    try:
        raise ValueError("Authorization=Bearer-XYZ")
    except ValueError:
        log.exception("failed")
    text = (tmp_path / "logs/worker.log").read_text()
    for secret in ("tok-ABCDEFGH1234", "AIzaSySECRET", "Bearer-XYZ"):
        assert secret not in text
    first, second = lines(tmp_path / "logs/worker.log")
    assert '"YouTube_API_Key": "<已设置>"' in first["msg"] and '"empty_token": ""' in first["msg"]
    assert "<已隐藏>" in first["msg"] and "<已隐藏>" in second["exc"]
    assert second["error_type"] == "ValueError"


def test_api_logs_writes_and_errors_but_not_fast_gets(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        token = client.get("/api/session").json()["token"]
        client.headers["X-Local-Token"] = token
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/tasks?limit=3").status_code == 200
        assert client.get("/api/companies/missing?secret=1").status_code == 404
        client.post("/api/exclusion-rules", json={"kind": "name", "pattern": "x" * 3})
    records = [line for line in lines(tmp_path / "logs/api.log") if line["event"] == "http_request"]
    assert [(r["method"], r["route"]) for r in records] == [
        ("GET", "/api/companies/{id}"), ("POST", "/api/exclusion-rules")]
    missing = records[0]
    assert missing["status"] == 404 and missing["code"] == "not_found" and missing["query_keys"] == ["secret"]
    assert missing["request_id"].startswith("req_") and isinstance(missing["duration_ms"], int)
    assert "missing" not in json.dumps(missing)
    assert token not in (tmp_path / "logs/api.log").read_text()
