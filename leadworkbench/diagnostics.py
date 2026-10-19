"""Redacted diagnostic bundle (P1-7, design §6.3–6.4).

``build(data_dir, store, settings=...)`` returns zip bytes built in memory from a fixed
whitelist. Business data (names, contacts, URLs), tokens, keys and login state never
leave the machine: log lines get rules 1–8 of §6.3, JSON files rules 1–4 and 6.
A failing part becomes ``{"error": "<code>"}`` and the rest is still written.
"""
from __future__ import annotations
from contextlib import ExitStack
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import sys
import time
from urllib.parse import urlsplit
import zipfile

from . import logging_setup
from .errors import classify_storage_error

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
LOG_BUDGET = 5_000_000
NAME_BUDGET_SECONDS = 10.0
PROCESSES = ("api", "worker", "export")
NOTE = "不含企业、联系人数据，不含令牌、密钥和登录信息"
SKIPPED_NOTE = "数据库不可用，未做名称脱敏；日志中已去掉异常消息"
PARTIAL_NOTE = "名称脱敏超过 10 秒，之后只扫描 WARNING 及以上的日志行"
UNSTRUCTURED = "<非结构化日志已省略>"
ENV_FLAGS = ("MAPS_SCRAPER_BIN", "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_DRIVER_PATH", "LEAD_PORT", "LEAD_LOG_LEVEL")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"https?://\S+")
LOCAL_URL = re.compile(r"(?i)^(https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)")
PHONE = re.compile(r"[+(\d][\d+().\- ]*[\d)]")
FRAME = re.compile(r'^\s*File ".*", line \d+, in \S+$')
EXC_TYPE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")
CHAIN = ("During handling of the above exception, another exception occurred:",
         "The above exception was the direct cause of the following exception:")


# ---- redaction (§6.3) -------------------------------------------------------

