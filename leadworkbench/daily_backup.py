"""Scheduled daily backups and their S3 upload (AWS trial, design §5.1–5.3).

Two state files, one writer each, both 0600 and replaced atomically:

- ``backup-state.json``: written only by ``run_local`` (worker main loop, holds worker.lock).
- ``backup-upload-state.json``: written only by ``upload_pending`` (background thread, no
  worker.lock). There is no "in progress" state: an upload killed half way is retried.

A damaged state file reads as empty (WARNING ``backup_state_invalid``).
"""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3

log = logging.getLogger(__name__)
STATE = "backup-state.json"
UPLOAD_STATE = "backup-upload-state.json"
RETRY_AFTER = timedelta(hours=1)
UPLOAD_ATTEMPTS = 3
CHECK_SECONDS = 60
STARTUP_DELAY_SECONDS = 120


def utcnow():
    return datetime.now(UTC)


def iso(moment):
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _parse(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def read_state(directory, name) -> dict:
    path = Path(directory) / name
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        log.warning("backup state unreadable", extra={"event": "backup_state_invalid", "kind": name})
        return {}
    try:
        state = json.loads(text)
        if not isinstance(state, dict):
            raise ValueError("not an object")
    except ValueError:
        log.warning("backup state invalid", extra={"event": "backup_state_invalid", "kind": name})
        return {}
    return state


def write_state(directory, name, state):
    path = Path(directory) / name
    temporary = path.with_name(f".{name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(temporary, path)


def _scheduled(now, cfg):
    hour, minute = (int(part) for part in cfg.backup_time.split(":"))
    local = now.astimezone(cfg.tz)
    return local, local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def due(state, now, cfg) -> bool:
    """Today's backup time has passed, no successful daily backup today, retry time reached."""
    local, scheduled = _scheduled(now, cfg)
    if local < scheduled:
        return False
    last = _parse(state.get("last_daily_at"))
    if state.get("last_daily_status") == "ok" and last and last.astimezone(cfg.tz).date() == local.date():
        return False
    retry = _parse(state.get("next_attempt_at"))
    return not (retry and now < retry)


def next_due(state, now, cfg):
    local, scheduled = _scheduled(now, cfg)
    if due(state, now, cfg):
        return now
    retry = _parse(state.get("next_attempt_at"))
    if local < scheduled:
        candidate = scheduled
    else:
        candidate = scheduled + timedelta(days=1)
        last = _parse(state.get("last_daily_at"))
        done_today = state.get("last_daily_status") == "ok" and last and last.astimezone(cfg.tz).date() == local.date()
        if retry and not done_today and retry > now:
            candidate = retry.astimezone(cfg.tz)
    return candidate.astimezone(UTC)


def run_local(store, cfg, now=None) -> dict:
    """Create ``auto-daily-…sqlite3`` (caller holds worker.lock) and record the outcome."""
    from .errors import classify_storage_error
    now = now or utcnow()
    state = read_state(store.directory, STATE)
    try:
        name = store.backup(kind="daily")["name"]
        state = {"last_daily_at": iso(now), "last_daily_name": name, "last_daily_status": "ok",
                 "last_daily_error": None, "next_attempt_at": None}
    except (sqlite3.Error, OSError) as exc:
        classified = classify_storage_error(exc, store.directory)
        code = classified.code if classified and classified.code in ("disk_full", "readonly_storage") else "backup_failed"
        state = {**state, "last_daily_status": "failed", "last_daily_error": code,
                 "last_failed_at": iso(now), "next_attempt_at": iso(now + RETRY_AFTER)}
    try:
        write_state(store.directory, STATE, state)
    except OSError:
        log.warning("could not write backup state", exc_info=True,
                    extra={"event": "backup_state_write_failed", "kind": STATE})
    return state


# ---- S3 upload (§5.2): background thread, never holds worker.lock ----

def upload_due(upload_state, daily_state, now) -> bool:
    name = daily_state.get("last_daily_name")
    if daily_state.get("last_daily_status") != "ok" or not name:
        return False
    if upload_state.get("name") != name:
        return True
    if upload_state.get("status") != "failed" or int(upload_state.get("attempts") or 0) >= UPLOAD_ATTEMPTS:
        return False
    retry = _parse(upload_state.get("next_at"))
    return retry is None or now >= retry


def _error_code(exc):
    import re
    code = None
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
    code = re.sub(r"[^A-Za-z0-9_.-]", "", str(code or type(exc).__name__))[:64]
    return code or "upload_failed"


def _upload_errors():
    errors = [OSError]
    try:
        from botocore.exceptions import BotoCoreError, ClientError
        errors += [BotoCoreError, ClientError]
    except ImportError:
        pass
    return tuple(errors)


def upload_pending(directory, cfg, uploader, now=None) -> dict:
    """Upload the newest daily backup if due; writes backup-upload-state.json only.
    Unexpected exceptions propagate (the worker logs ``backup_upload_crashed``)."""
    import errno
    now = now or utcnow()
    daily_state = read_state(directory, STATE)
    state = read_state(directory, UPLOAD_STATE)
    if uploader is None or not upload_due(state, daily_state, now):
        return state
    name = daily_state["last_daily_name"]
    attempts = int(state.get("attempts") or 0) + 1 if state.get("name") == name else 1
    failed = {"name": name, "status": "failed", "at": iso(now), "key": None, "attempts": attempts,
              "next_at": iso(now + RETRY_AFTER)}
    path = Path(directory) / "backups" / name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or exc.errno == errno.ELOOP:
            state = {**failed, "error_code": "local_file_missing", "attempts": UPLOAD_ATTEMPTS}
        else:
            state = {**failed, "error_code": _error_code(exc)}
        log.warning("backup upload failed", extra={"event": "backup_upload_failed", "error_type": type(exc).__name__,
                                                   "code": state["error_code"]})
        write_state(directory, UPLOAD_STATE, state)
        return state
    try:
        with os.fdopen(fd, "rb") as handle:
            key = uploader.upload(handle, name)
        state = {"name": name, "status": "ok", "at": iso(now), "key": key, "error_code": None,
                 "attempts": attempts, "next_at": None}
    except ImportError as exc:
        state = {**failed, "error_code": "boto3_missing"}
        log.warning("backup upload failed", extra={"event": "backup_upload_failed", "error_type": type(exc).__name__,
                                                   "code": "boto3_missing"})
    except _upload_errors() as exc:
        state = {**failed, "error_code": _error_code(exc)}
        log.warning("backup upload failed", extra={"event": "backup_upload_failed", "error_type": type(exc).__name__,
                                                   "code": state["error_code"]})
    write_state(directory, UPLOAD_STATE, state)
    return state
