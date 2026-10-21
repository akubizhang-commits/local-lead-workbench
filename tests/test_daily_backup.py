"""AWS trial D5: scheduled daily backups (design §5.1–5.3, §10.1 test_daily_backup)."""
from datetime import UTC, datetime, timedelta
import json
import logging
import os
import sqlite3

import pytest

from leadworkbench import daily_backup as daily
from leadworkbench.config import load
from leadworkbench.db import Store

CFG = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"})  # 03:30 Asia/Shanghai


def at(text):
    """Shanghai wall clock → UTC."""
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(UTC)


def test_due_rules():
    assert not daily.due({}, at("2026-10-21T03:29"), CFG)
    assert daily.due({}, at("2026-10-21T03:30"), CFG)
    assert daily.due({}, at("2026-10-21T23:00"), CFG)  # missed while restarting: catch up
    done = {"last_daily_at": daily.iso(at("2026-10-21T03:31")), "last_daily_status": "ok"}
    assert not daily.due(done, at("2026-10-21T12:00"), CFG)
    assert daily.due(done, at("2026-10-22T03:30"), CFG)
    yesterday_late = {"last_daily_at": daily.iso(at("2026-10-20T23:00")), "last_daily_status": "ok"}
    assert daily.due(yesterday_late, at("2026-10-21T03:31"), CFG)
    failed = {"last_daily_status": "failed", "next_attempt_at": daily.iso(at("2026-10-21T04:31"))}
    assert not daily.due(failed, at("2026-10-21T04:00"), CFG)
    assert daily.due(failed, at("2026-10-21T04:31"), CFG)


def test_due_uses_the_configured_timezone():
    tokyo = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test", "LEAD_TIMEZONE": "Asia/Tokyo"})
    # 02:30 Shanghai = 03:30 Tokyo: due in Tokyo, not yet in Shanghai.
    assert daily.due({}, at("2026-10-21T02:30"), tokyo)
    assert not daily.due({}, at("2026-10-21T02:30"), CFG)
    assert not daily.due({}, at("2026-10-21T02:29"), tokyo)


def test_next_due():
    assert daily.next_due({}, at("2026-10-21T01:00"), CFG) == at("2026-10-21T03:30")
    done = {"last_daily_at": daily.iso(at("2026-10-21T03:31")), "last_daily_status": "ok"}
    assert daily.next_due(done, at("2026-10-21T12:00"), CFG) == at("2026-10-22T03:30")
    failed = {"last_daily_status": "failed", "next_attempt_at": daily.iso(at("2026-10-21T04:31"))}
    assert daily.next_due(failed, at("2026-10-21T04:00"), CFG) == at("2026-10-21T04:31")


def test_run_local_success_and_naming(tmp_path):
    store = Store(tmp_path)
    state = daily.run_local(store, CFG, now=at("2026-10-21T03:31"))
    assert state["last_daily_status"] == "ok" and state["last_daily_name"].startswith("auto-daily-")
    assert (tmp_path / "backups" / state["last_daily_name"]).is_file()
    assert daily.read_state(tmp_path, daily.STATE) == state
    assert (os.stat(tmp_path / daily.STATE).st_mode & 0o777) == 0o600


def test_run_local_failure_retries_in_an_hour(tmp_path, monkeypatch):
    store = Store(tmp_path)
    ok = daily.run_local(store, CFG, now=at("2026-10-20T03:31"))
    def full(kind="manual"):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(store, "backup", full)
    now = at("2026-10-21T03:31")
    state = daily.run_local(store, CFG, now=now)
    assert state["last_daily_status"] == "failed" and state["last_daily_error"] == "disk_full"
    assert state["next_attempt_at"] == daily.iso(now + timedelta(hours=1))
    assert state["last_daily_name"] == ok["last_daily_name"]  # last good backup is remembered
    assert not daily.due(state, now + timedelta(minutes=30), CFG)
    def other(kind="manual"):
        raise OSError("boom")
    monkeypatch.setattr(store, "backup", other)
    assert daily.run_local(store, CFG, now=now)["last_daily_error"] == "backup_failed"


