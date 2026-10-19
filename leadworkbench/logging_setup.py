"""JSON line logs per process with size rotation (P1-7, design §6.1–6.3).

``configure("api" | "worker" | "export", data_dir)`` writes ``{data}/logs/<process>.log``
(5 MB × 5 files). Every line is one JSON object with ts, level, logger, process,
pid, event and msg; request_id / run_id / item_id / stage / job_id come from
context variables. Tokens and secrets are masked when written (rules 1–2);
names, URLs and exception messages are only removed when a diagnostic bundle is
built (diagnostics.py), so local logs stay useful for troubleshooting.
"""
from __future__ import annotations
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import sys

from .errors import REQUEST_ID

RUN_ID: ContextVar[str] = ContextVar("run_id", default="")
ITEM_ID: ContextVar[str] = ContextVar("item_id", default="")
STAGE: ContextVar[str] = ContextVar("stage", default="")
JOB_ID: ContextVar[str] = ContextVar("job_id", default="")
CONTEXT = {"request_id": REQUEST_ID, "run_id": RUN_ID, "item_id": ITEM_ID, "stage": STAGE, "job_id": JOB_ID}
OPTIONAL = ("duration_ms", "status", "code", "method", "route", "error_type", "query_keys", "id", "kind",
            "action", "pgid", "pid_target", "trigger", "deleted", "freed_bytes", "errors", "phase", "processed",
            "total", "size_bytes", "format", "layout", "query_len", "query_sha8", "guard_pid", "where", "state",
            "stop_reason", "records", "records_count", "returncode", "members", "backoff_s", "sqlite_code",
            "errno", "lane")
MAX_BYTES = 5_000_000
BACKUPS = 5
MARK = "_leadworkbench"
SECRET_KEYS = r"(?i)(token|secret|password|passwd|api[_-]?key|cookie|authorization|proxy_url|session)"
HEADER = re.compile(r"(?i)\b(X-Local-Token|Authorization|Cookie)(\s*[:=]\s*)(\S+)")
JSON_SECRET = re.compile(r'("[^"]*' + SECRET_KEYS[4:] + r'[^"]*"\s*:\s*)"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_TOKENS: set[str] = set()


def hide_token(value: str):
    """Register the current local token: its literal text is written as <token>."""
    if value:
        _TOKENS.add(value)


def mask_secrets(text: str) -> str:
    """Rules 1–2 of design §6.3."""
    if not text:
        return text
    for token in _TOKENS:
        text = text.replace(token, "<token>")
    text = HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}<已隐藏>", text)
    return JSON_SECRET.sub(lambda m: m.group(1) + ('"<已设置>"' if m.group(3) else '""'), text)


class JsonFormatter(logging.Formatter):
    def __init__(self, process):
        super().__init__()
        self.process = process

    def format(self, record):
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "process": self.process, "pid": record.process,
            "event": getattr(record, "event", None) or "log", "msg": mask_secrets(record.getMessage()),
        }
        for key, var in CONTEXT.items():
            value = getattr(record, key, None) or var.get()
            if value:
                payload[key] = value
        for key in OPTIONAL:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = mask_secrets(self.formatException(record.exc_info))
            payload.setdefault("error_type", record.exc_info[0].__name__ if record.exc_info[0] else None)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    """Rotated and newly opened log files stay 0600."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


class _TextFormatter(logging.Formatter):
    """The console keeps the plain text format, with the same secret masking."""

    def format(self, record):
        return mask_secrets(super().format(record))


class _Console(logging.StreamHandler):
    """Writes to the current sys.stderr (test runners swap it out between tests)."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def _ours(handler):
    return getattr(handler, MARK, False)


def configure(process, data_dir, *, level=None):
    """Idempotent: replaces only handlers installed by an earlier configure()."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if _ours(h)]:
        root.removeHandler(handler)
        handler.close()
    level = level or os.environ.get("LEAD_LOG_LEVEL", "INFO").upper()
    root.setLevel(level)
    console = _Console()
    console.setFormatter(_TextFormatter("%(asctime)s %(levelname)s %(message)s"))
    console.setLevel(logging.WARNING if process == "api" else level)
    setattr(console, MARK, True)
    root.addHandler(console)
    if data_dir is None:
        return None
    path = Path(data_dir) / "logs" / f"{process}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        handler = _PrivateRotatingHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("logs directory not writable, console only: %s", exc,
                                            extra={"event": "log_fallback"})
        return None
    handler.setFormatter(JsonFormatter(process))
    handler.setLevel(level)
    setattr(handler, MARK, True)
    root.addHandler(handler)
    return path