class Redactor:
    def __init__(self, literals=(), names=None):
        self.literals = sorted({value for value in literals if value and len(value) >= 4}, key=len, reverse=True)
        self.home = str(Path.home())
        self.index = None
        if names is not None:
            self.index = {}
            for name in sorted({n.lower() for n in names if n and len(n) >= 4}, key=len, reverse=True):
                self.index.setdefault(name[:4], []).append(name)

    def text(self, value: str, *, phone=False) -> str:
        """Rules 1–4 and 6 (+ rule 5 for free-text log fields)."""
        if not value:
            return value
        for literal in self.literals:
            value = value.replace(literal, "<已隐藏>")
        value = logging_setup.mask_secrets(value)
        value = EMAIL.sub("<email>", value)
        value = URL.sub(_url, value)
        if phone:
            value = PHONE.sub(_phone, value)
        return value.replace(self.home, "~")

    def names(self, value: str) -> str:
        """Rule 8: exact, case-insensitive replacement of known company/contact names."""
        if not self.index or not value:
            return value
        lowered = value.lower()
        if len(lowered) != len(value):
            lowered = value
        out, start, i, end = [], 0, 0, len(value) - 3
        while i < end:
            candidates = self.index.get(lowered[i:i + 4])
            if candidates:
                for name in candidates:
                    if lowered.startswith(name, i):
                        out.append(value[start:i])
                        out.append("<name>")
                        i += len(name)
                        start = i
                        break
                else:
                    i += 1
                continue
            i += 1
        out.append(value[start:])
        return "".join(out)

    def value(self, value):
        """Recursively apply rules 1–4 and 6 to every string of a JSON value."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {k: ("<已设置>" if v else "")
                    if _secret_key(k) and isinstance(v, str) and v not in ("已设置", "未设置") else self.value(v)
                    for k, v in value.items()}
        return value


def _secret_key(key) -> bool:
    return bool(re.search(logging_setup.SECRET_KEYS, str(key)))


def _url(match):
    local = LOCAL_URL.match(match.group(0))
    return local.group(1) if local else "<url>"


def _phone(match):
    text = match.group(0)
    return "<phone>" if sum(c.isdigit() for c in text) >= 7 else text


def exception_structure(text: str, home: str) -> str:
    """Rule 7: keep frame lines and the exception type chain, drop source and messages."""
    out, state = [], "start"
    for line in (text or "").splitlines():
        if line.startswith("Traceback (most recent call last)"):
            out.append(line)
            state = "frames"
        elif line.strip() in CHAIN:
            out.append(line.strip())
            state = "start"
        elif state == "frames" and line[:1].isspace():
            if FRAME.match(line):
                out.append(line.replace(home, "~"))
        elif state in ("frames", "start") and line.strip():
            match = EXC_TYPE.match(line)
            out.append(match.group(1) if match else "<exception>")
            state = "message"
    return "\n".join(out)


def error_type(value):
    match = EXC_TYPE.match(str(value or ""))
    return match.group(1) if match else "<error>"


# ---- logs -------------------------------------------------------------------

def _log_files(data_dir, process):
    """Current file first, then .1, .2 … (newest to oldest)."""
    base = Path(data_dir) / "logs" / f"{process}.log"
    files = [base] if base.is_file() else []
    index = 1
    while (candidate := base.with_name(f"{process}.log.{index}")).is_file():
        files.append(candidate)
        index += 1
    return files


def _shares(sizes, budget):
    """Water-filling: each process gets an equal share, unused bytes go to the others."""
    shares = {key: 0 for key in sizes}
    remaining = dict(sizes)
    while remaining and budget > 0:
        share = budget // len(remaining)
        if share == 0:
            break
        for key in list(remaining):
            take = min(share, remaining[key])
            shares[key] += take
            budget -= take
            remaining[key] -= take
            if remaining[key] == 0:
                del remaining[key]
    return shares


def _newest_lines(files, limit):
    """The newest whole lines of a rotation set within ``limit`` bytes, oldest first."""
    chunks, used = [], 0
    for path in files:
        data = path.read_bytes()
        if used + len(data) > limit:
            data = data[len(data) - (limit - used):]
            newline = data.find(b"\n")
            data = data[newline + 1:] if newline >= 0 else b""
            chunks.append(data)
            break
        chunks.append(data)
        used += len(data)
    return b"".join(reversed(chunks)).decode("utf-8", "replace").splitlines()


def redact_line(line, redactor, *, scan_names=True):
    try:
        record = json.loads(line)
    except ValueError:
        return UNSTRUCTURED
    if not isinstance(record, dict):
        return UNSTRUCTURED
    for key, value in list(record.items()):
        if not isinstance(value, str):
            continue
        if key == "exc":
            record[key] = exception_structure(redactor.text(value), redactor.home)
        elif key == "error":
            record[key] = error_type(value)
        elif key == "msg":
            msg = redactor.text(value, phone=True)
            record[key] = redactor.names(msg) if scan_names else msg
        else:
            record[key] = redactor.text(value)
    return json.dumps(record, ensure_ascii=False)


def collect_logs(data_dir, redactor, names_state):
    """Returns {zip name: text}; ``names_state`` is updated to "partial" past the time budget."""
    sets = {process: _log_files(data_dir, process) for process in PROCESSES}
    sizes = {process: sum(p.stat().st_size for p in files) for process, files in sets.items() if files}
    shares = _shares(sizes, LOG_BUDGET)
    started = time.monotonic()
    result = {}
    for process, files in sets.items():
        if not files:
            continue
        lines = []
        for line in _newest_lines(files, shares[process]):
            scan = redactor.index is not None
            if scan and time.monotonic() - started > NAME_BUDGET_SECONDS:
                names_state["status"] = "partial"
                try:
                    scan = json.loads(line).get("level") in ("WARNING", "ERROR", "CRITICAL")
                except (ValueError, AttributeError):
                    scan = False
            lines.append(redact_line(line, redactor, scan_names=scan))
        result[f"logs/{process}.log"] = "\n".join(lines) + ("\n" if lines else "")
    return result


# ---- sections ---------------------------------------------------------------

def _dir_stats(path: Path):
    files = size = 0
    if path.is_dir():
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    info = os.lstat(os.path.join(root, name))
                except OSError:
                    continue
                files += 1
                size += info.st_size
    return {"files": files, "bytes": size}


def _git_commit():
    try:
        dot_git = ROOT / ".git"
        git_dir = dot_git
        if dot_git.is_file():  # worktree: "gitdir: <path>"
            git_dir = Path(dot_git.read_text().split(":", 1)[1].strip())
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        common = git_dir
        if (git_dir / "commondir").is_file():
            common = (git_dir / (git_dir / "commondir").read_text().strip()).resolve()
        for base in (git_dir, common):
            if (base / ref).is_file():
                return (base / ref).read_text().strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except (OSError, IndexError, ValueError):
        pass
    return "unknown"


def _environment(data_dir: Path, con):
    directory = Path(data_dir)
    result = {"disk_free_bytes": shutil.disk_usage(directory).free,
              "directories": {name: _dir_stats(directory / name) for name in ("runs", "backups", "exports", "logs")}}
    for name in ("workbench.sqlite3", "workbench.sqlite3-wal"):
        path = directory / name
        result.setdefault("database", {})[name] = path.stat().st_size if path.exists() else 0
    result["export_jobs"] = len(list((directory / "exports").glob("*.json"))) if (directory / "exports").is_dir() else 0
    if con is None:
        result["database_counts"] = {"error": "storage_unavailable"}
        return result
    result["user_version"] = con.execute("PRAGMA user_version").fetchone()[0]
    try:
        result["ledger"] = [dict(id=r[0], applied_at=r[1]) for r in
                            con.execute("SELECT id, applied_at FROM schema_migrations ORDER BY rowid")]
    except sqlite3.OperationalError:
        result["ledger"] = []
    result["counts"] = {
        "companies": con.execute("SELECT count(*) FROM companies WHERE merged_into IS NULL").fetchone()[0],
        "contacts": con.execute("SELECT count(*) FROM contacts").fetchone()[0],
        "pending_reviews": con.execute("SELECT count(*) FROM reviews WHERE status='pending'").fetchone()[0],
        "runs_by_status": dict(con.execute("SELECT COALESCE(status,''), count(*) FROM runs GROUP BY 1").fetchall()),
        "work_items_by_state": dict(con.execute(
            "SELECT COALESCE(state,''), count(*) FROM work_items GROUP BY 1").fetchall()),
    }
    return result


def _origin(url):
    """SearXNG address: scheme://host:port only."""
    try:
        parts = urlsplit(url or "")
        if not parts.scheme or not parts.hostname:
            return ""
        return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return ""


