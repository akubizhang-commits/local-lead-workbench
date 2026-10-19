"""Cross-process serialisation of schema initialisation (P1-5 / P1-9, design §3.2).

Every process that opens the database (API, worker, export child, restore) runs the
schema set-up inside ``migrate_lock``, so two processes starting on a fresh data
directory never interleave DDL.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import logging
import os
from pathlib import Path
import time

log = logging.getLogger(__name__)

LOCK_WAIT_SECONDS = 120
LOCK_RETRY_SECONDS = 0.1


class MigrationError(Exception):
    """Schema initialisation failed; the database must not be used."""


class MigrationLockTimeout(MigrationError):
    def __init__(self, message="数据库正在由另一个进程初始化，请稍后重试"):
        super().__init__(message)


class SchemaNotReady(MigrationError):
    def __init__(self, message="数据库尚未完成初始化，请稍后重试"):
        super().__init__(message)


@contextmanager
def migrate_lock(data_dir, *, wait_seconds=None):
    """Exclusive ``{data}/migrate.lock``; LOCK_NB retried every 0.1 s, up to 120 s."""
    wait_seconds = LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    path = Path(data_dir) / "migrate.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        start = time.monotonic()
        logged = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = time.monotonic() - start
                if waited >= wait_seconds:
                    raise MigrationLockTimeout() from None
                if waited > 2 and not logged:
                    logged = True
                    log.info("waiting for migrate.lock", extra={"event": "migration_lock_wait"})
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
