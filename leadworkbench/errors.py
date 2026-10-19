"""Domain errors with explicit codes (P1-6, design §5.2–5.5)."""
from __future__ import annotations
from contextvars import ContextVar
import errno
import os
from pathlib import Path
import shutil
import sqlite3


class DomainError(Exception):
    code = "invalid"
    status = 400

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None, **extra):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.extra = extra

    def __str__(self):
        return self.message


class NotFound(DomainError, KeyError):
    """404; still a KeyError so existing ``pytest.raises(KeyError)`` keeps working."""
    code = "not_found"
    status = 404

    def __init__(self, message: str = "记录不存在", **kwargs):
        super().__init__(message, **kwargs)

    def __str__(self):  # KeyError would quote the message
        return self.message


class Invalid(DomainError, ValueError):
    code = "invalid"
    status = 400


class Conflict(DomainError, ValueError):
    code = "conflict"
    status = 409


class StorageError(DomainError):
    code = "storage_unavailable"
    status = 503


class Busy(DomainError):
    code = "db_busy"
    status = 503

    def __init__(self, message: str = "本地数据库正忙，请稍后重试。", *, retry_after: int = 2, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ExportBusy(DomainError):
    code = "export_busy"
    status = 409


DISK_FULL = "磁盘空间不足，本次修改未保存。请清理磁盘空间后重试。"
READONLY = "数据目录不可写，本次修改未保存。请检查 {data_dir} 的权限或磁盘是否只读后重试。"
CORRUPT = "本地数据库文件损坏，请停止写入，在设置页导出诊断包，并从备份恢复。"
INTERNAL = "服务出错，请稍后重试。如果反复出现，请在设置页导出诊断包，并提供错误编号 {request_id}。"

_SQLITE_BUSY, _SQLITE_LOCKED, _SQLITE_READONLY, _SQLITE_IOERR = 5, 6, 8, 10
_SQLITE_CORRUPT, _SQLITE_FULL, _SQLITE_CANTOPEN, _SQLITE_NOTADB = 11, 13, 14, 26
_LOW_SPACE = 1_000_000


def _primary(exc):
    code = getattr(exc, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None


def _writable(data_dir):
    try:
        return data_dir is not None and os.access(data_dir, os.W_OK)
    except OSError:
        return False


def _inside(path, data_dir):
    if not path:
        return True  # no file name: the failing write was ours
    try:
        return Path(path).resolve().is_relative_to(Path(data_dir).resolve())
    except (OSError, ValueError):
        return False


def _low_space(data_dir):
    try:
        return data_dir is not None and shutil.disk_usage(data_dir).free < _LOW_SPACE
    except OSError:
        return False


def classify_storage_error(exc, data_dir=None, *, disk_full_message=DISK_FULL):
    """StorageError/Busy for storage failures, None for anything else (design §5.3)."""
    if isinstance(exc, DomainError):
        return None
    readonly = StorageError(READONLY.format(data_dir=data_dir or "数据目录"), code="readonly_storage", status=503)
    full = StorageError(disk_full_message, code="disk_full", status=507)
    if isinstance(exc, sqlite3.Error):
        primary = _primary(exc)
        text = str(exc).lower()
        if primary == _SQLITE_FULL or "database or disk is full" in text:
            return full
        if primary == _SQLITE_READONLY or "readonly database" in text or (
                primary == _SQLITE_CANTOPEN and data_dir is not None and not _writable(data_dir)):
            return readonly
        if primary in (_SQLITE_BUSY, _SQLITE_LOCKED) or "database is locked" in text:
            return Busy()
        if primary in (_SQLITE_CORRUPT, _SQLITE_NOTADB):
            return StorageError(CORRUPT, code="db_corrupt", status=500)
        if primary == _SQLITE_IOERR and _low_space(data_dir):
            return full
        return None
    if isinstance(exc, OSError):
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            return full
        if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM) and (
                data_dir is None or _inside(exc.filename, data_dir)):
            return readonly
    return None


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")


def test_fault(name: str) -> bool:
    """Fault injection for tests and screenshots; only via the process environment."""
    return os.environ.get("LEAD_TEST_FAULT", "") == name