def _config(data_dir: Path, store, settings):
    from .social_connections import load_connections
    connections = load_connections(data_dir)
    social = {}
    if store is not None:
        social = store.social_settings()
    secrets_path = Path(data_dir) / "social-secrets.json"
    secrets = json.loads(secrets_path.read_text()) if secrets_path.exists() else {}
    return {
        "settings": settings,
        "searxng": _origin(social.get("searxng_url", "")),
        "social_connections": {
            "reader": connections.get("reader"), "instagram_reader": connections.get("instagram_reader"),
            "max_posts": connections.get("max_posts"),
            "proxy_url": "已设置" if connections.get("proxy_url") else "未设置",
            "youtube_api_key": "已设置" if secrets.get("youtube_api_key") else "未设置",
        },
        "environment": {name: name in os.environ for name in ENV_FLAGS},
    }


def _worker(data_dir: Path, con):
    from . import maps_guard
    from .worker import worker_health
    result = {"health": worker_health(data_dir)}
    if con is not None:
        from .maps_policy import status
        policy = status(con)
        result["maps_policy"] = {key: policy.get(key) for key in
                                 ("allowed", "manual_required", "phase", "reason", "next_attempt_at", "failures")}
    result["maps_guards"] = [
        {"pid": entry.get("guard_pid"), "pgid": entry.get("pgid"), "started_at": entry.get("started_at"),
         "run_dir": "/".join(Path(str(entry.get("run_dir", ""))).parts[-2:])}
        for entry in maps_guard.entries(data_dir)]
    return result