@pytest.mark.parametrize("name", [daily.STATE, daily.UPLOAD_STATE])
def test_corrupt_state_reads_empty(tmp_path, caplog, name):
    (tmp_path / name).write_text("{broken")
    with caplog.at_level(logging.WARNING):
        assert daily.read_state(tmp_path, name) == {}
    assert any(getattr(r, "event", "") == "backup_state_invalid" and r.kind == name for r in caplog.records)
    (tmp_path / name).write_text("[1, 2]")
    assert daily.read_state(tmp_path, name) == {}


# ---- S3 upload (FakeUploader) ----
import asyncio  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

UPLOAD_CFG = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test",
                   "LEAD_BACKUP_S3_BUCKET": "lw-backups", "LEAD_BACKUP_S3_REGION": "ap-northeast-1"})


class FakeUploader:
    def __init__(self, error=None, gate=None):
        self.calls, self.error, self.gate = [], error, gate

    def upload(self, fileobj, name):
        self.calls.append(name)
        if self.gate is not None:
            self.gate.wait(10)
        data = fileobj.read()
        if self.error is not None:
            raise self.error
        self.data = data
        return f"leadworkbench/2026/10/{name}"


def client_error(code):
    ClientError = pytest.importorskip("botocore.exceptions").ClientError
    return ClientError({"Error": {"Code": code, "Message": "secret bucket name lw-backups"}}, "PutObject")


def test_upload_due_rules():
    now = at("2026-10-21T05:00")
    ok = {"last_daily_status": "ok", "last_daily_name": "auto-daily-b.sqlite3"}
    assert not daily.upload_due({}, {}, now)
    assert not daily.upload_due({}, {**ok, "last_daily_status": "failed"}, now)
    assert daily.upload_due({}, ok, now)
    assert daily.upload_due({"name": "auto-daily-a.sqlite3", "status": "ok"}, ok, now)
    assert not daily.upload_due({"name": "auto-daily-b.sqlite3", "status": "ok"}, ok, now)
    failed = {"name": "auto-daily-b.sqlite3", "status": "failed", "attempts": 1, "next_at": daily.iso(now)}
    assert daily.upload_due(failed, ok, now)
    assert not daily.upload_due({**failed, "next_at": daily.iso(now + timedelta(minutes=1))}, ok, now)
    assert not daily.upload_due({**failed, "attempts": 3}, ok, now)


def test_upload_success_and_failures(tmp_path):
    store = Store(tmp_path)
    local = daily.run_local(store, UPLOAD_CFG)
    fake = FakeUploader()
    state = daily.upload_pending(tmp_path, UPLOAD_CFG, fake)
    assert state["status"] == "ok" and fake.calls == [local["last_daily_name"]]
    assert fake.data == (tmp_path / "backups" / local["last_daily_name"]).read_bytes()
    assert (os.stat(tmp_path / daily.UPLOAD_STATE).st_mode & 0o777) == 0o600
    assert daily.upload_pending(tmp_path, UPLOAD_CFG, fake)["status"] == "ok" and len(fake.calls) == 1


def test_upload_retries_three_times_at_most(tmp_path):
    store = Store(tmp_path)
    daily.run_local(store, UPLOAD_CFG)
    fake = FakeUploader(error=client_error("AccessDenied"))
    now = daily.utcnow()
    for attempt in range(1, 4):
        state = daily.upload_pending(tmp_path, UPLOAD_CFG, fake, now=now)
        assert (state["status"], state["error_code"], state["attempts"]) == ("failed", "AccessDenied", attempt)
        assert "lw-backups" not in json.dumps(state)
        now += timedelta(hours=1)
    assert daily.upload_pending(tmp_path, UPLOAD_CFG, fake, now=now)["attempts"] == 3
    assert len(fake.calls) == 3


