"""Restore-only maintenance gate and the shared BEGIN IMMEDIATE entry (P1-1, design §7.2–7.3).

Ordinary API requests no longer run one at a time. Concurrency is handled by
SQLite (WAL, one connection per call, busy_timeout) and by doing read-then-write
inside one IMMEDIATE transaction. Only restoring a backup replaces the database
file, so only that request takes the gate exclusively.

The gate lives on one event loop thread (the API middleware); it uses counters and
short polling instead of asyncio primitives so it is independent of which loop the
test client runs on. Other threads may read ``exclusive_pending``.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio
import sqlite3
import time

from .errors import Busy, DomainError

SHARED_WAIT_SECONDS = 30.0
EXCLUSIVE_WAIT_SECONDS = 10.0
POLL_SECONDS = 0.01
MAINTENANCE = "正在恢复备份，请稍后重试。"
MAINTENANCE_BUSY = "仍有请求在处理（例如大列表），请稍后再恢复备份。"


class MaintenanceGate:
    def __init__(self, *, shared_wait=SHARED_WAIT_SECONDS, exclusive_wait=EXCLUSIVE_WAIT_SECONDS):
        self.shared_wait = shared_wait
        self.exclusive_wait = exclusive_wait
        self.active = 0
        self._exclusive = False
        self._waiting = 0

    @property
    def exclusive_pending(self) -> bool:
        """True while a restore is queued or running (writer preference)."""
        return self._exclusive or self._waiting > 0

    @asynccontextmanager
    async def shared(self):
        deadline = time.monotonic() + self.shared_wait
        while self.exclusive_pending:
            if time.monotonic() >= deadline:
                raise Busy(MAINTENANCE, code="maintenance")
            await asyncio.sleep(POLL_SECONDS)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1

    @asynccontextmanager
    async def exclusive(self):
        deadline = time.monotonic() + self.exclusive_wait
        self._waiting += 1
        try:
            while self._exclusive or self.active > 0:
                if time.monotonic() >= deadline:
                    raise DomainError(MAINTENANCE_BUSY, code="maintenance_busy", status=409)
                await asyncio.sleep(POLL_SECONDS)
            self._exclusive = True
        finally:
            self._waiting -= 1
        try:
            yield
        finally:
            self._exclusive = False


BEGIN_RETRIES = 4
BEGIN_WAITED_SECONDS = 1.0


def _busy(exc):
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in (5, 6):
        return True
    text = str(exc).lower()
    return "database is locked" in text or "database is busy" in text or "database table is locked" in text


def begin_immediate(con):
    """BEGIN IMMEDIATE, retrying only a BUSY that returned at once (nothing has run yet).

    A BUSY that already waited out busy_timeout (>= 1 s) is raised unchanged and
    becomes 503 db_busy at the API boundary."""
    for attempt in range(BEGIN_RETRIES):
        start = time.monotonic()
        try:
            con.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if not _busy(exc) or time.monotonic() - start >= BEGIN_WAITED_SECONDS or attempt == BEGIN_RETRIES - 1:
                raise
            time.sleep(0.05 * 2 ** attempt)