def _runs(con):
    if con is None:
        return {"error": "storage_unavailable"}
    runs = []
    for row in con.execute("SELECT id, kind, status, created_at, finished_at, progress FROM runs "
                           "ORDER BY created_at DESC, rowid DESC LIMIT 20").fetchall():
        try:
            progress = json.loads(row[5] or "{}")
        except ValueError:
            progress = {}
        numbers = {k: v for k, v in progress.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} \
            if isinstance(progress, dict) else {}
        items = [dict(stage=r[0], state=r[1], stop_reason=r[2], error_type=r[3], count=r[4]) for r in con.execute(
            """SELECT stage, state, json_extract(result,'$.stop_reason'), json_extract(result,'$.error_type'), count(*)
               FROM work_items WHERE run_id=? AND json_valid(COALESCE(result,'{}'))
               GROUP BY 1, 2, 3, 4""", (row[0],))]
        runs.append(dict(id=row[0], kind=row[1], status=row[2], created_at=row[3], finished_at=row[4],
                         progress=numbers, work_items=items))
    return runs


def _names(con):
    return [row[0] for row in con.execute(
        "SELECT DISTINCT name FROM companies WHERE length(name)>=4 "
        "UNION SELECT DISTINCT name FROM contacts WHERE length(name)>=4")]


def _secret_literals(data_dir: Path, token):
    literals = [token] if token else []
    try:
        path = Path(data_dir) / "social-secrets.json"
        if path.exists():
            literals += [v for v in json.loads(path.read_text()).values() if isinstance(v, str)]
        path = Path(data_dir) / "social-connections.json"
        if path.exists():
            literals.append(json.loads(path.read_text()).get("proxy_url") or "")
    except (OSError, ValueError, AttributeError):
        pass
    return literals


def _part(builder, data_dir):
    try:
        return builder()
    except Exception as exc:  # one failing part must not lose the rest (§6.4)
        classified = classify_storage_error(exc, data_dir)
        return {"error": classified.code if classified is not None else type(exc).__name__}


def build(data_dir, store=None, *, settings=None, token=None, extra_config=None) -> bytes:
    data_dir = Path(data_dir)
    names_state = {"status": "skipped"}
    with ExitStack() as stack:
        con = names = None
        if store is not None:
            try:
                con = stack.enter_context(store.connect())
                names = _names(con)
                names_state["status"] = "done"
            except (sqlite3.Error, OSError):
                con = names = None
        redactor = Redactor(_secret_literals(data_dir, token), names)
        files = {
            "environment.json": _part(lambda: _environment(data_dir, con), data_dir),
            "config.json": _part(lambda: {**_config(data_dir, store, settings), **(extra_config or {})}, data_dir),
            "worker.json": _part(lambda: _worker(data_dir, con), data_dir),
            "runs.json": _part(lambda: _runs(con), data_dir),
        }
    files = {name: json.dumps(redactor.value(payload), ensure_ascii=False, indent=2)
             for name, payload in files.items()}
    try:
        files.update(collect_logs(data_dir, redactor, names_state))
    except OSError as exc:
        classified = classify_storage_error(exc, data_dir)
        files["logs/error.json"] = json.dumps({"error": classified.code if classified else type(exc).__name__})
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": VERSION, "git_commit": _git_commit(),
        "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
        "files": ["manifest.json", *files], "note": NOTE, "name_redaction": names_state["status"],
    }
    if names_state["status"] == "skipped":
        manifest["name_redaction_note"] = SKIPPED_NOTE
    elif names_state["status"] == "partial":
        manifest["name_redaction_note"] = PARTIAL_NOTE
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(redactor.value(manifest), ensure_ascii=False, indent=2))
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()