def test_upload_error_code_is_sanitised(tmp_path):
    store = Store(tmp_path)
    daily.run_local(store, UPLOAD_CFG)
    state = daily.upload_pending(tmp_path, UPLOAD_CFG, FakeUploader(error=client_error("Bad Code<script>" + "x" * 99)))
    assert state["error_code"] == ("BadCodescript" + "x" * 99)[:64]
    EndpointConnectionError = pytest.importorskip("botocore.exceptions").EndpointConnectionError
    (tmp_path / daily.UPLOAD_STATE).unlink()
    state = daily.upload_pending(tmp_path, UPLOAD_CFG, FakeUploader(error=EndpointConnectionError(endpoint_url="u")))
    assert state["error_code"] == "EndpointConnectionError"


def test_missing_or_symlinked_backup(tmp_path):
    store = Store(tmp_path)
    local = daily.run_local(store, UPLOAD_CFG)
    path = tmp_path / "backups" / local["last_daily_name"]
    moved = tmp_path / "elsewhere.sqlite3"
    path.rename(moved)
    state = daily.upload_pending(tmp_path, UPLOAD_CFG, FakeUploader())
    assert (state["error_code"], state["attempts"]) == ("local_file_missing", 3)
    (tmp_path / daily.UPLOAD_STATE).unlink()
    path.symlink_to(moved)
    state = daily.upload_pending(tmp_path, UPLOAD_CFG, FakeUploader())
    assert (state["error_code"], state["attempts"]) == ("local_file_missing", 3)


def test_deleted_after_open_still_uploads(tmp_path):
    store = Store(tmp_path)
    local = daily.run_local(store, UPLOAD_CFG)
    path = tmp_path / "backups" / local["last_daily_name"]
    expected = path.read_bytes()
    class Deleting(FakeUploader):
        def upload(self, fileobj, name):
            path.unlink()  # retention removes it while the upload runs
            return super().upload(fileobj, name)
    fake = Deleting()
    assert daily.upload_pending(tmp_path, UPLOAD_CFG, fake)["status"] == "ok"
    assert fake.data == expected


def test_boto3_missing(tmp_path, monkeypatch):
    store = Store(tmp_path)
    daily.run_local(store, UPLOAD_CFG)
    class Missing(FakeUploader):
        def upload(self, fileobj, name):
            raise ModuleNotFoundError("No module named 'boto3'")
    assert daily.upload_pending(tmp_path, UPLOAD_CFG, Missing())["error_code"] == "boto3_missing"


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def _quiet_social(store, **kwargs):
    return False


async def test_blocked_upload_never_delays_a_restore(tmp_path, monkeypatch, caplog):
    from leadworkbench import backup_upload, social_worker, worker
    from leadworkbench.worker import worker_lock
    store = Store(tmp_path)
    daily.run_local(store, UPLOAD_CFG)  # today's backup exists: no new local backup is due
    gate = threading.Event()
    fake = FakeUploader(gate=gate)
    monkeypatch.setattr(backup_upload, "from_config", lambda cfg: fake)
    monkeypatch.setattr(daily, "STARTUP_DELAY_SECONDS", 0)
    monkeypatch.setattr(daily, "CHECK_SECONDS", 0.05)
    monkeypatch.setattr(social_worker, "process_social_item", _quiet_social)
    dispatched = []
    original = Store.dispatch_due
    monkeypatch.setattr(Store, "dispatch_due", lambda self: (dispatched.append(1), original(self))[1])
    task = asyncio.create_task(worker.serve(tmp_path, cfg=UPLOAD_CFG))
    try:
        assert await _until(lambda: fake.calls)
        got = await asyncio.to_thread(lambda: _lock_within(tmp_path, worker_lock, 5))
        assert got  # worker.lock is free while the upload thread is blocked
        before = len(dispatched)
        assert await _until(lambda: len(dispatched) > before)
        await asyncio.sleep(0.3)
        assert len(fake.calls) == 1  # only one upload at a time
        gate.set()
        assert await _until(lambda: daily.read_state(tmp_path, daily.UPLOAD_STATE).get("status") == "ok")
    finally:
        gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _lock_within(directory, worker_lock, seconds):
    with worker_lock(directory, wait_seconds=seconds):
        return True


