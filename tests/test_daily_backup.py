"""AWS trial D5: scheduled daily backups (design §5.1–5.3, §10.1 test_daily_backup)."""
from datetime import UTC, datetime, timedelta
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
