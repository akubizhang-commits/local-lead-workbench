"""Storage retention with first-run confirmation (P1-8, design §10).

Configuration and state live in ``{data}/retention.json`` (never in the database, so a
restored backup cannot flip the confirmation). Only three kinds of files are ever
deleted: Maps raw results under ``runs/``, ``backups/auto-*.sqlite3`` and finished
export files. Database records are never touched: the only database access is one
read-only query for run states.

The first deletion needs an explicit confirmation of the exact list (``plan_id``);
afterwards the worker runs ``run("auto")`` periodically while the policy is not
stricter than the confirmed one (decision D7).
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time

from .errors import READONLY, Conflict, DomainError, StorageError

log = logging.getLogger(__name__)

DEFAULT_POLICY = {"maps_raw_days": 30, "auto_backup_keep": 10, "export_days": 7}
LIMITS = {"maps_raw_days": (7, 365), "auto_backup_keep": (1, 100), "export_days": (1, 90)}
STATES = ("awaiting_confirmation", "active")
LOCK_WAIT_SECONDS = 5.0
HISTORY_KEEP = 20
HISTORY_SHOWN = 5
PREVIEW_LIMIT = 500
TEMP_FILE_DAYS = 1
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 2  # retention.log + .1 + .2 = at most 3 files
YIELD_EVERY = 200
LIVE_EXPORT = {"queued", "running"}
PROTECTED_RUN_STATES = ("queued", "running", "paused")
MAPS_DIR = re.compile(r"^maps-[0-9a-f]{32}$")
EXPORT_FILE = re.compile(r"^([0-9a-f]{32})\.(csv|xlsx)$")
EXPORT_PART = re.compile(r"^([0-9a-f]{32})\.part$")
JOB_FILE = re.compile(r"^([0-9a-f]{32})\.json$")
CANCEL_FILE = re.compile(r"^([0-9a-f]{32})\.cancel$")
BUSY_MESSAGE = "清理正在进行，请稍后再试"
CHANGED_MESSAGE = "待清理文件已变化，请重新查看后确认"


def now():
    return datetime.now(timezone.utc).isoformat()


def default_state():
    return {"version": 1, "policy": dict(DEFAULT_POLICY), "state": "awaiting_confirmation",
            "confirmed_at": None, "confirmed_policy": None, "history": []}


def state_path(data_dir) -> Path:
    return Path(data_dir) / "retention.json"


# ---- state file -----------------------------------------------------------

@contextmanager
def retention_lock(data_dir, wait=None):
    """fcntl lock shared by the API (confirm/policy) and the worker (auto runs)."""
    wait = LOCK_WAIT_SECONDS if wait is None else wait
    path = Path(data_dir) / "retention.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Conflict(BUSY_MESSAGE)
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _valid_policy(policy) -> bool:
    return isinstance(policy, dict) and all(
        isinstance(policy.get(key), int) and not isinstance(policy.get(key), bool) and low <= policy[key] <= high
        for key, (low, high) in LIMITS.items())


def _parse(text):
    data = json.loads(text)
    if not isinstance(data, dict) or not _valid_policy(data.get("policy")) or data.get("state") not in STATES:
        raise ValueError("invalid retention.json")
    if data["state"] == "active" and not _valid_policy(data.get("confirmed_policy")):
        raise ValueError("active without confirmed policy")
    state = default_state()
    state.update({key: data[key] for key in ("policy", "state", "confirmed_at", "confirmed_policy") if key in data})
    state["history"] = [h for h in data.get("history") or [] if isinstance(h, dict)][-HISTORY_KEEP:]
    return state


def peek(data_dir):
    """Lock-free read for cheap callers (overview, export expiry). Corrupt -> defaults."""
    try:
        return _parse(state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_state()


def load(data_dir):
    """Read under retention_lock. A corrupt file is moved aside and confirmation is required again."""
    path = state_path(data_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_state()
    try:
        return _parse(text)
    except ValueError:
        aside = path.with_name(f"retention.json.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")
        try:
            os.replace(path, aside)
        except OSError:
            pass
        log.warning("retention.json was unreadable; confirmation required again",
                    extra={"event": "retention_corrupt"})
        return default_state()


def save(data_dir, state):
    path = state_path(data_dir)
    temporary = path.with_name(f".retention.json.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(temporary, path)


def stricter(policy, confirmed) -> bool:
    """True when any value deletes more than the confirmed policy (fewer days or copies)."""
    if not confirmed:
        return True
    return any(policy[key] < confirmed.get(key, policy[key]) for key in LIMITS)


def export_days(data_dir) -> int:
    return peek(data_dir)["policy"]["export_days"]


# ---- candidates (§10.3) ---------------------------------------------------

def _protected_runs(data_dir):
    """Read-only: ids of queued/running/paused runs. None when the database is busy or unreadable."""
    path = Path(data_dir) / "workbench.sqlite3"
    if not path.exists():
        return set()
    try:
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            con.execute("PRAGMA busy_timeout=2000")
            marks = ",".join("?" * len(PROTECTED_RUN_STATES))
            return {row[0] for row in con.execute(f"SELECT id FROM runs WHERE status IN ({marks})",
                                                  PROTECTED_RUN_STATES)}
        finally:
            con.close()
    except sqlite3.Error:
        log.warning("run states unavailable; Maps raw results skipped this time",
                    extra={"event": "retention_runs_busy"})
        return None


def _guard_run_dirs(data_dir):
    """run_dir of every live Maps guard registry entry (read only, unlike maps_guard.entries)."""
    from .maps_guard import registry_dir
    folder = registry_dir(data_dir)
    result = []
    for path in folder.glob("*.json") if folder.is_dir() else ():
        try:
            value = json.loads(path.read_text()).get("run_dir")
        except (OSError, ValueError, AttributeError):
            continue
        if value:
            result.append(os.path.realpath(value))
    return result


def _is_plain_dir(path) -> bool:
    try:
        return os.lstat(path).st_mode & 0o170000 == 0o040000
    except OSError:
        return False


def _is_plain_file(path) -> bool:
    try:
        return os.lstat(path).st_mode & 0o170000 == 0o100000
    except OSError:
        return False


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _tree_size(path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):  # os.walk does not follow symlinks
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _finished_at(maps_dir: Path):
    try:
        stamp = json.loads((maps_dir / "metadata.json").read_text()).get("finished_at")
        return datetime.fromisoformat(stamp).timestamp()
    except (OSError, ValueError, TypeError, AttributeError):
        return os.lstat(maps_dir).st_mtime


def _item(category, data_dir, path: Path, size, mtime_ns, reason):
    return {"category": category, "path": path.relative_to(data_dir).as_posix(), "size_bytes": size,
            "mtime_ns": mtime_ns, "reason": reason}


def _maps_candidates(data_dir: Path, days, at, should_stop):
    runs = data_dir / "runs"
    if not _is_plain_dir(runs):
        return []
    if should_stop():
        return []
    protected = _protected_runs(data_dir)
    if protected is None:
        return []
    in_use = _guard_run_dirs(data_dir)
    cutoff = at - days * 86400
    result, seen = [], 0
    for run in sorted(os.listdir(runs)):
        run_dir = runs / run
        if run in protected or not _is_plain_dir(run_dir):
            continue
        for item in sorted(os.listdir(run_dir)):
            item_dir = run_dir / item
            if not _is_plain_dir(item_dir):
                continue
            for name in sorted(os.listdir(item_dir)):
                seen += 1
                if seen % YIELD_EVERY == 0:
                    time.sleep(0)  # let API threads take the GIL (R3-5)
                maps_dir = item_dir / name
                if not MAPS_DIR.fullmatch(name) or not _is_plain_dir(maps_dir) or not _inside(maps_dir, runs):
                    continue
                real = os.path.realpath(maps_dir)
                if any(real == used or used.startswith(real + os.sep) or real.startswith(used + os.sep)
                       for used in in_use):
                    continue
                if _finished_at(maps_dir) >= cutoff:
                    continue
                result.append(_item("maps_raw", data_dir, maps_dir, _tree_size(maps_dir),
                                    os.lstat(maps_dir).st_mtime_ns, f"已超过 {days} 天"))
    return result


def _backup_candidates(data_dir: Path, keep):
    folder = data_dir / "backups"
    if not _is_plain_dir(folder):
        return []
    autos = []
    for name in os.listdir(folder):
        path = folder / name
        if name.startswith("auto-") and name.endswith(".sqlite3") and _is_plain_file(path) and _inside(path, folder):
            info = os.lstat(path)
            autos.append((info.st_mtime_ns, name, path, info.st_size))
    autos.sort(reverse=True)
    return [_item("auto_backup", data_dir, path, size, mtime_ns, f"超出最近 {keep} 份")
            for mtime_ns, _name, path, size in autos[keep:]]


def _export_candidates(data_dir: Path, days, at):
    exports = data_dir / "exports"
    jobs = exports / "jobs"
    if not _is_plain_dir(exports):
        return []
    result = []
    states = {}
    if _is_plain_dir(jobs):
        for name in sorted(os.listdir(jobs)):
            match = JOB_FILE.fullmatch(name)
            path = jobs / name
            if not match or not _is_plain_file(path):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            states[match.group(1)] = job
    temp_cutoff = at - TEMP_FILE_DAYS * 86400
    for job_id, job in states.items():
        if job.get("status") in LIVE_EXPORT:
            continue
        try:
            finished = datetime.fromisoformat(job.get("finished_at") or "").timestamp()
        except (TypeError, ValueError):
            continue
        if finished + days * 86400 >= at:
            continue
        reason = f"已超过 {days} 天"
        for path in (exports / f"{job_id}.{job.get('format')}", jobs / f"{job_id}.json"):
            if EXPORT_FILE.fullmatch(path.name) or JOB_FILE.fullmatch(path.name):
                if _is_plain_file(path) and _inside(path, exports):
                    info = os.lstat(path)
                    result.append(_item("export", data_dir, path, info.st_size, info.st_mtime_ns, reason))
    temporary = [(exports / name, EXPORT_PART.fullmatch(name)) for name in os.listdir(exports)]
    if _is_plain_dir(jobs):
        temporary += [(jobs / name, CANCEL_FILE.fullmatch(name)) for name in os.listdir(jobs)]
    for path, match in sorted(temporary, key=lambda pair: str(pair[0])):
        if not match or not _is_plain_file(path):
            continue
        job = states.get(match.group(1))
        if job and job.get("status") in LIVE_EXPORT:
            continue
        info = os.lstat(path)
        if info.st_mtime < temp_cutoff:
            result.append(_item("export", data_dir, path, info.st_size, info.st_mtime_ns,
                                "未完成的临时文件，已超过 1 天"))
    return result


def candidates(data_dir, policy, *, at=None, should_stop=lambda: False):
    data_dir = Path(data_dir)
    at = time.time() if at is None else at
    items = _maps_candidates(data_dir, policy["maps_raw_days"], at, should_stop)
    items += _backup_candidates(data_dir, policy["auto_backup_keep"])
    items += _export_candidates(data_dir, policy["export_days"], at)
    return items


def plan_id(items) -> str:
    rows = sorted((i["category"], i["path"], i["size_bytes"], i["mtime_ns"]) for i in items)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def preview(items):
    shown = [{key: item[key] for key in ("category", "path", "size_bytes", "reason")} for item in items[:PREVIEW_LIMIT]]
    return {"plan_id": plan_id(items), "total_files": len(items),
            "total_bytes": sum(item["size_bytes"] for item in items), "items": shown,
            "truncated": len(items) > PREVIEW_LIMIT}


def usage(data_dir):
    data_dir = Path(data_dir)
    return {f"{name}_bytes": _tree_size(data_dir / name) if _is_plain_dir(data_dir / name) else 0
            for name in ("runs", "backups", "exports", "logs")}


def view(data_dir, state=None):
    """GET /api/retention payload. ``state`` is passed when the caller already holds the lock."""
    if state is None:
        with retention_lock(data_dir):
            state = load(data_dir)
    items = candidates(data_dir, state["policy"])
    return {"policy": state["policy"], "state": state["state"], "confirmed_at": state["confirmed_at"],
            "confirmed_policy": state["confirmed_policy"], "history": state["history"][-HISTORY_SHOWN:][::-1],
            "usage": usage(data_dir), "preview": preview(items)}


def summary(data_dir, *, should_stop=lambda: False):
    """Counts for /api/overview; returns None when abandoned (restore pending)."""
    state = peek(data_dir)
    items = candidates(data_dir, state["policy"], should_stop=should_stop)
    if should_stop():
        return None
    return {"state": state["state"], "pending_files": len(items),
            "pending_bytes": sum(item["size_bytes"] for item in items)}


# ---- deletion -------------------------------------------------------------

ROOTS = {"maps_raw": "runs", "auto_backup": "backups", "export": "exports"}


def _rotate_log(path: Path):
    try:
        if path.stat().st_size <= LOG_MAX_BYTES:
            return
    except FileNotFoundError:
        return
    for index in range(LOG_BACKUPS, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index}"))


def _write_log(data_dir, lines):
    folder = Path(data_dir) / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "retention.log"
        _rotate_log(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("could not write retention.log", exc_info=True, extra={"event": "retention_log_failed"})


def delete(data_dir, items):
    data_dir = Path(data_dir)
    deleted, freed, errors, lines = 0, 0, [], []
    for item in items:
        path = data_dir / item["path"]
        root = data_dir / ROOTS[item["category"]]
        problems = []
        try:
            if os.path.islink(path) or not _inside(path, root):
                raise OSError(errno.EPERM, "outside retention scope")
            if item["category"] == "maps_raw":
                if not _is_plain_dir(path):
                    raise FileNotFoundError(errno.ENOENT, "missing")
                shutil.rmtree(path, onexc=lambda _f, p, e: problems.append((p, e)))
                if problems:
                    raise problems[0][1]
                for parent in (path.parent, path.parent.parent):  # empty <item_id>/ and <run_id>/
                    try:
                        parent.rmdir()
                    except OSError:
                        break
            else:
                os.unlink(path)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            errors.append({"path": item["path"], "reason": reason})
            lines.append({"ts": now(), "action": "error", **_public(item), "error": reason})
            continue
        deleted += 1
        freed += item["size_bytes"]
        lines.append({"ts": now(), "action": "deleted", **_public(item)})
    if errors:
        log.warning("%d files could not be deleted", len(errors), extra={"event": "retention_errors"})
    return {"deleted": deleted, "freed_bytes": freed, "errors": errors}, lines


def _public(item):
    return {key: item[key] for key in ("category", "path", "size_bytes", "reason")}


def _require_writable(data_dir):
    if not os.access(data_dir, os.W_OK):
        raise StorageError(READONLY.format(data_dir=data_dir), code="readonly_storage", status=503)


def run(data_dir, trigger, *, expected_plan=None):
    """Delete the current candidates. ``confirm`` checks the plan and activates the policy;
    ``auto`` only runs while active and not stricter than the confirmed policy."""
    data_dir = Path(data_dir)
    if trigger == "auto" and not os.access(data_dir, os.W_OK):
        log.warning("data directory read-only; retention skipped", extra={"event": "retention_skipped"})
        return None
    if trigger == "confirm":
        _require_writable(data_dir)
    with retention_lock(data_dir):
        state = load(data_dir)
        if trigger == "auto" and (state["state"] != "active" or stricter(state["policy"], state["confirmed_policy"])):
            return None
        items = candidates(data_dir, state["policy"])
        if trigger == "confirm" and plan_id(items) != expected_plan:
            raise DomainError(CHANGED_MESSAGE, code="retention_plan_changed", status=409,
                              preview=preview(items))
        result, lines = delete(data_dir, items)
        stamp = now()
        if trigger == "confirm":
            state.update(state="active", confirmed_at=stamp, confirmed_policy=dict(state["policy"]))
        entry = {"at": stamp, "trigger": trigger, "deleted": result["deleted"], "freed_bytes": result["freed_bytes"],
                 "errors": len(result["errors"])}
        state["history"] = [*state["history"], entry][-HISTORY_KEEP:]
        _write_log(data_dir, [*lines, {"ts": stamp, "action": "summary", **entry}])
        save(data_dir, state)
        log.info("retention run", extra={"event": "retention_run", "trigger": trigger, "deleted": result["deleted"],
                                         "freed_bytes": result["freed_bytes"], "errors": len(result["errors"])})
        return {**result, "state": state}


def set_policy(data_dir, policy):
    """D7: a stricter policy needs a new confirmation; a looser one stays active."""
    _require_writable(data_dir)
    with retention_lock(data_dir):
        state = load(data_dir)
        state["policy"] = {key: policy[key] for key in LIMITS}
        if state["state"] == "active":
            if stricter(state["policy"], state["confirmed_policy"]):
                state["state"] = "awaiting_confirmation"
            else:
                state["confirmed_policy"] = dict(state["policy"])
        save(data_dir, state)
        return view(data_dir, state)


# ---- overview cache (§10.6, R3-5) ------------------------------------------

class SummaryCache:
    """Last computed summary for /api/overview; never computed on the request thread."""
    TTL_SECONDS = 600

    def __init__(self, data_dir, gate=None):
        self.data_dir = Path(data_dir)
        self.gate = gate
        self.value = None
        self.computed_at = 0.0
        self.stale = True
        self._running = threading.Lock()
        self.thread = None

    def invalidate(self):
        self.stale = True

    def _blocked(self) -> bool:
        return bool(self.gate is not None and self.gate.exclusive_pending)

    def get(self):
        if self.stale or time.monotonic() - self.computed_at > self.TTL_SECONDS:
            self.refresh()
        if self.value is not None:
            return dict(self.value)
        return {"state": peek(self.data_dir)["state"], "pending_files": None, "pending_bytes": None}

    def refresh(self):
        if self._blocked() or not self._running.acquire(blocking=False):
            return False
        self.thread = threading.Thread(target=self._compute, name="retention-summary", daemon=True)
        self.thread.start()
        return True

    def _compute(self):
        try:
            if self._blocked():
                return
            self.stale = False
            value = summary(self.data_dir, should_stop=self._blocked)
            if value is None:
                self.stale = True
                return
            self.value = value
            self.computed_at = time.monotonic()
        except Exception:
            self.stale = True
            log.warning("retention summary failed", exc_info=True, extra={"event": "retention_summary_failed"})
        finally:
            self._running.release()
