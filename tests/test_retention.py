"""P1-8: retention policy with first-run confirmation (design §10.7)."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from leadworkbench import retention
from leadworkbench.api import create_app

DAY = 86400


def ago(days):
    return time.time() - days * DAY


def iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def maps_dir(root, run, item, days, *, metadata=True):
    path = root / "runs" / run / item / f"maps-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    (path / "results.jsonl").write_text('{"title": "RAW-RESULT-MARKER"}\n' * 10)
    if metadata:
        (path / "metadata.json").write_text(json.dumps({"finished_at": iso(days)}))
    os.utime(path, (ago(days), ago(days)))
    return path


def export_job(root, status, days, *, fmt="xlsx"):
    job_id = uuid.uuid4().hex
    jobs = root / "exports" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job = {"id": job_id, "status": status, "format": fmt, "finished_at": iso(days) if status != "running" else None}
    (jobs / f"{job_id}.json").write_text(json.dumps(job))
    target = root / "exports" / (f"{job_id}.{fmt}" if status != "running" else f"{job_id}.part")
    target.write_bytes(b"x" * 100)
    if status == "running":
        os.utime(target, (ago(3), ago(3)))
    return job_id, target


def add_run(store, run_id, status):
    with store.connect() as con:
        con.execute("INSERT INTO runs(id, kind, status, progress, created_at) VALUES(?, 'discovery', ?, '{}', ?)",
                    (run_id, status, iso(50)))


def dump_hash(root):
    with sqlite3.connect(root / "workbench.sqlite3") as con:
        return hashlib.sha256("\n".join(con.iterdump()).encode()).hexdigest()


def snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if "logs" not in p.parts and not p.name.startswith("retention.json")
                  and not p.name.startswith("workbench.sqlite3") and p.name != "retention.lock")


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "data"
    app = create_app(root, allowed_hosts={"testserver"})
    store = app.state.store
    for run_id, status in (("r_done", "completed"), ("r_paused", "paused"), ("r_fail", "failed")):
        add_run(store, run_id, status)
    expired = [maps_dir(root, "r_done", "i1", 40), maps_dir(root, "r_done", "i2", 40),
               maps_dir(root, "r_fail", "i1", 40, metadata=False)]
    recent = maps_dir(root, "r_done", "i1", 10)
    paused = maps_dir(root, "r_paused", "i1", 40)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link = root / "runs" / "r_done" / "i1" / f"maps-{uuid.uuid4().hex}"
    link.symlink_to(outside, target_is_directory=True)
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    autos = []
    for index in range(12):
        path = backups / f"auto-pre-restore-2026101{index:02d}T000000-{index:08x}.sqlite3"
        path.write_bytes(b"b" * 10)
        os.utime(path, (ago(20 - index), ago(20 - index)))
        autos.append(path)
    manual = [backups / f"backup-2026100{i}T000000-0000000{i}.sqlite3" for i in range(3)]
    manual.append(backups / "contact-quality-20261001.sqlite3")
    for path in manual:
        path.write_bytes(b"m")
        os.utime(path, (ago(100), ago(100)))
    old_exports = [export_job(root, "succeeded", 10), export_job(root, "succeeded", 9, fmt="csv")]
    fresh_export = export_job(root, "succeeded", 2)
    running_export = export_job(root, "running", 0)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    with client:
        yield dict(root=root, app=app, client=client, expired=expired, recent=recent, paused=paused, link=link,
                   outside=outside, autos=autos, manual=manual, old_exports=old_exports, fresh_export=fresh_export,
                   running_export=running_export)


def expected_paths(w):
    root = w["root"]
    paths = {str(p.relative_to(root)) for p in w["expired"]}
    paths |= {str(p.relative_to(root)) for p in w["autos"][:2]}  # the two oldest auto backups
    for job_id, target in w["old_exports"]:
        paths |= {str(target.relative_to(root)), f"exports/jobs/{job_id}.json"}
    return paths


def test_first_run_lists_without_deleting_and_confirm_deletes_exactly_the_plan(world):
    client, root = world["client"], world["root"]
    before_files, before_db = snapshot(root), dump_hash(root)
    view = client.get("/api/retention").json()
    assert view["state"] == "awaiting_confirmation" and view["policy"] == retention.DEFAULT_POLICY
    preview = view["preview"]
    assert {item["path"] for item in preview["items"]} == expected_paths(world)
    assert preview["total_files"] == 9 and not preview["truncated"]
    assert {item["category"] for item in preview["items"]} == {"maps_raw", "auto_backup", "export"}
    assert any(item["reason"] == "已超过 30 天" for item in preview["items"])
    assert any(item["reason"] == "超出最近 10 份" for item in preview["items"])
    assert snapshot(root) == before_files  # listing deletes nothing
    wrong = client.post("/api/retention/confirm", json={"plan_id": "0" * 64})
    assert wrong.status_code == 409 and wrong.json()["code"] == "retention_plan_changed"
    assert wrong.json()["preview"]["plan_id"] == preview["plan_id"]
    assert snapshot(root) == before_files
    confirmed = client.post("/api/retention/confirm", json={"plan_id": preview["plan_id"]})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["deleted"] == 9 and body["errors"] == [] and body["state"] == "active"
    assert body["freed_bytes"] == preview["total_bytes"]
    after = set(snapshot(root))
    removed = set(before_files) - after
    assert {p for p in removed if p in expected_paths(world)} == expected_paths(world)
    for path in removed:  # only plan items and their contents / emptied parents disappeared
        assert any(path == p or path.startswith(p + "/") for p in expected_paths(world)) or path == "runs/r_fail" \
            or path == "runs/r_fail/i1" or path == "runs/r_done/i2"
    for kept in [*world["manual"], world["recent"], world["paused"], world["fresh_export"][1],
                 world["running_export"][1], *world["autos"][2:]]:
        assert kept.exists(), kept
    assert world["link"].is_symlink() and (world["outside"] / "keep.txt").exists()
    assert dump_hash(root) == before_db
    lines = [json.loads(l) for l in (root / "logs/retention.log").read_text().splitlines()]
    assert sum(l["action"] == "deleted" for l in lines) == 9 and lines[-1]["action"] == "summary"
    state = json.loads((root / "retention.json").read_text())
    assert state["state"] == "active" and state["confirmed_policy"] == retention.DEFAULT_POLICY
    assert (os.stat(root / "retention.json").st_mode & 0o777) == 0o600


def test_auto_runs_only_when_active_and_not_stricter(world):
    client, root = world["client"], world["root"]
    plan = client.get("/api/retention").json()["preview"]["plan_id"]
    assert retention.run(root, "auto") is None  # awaiting confirmation: nothing happens
    assert all(p.exists() for p in world["expired"])
    assert client.post("/api/retention/confirm", json={"plan_id": plan}).status_code == 200
    newer = maps_dir(root, "r_done", "i3", 31)
    result = retention.run(root, "auto")
    assert result["deleted"] == 1 and not newer.exists()
    history = client.get("/api/retention").json()["history"]
    assert [h["trigger"] for h in history] == ["auto", "confirm"]
    looser = client.put("/api/retention/policy", json={"maps_raw_days": 60, "auto_backup_keep": 10, "export_days": 7})
    assert looser.json()["state"] == "active" and looser.json()["confirmed_policy"]["maps_raw_days"] == 60
    stricter = client.put("/api/retention/policy", json={"maps_raw_days": 7, "auto_backup_keep": 10, "export_days": 7})
    assert stricter.status_code == 200 and stricter.json()["state"] == "awaiting_confirmation"
    assert retention.run(root, "auto") is None
    assert world["recent"].exists()
    assert any(item["path"] == str(world["recent"].relative_to(root)) for item in stricter.json()["preview"]["items"])


def test_policy_validation(world):
    client = world["client"]
    for body in ({"maps_raw_days": 6, "auto_backup_keep": 10, "export_days": 7},
                 {"maps_raw_days": 30, "auto_backup_keep": 101, "export_days": 7},
                 {"maps_raw_days": 30, "auto_backup_keep": 10, "export_days": 91},
                 {"maps_raw_days": 30, "auto_backup_keep": 10},
                 {"maps_raw_days": 30, "auto_backup_keep": 10, "export_days": 7, "extra": 1}):
        assert client.put("/api/retention/policy", json=body).status_code == 422, body
    assert client.post("/api/retention/confirm", json={"plan_id": "xyz"}).status_code == 422


def test_corrupt_state_requires_confirmation_again(world):
    root, client = world["root"], world["client"]
    (root / "retention.json").write_text('{"state": "active", "policy": ')
    view = client.get("/api/retention").json()
    assert view["state"] == "awaiting_confirmation"
    assert list(root.glob("retention.json.corrupt-*"))
    assert retention.run(root, "auto") is None


def test_lock_timeout_is_conflict(world, monkeypatch):
    root, client = world["root"], world["client"]
    monkeypatch.setattr(retention, "LOCK_WAIT_SECONDS", 0.2)
    plan = client.get("/api/retention").json()["preview"]["plan_id"]
    with retention.retention_lock(root):
        threadless = client.post("/api/retention/confirm", json={"plan_id": plan})
    assert threadless.status_code == 409 and threadless.json()["code"] == "conflict"
    assert threadless.json()["detail"] == retention.BUSY_MESSAGE


def test_backup_naming_and_kinds(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    store = app.state.store
    assert store.backup()["name"].startswith("backup-")
    assert store.backup(kind="pre-repair")["name"].startswith("auto-pre-repair-")
    with pytest.raises(ValueError):
        store.backup(kind="weekly")
    client = TestClient(app)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    manual = client.post("/api/backups").json()["name"]
    assert client.post("/api/backups/restore", json={"name": manual}).status_code == 200
    (tmp_path / "backups" / "contact-quality-1.sqlite3").write_bytes(b"x")
    listed = {b["name"]: b for b in client.get("/api/backups").json()}
    kinds = sorted((b["kind"], b["reason"]) for b in listed.values())
    assert ("auto", "pre-restore") in kinds and ("auto", "pre-repair") in kinds
    assert ("other", None) in kinds and listed[manual]["kind"] == "manual"
    assert all(isinstance(b["size_bytes"], int) for b in listed.values())


def test_overview_uses_cached_summary(world, monkeypatch):
    client, app = world["client"], world["app"]
    cache = app.state.retention_summary
    first = client.get("/api/overview").json()["retention"]
    assert first["state"] == "awaiting_confirmation" and first["export_days"] == 7
    cache.thread.join(5)
    current = client.get("/api/overview").json()["retention"]
    assert current["pending_files"] == 9 and current["pending_bytes"] > 0
    original = retention.summary
    def slow(*args, **kwargs):
        time.sleep(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(retention, "summary", slow)
    client.get("/api/retention")  # invalidates the cache
    started = time.perf_counter()
    stale = client.get("/api/overview").json()["retention"]
    assert time.perf_counter() - started < 0.2
    assert stale["pending_files"] == 9
    assert cache.thread.daemon and cache.thread.is_alive()
    assert cache.refresh() is False  # at most one recomputation at a time
    cache.thread.join(5)


def test_background_summary_respects_pending_restore(world, monkeypatch):
    app, root = world["app"], world["root"]
    calls = []
    monkeypatch.setattr(retention, "_protected_runs", lambda data_dir: calls.append(data_dir) or set())
    cache = app.state.retention_summary
    if cache.thread is not None:
        cache.thread.join(5)
    app.state.gate._waiting = 1
    try:
        cache.invalidate()
        assert cache.refresh() is False
        assert retention.summary(root, should_stop=lambda: app.state.gate.exclusive_pending) is None
    finally:
        app.state.gate._waiting = 0
    assert calls == []
    assert cache.refresh() is True
    cache.thread.join(5)
    assert len(calls) == 1


def test_registry_run_dir_is_protected(world):
    root = world["root"]
    registry = root / "run" / "maps"
    registry.mkdir(parents=True)
    in_use = world["expired"][0]
    (registry / "123.json").write_text(json.dumps({"guard_pid": 123, "run_dir": str(in_use)}))
    paths = {item["path"] for item in retention.candidates(root, retention.DEFAULT_POLICY)}
    assert str(in_use.relative_to(root)) not in paths
    assert str(world["expired"][1].relative_to(root)) in paths


def test_busy_database_skips_maps_category_only(world, monkeypatch):
    monkeypatch.setattr(retention, "_protected_runs", lambda data_dir: None)
    categories = {item["category"] for item in retention.candidates(world["root"], retention.DEFAULT_POLICY)}
    assert categories == {"auto_backup", "export"}


def test_diagnostics_bundle_has_retention_config_and_log(world):
    import io
    import zipfile
    client = world["client"]
    plan = client.get("/api/retention").json()["preview"]["plan_id"]
    assert client.post("/api/retention/confirm", json={"plan_id": plan}).status_code == 200
    response = client.get("/api/diagnostics/bundle")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    files = {name: archive.read(name).decode() for name in archive.namelist()}
    config = json.loads(files["config.json"])["retention"]
    assert config["state"] == "active" and config["policy"] == retention.DEFAULT_POLICY
    assert "logs/retention.log" in files and '"action": "deleted"' in files["logs/retention.log"]
    assert all("RAW-RESULT-MARKER" not in text for text in files.values())


def test_daily_backups_are_counted_separately(tmp_path):
    """AWS trial §5.1: auto-daily-* and the other auto-* each keep their newest N."""
    root = tmp_path / "data"
    create_app(root, allowed_hosts={"testserver"})
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    others, dailies = [], []
    for index in range(12):
        other = backups / f"auto-pre-restore-202610{index:02d}T000000-{index:08x}.sqlite3"
        daily = backups / f"auto-daily-202610{index:02d}T000000-{index:08x}.sqlite3"
        for path, group in ((other, others), (daily, dailies)):
            path.write_bytes(b"b" * 10)
            os.utime(path, (ago(30 - index), ago(30 - index)))
            group.append(path)
    items = retention._backup_candidates(root, 10)
    reasons = {item["path"]: item["reason"] for item in items}
    assert {f"backups/{p.name}" for p in others[:2]} | {f"backups/{p.name}" for p in dailies[:2]} == set(reasons)
    assert {reasons[f"backups/{p.name}"] for p in others[:2]} == {"超出最近 10 份"}
    assert {reasons[f"backups/{p.name}"] for p in dailies[:2]} == {"超出最近 10 份每日备份"}


def test_daily_backups_alone_leave_other_group_unchanged(tmp_path):
    root = tmp_path / "data"
    create_app(root, allowed_hosts={"testserver"})
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    for index in range(3):
        path = backups / f"auto-pre-repair-202610{index:02d}T000000-{index:08x}.sqlite3"
        path.write_bytes(b"b")
    for index in range(15):
        path = backups / f"auto-daily-202610{index:02d}T000000-{index:08x}.sqlite3"
        path.write_bytes(b"b")
        os.utime(path, (ago(30 - index), ago(30 - index)))
    items = retention._backup_candidates(root, 10)
    assert len(items) == 5 and all("每日备份" in item["reason"] for item in items)


def test_unconfirmed_policy_deletes_no_daily_backup(tmp_path):
    root = tmp_path / "data"
    create_app(root, allowed_hosts={"testserver"})
    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    for index in range(15):
        (backups / f"auto-daily-202610{index:02d}T000000-{index:08x}.sqlite3").write_bytes(b"b")
    assert retention.run(root, "auto") is None
    assert len(list(backups.glob("auto-daily-*"))) == 15


def test_daily_backup_kind(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    name = app.state.store.backup(kind="daily")["name"]
    assert name.startswith("auto-daily-")
    client = TestClient(app)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    listed = {b["name"]: b for b in client.get("/api/backups").json()}
    assert (listed[name]["kind"], listed[name]["reason"]) == ("auto", "daily")