async def test_crashing_upload_is_logged_and_the_loop_continues(tmp_path, monkeypatch, caplog):
    from leadworkbench import backup_upload, social_worker, worker
    store = Store(tmp_path)
    daily.run_local(store, UPLOAD_CFG)
    class Crash(FakeUploader):
        def upload(self, fileobj, name):
            self.calls.append(name)
            raise RuntimeError("unexpected")
    fake = Crash()
    monkeypatch.setattr(backup_upload, "from_config", lambda cfg: fake)
    monkeypatch.setattr(daily, "STARTUP_DELAY_SECONDS", 0)
    monkeypatch.setattr(daily, "CHECK_SECONDS", 0.05)
    monkeypatch.setattr(social_worker, "process_social_item", _quiet_social)
    dispatched = []
    original = Store.dispatch_due
    monkeypatch.setattr(Store, "dispatch_due", lambda self: (dispatched.append(1), original(self))[1])
    task = asyncio.create_task(worker.serve(tmp_path, cfg=UPLOAD_CFG))
    try:
        assert await _until(lambda: any(getattr(r, "event", "") == "backup_upload_crashed" for r in caplog.records))
        crashed = [r for r in caplog.records if getattr(r, "event", "") == "backup_upload_crashed"]
        assert all(r.exc_info is None and r.error_type == "RuntimeError" for r in crashed)
        before = len(dispatched)
        assert await _until(lambda: len(dispatched) > before)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---- GET /api/backups/status ----

def status_client(tmp_path, cfg):
    from fastapi.testclient import TestClient
    from leadworkbench import auth
    from leadworkbench.api import create_app
    import secrets
    app = create_app(tmp_path, config=cfg)
    password = secrets.token_urlsafe(18)
    auth.create_user(app.state.store, "alice", password)
    client = TestClient(app, base_url="https://lw.test")
    assert client.post("/api/auth/login", json={"username": "alice", "password": password}).status_code == 200
    return app, client


def test_status_endpoint_has_no_bucket_details(tmp_path):
    app, client = status_client(tmp_path, UPLOAD_CFG)
    body = client.get("/api/backups/status").json()
    assert body["daily_enabled"] is True and body["time"] == "03:30" and body["timezone"] == "Asia/Shanghai"
    assert body["last_daily_at"] is None and body["s3"] == {"configured": True, "last_status": None,
                                                           "last_at": None, "error_code": None}
    assert body["retention_active"] is False and body["next_due_at"]
    daily.run_local(app.state.store, UPLOAD_CFG)
    daily.upload_pending(tmp_path, UPLOAD_CFG, FakeUploader())
    text = client.get("/api/backups/status").text
    for secret in ("lw-backups", "ap-northeast-1", "leadworkbench/", "auto-daily-"):
        assert secret not in text
    body = json.loads(text)
    assert body["last_daily_status"] == "ok" and body["s3"]["last_status"] == "ok"


def test_status_with_damaged_state_files(tmp_path):
    app, client = status_client(tmp_path, UPLOAD_CFG)
    (tmp_path / daily.STATE).write_text("{bad")
    (tmp_path / daily.UPLOAD_STATE).write_text("{bad")
    body = client.get("/api/backups/status").json()
    assert body["last_daily_at"] is None and body["last_daily_status"] is None
    assert body["s3"]["last_status"] is None and body["s3"]["error_code"] is None


def test_status_local_mode(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    token = client.get("/api/session").json()["token"]
    body = client.get("/api/backups/status", headers={"X-Local-Token": token}).json()
    assert body["daily_enabled"] is False and body["s3"]["configured"] is False
