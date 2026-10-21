"""Maps scraper guard: the scraper's lifetime follows the worker (P1-4, design §9.3).

Run as ``python -m leadworkbench.maps_guard --registry-dir D --run-dir R -- <command>``
by sources/maps.py. The guard

- holds ``{data}/run/maps.lock`` (hard guarantee: one Maps query at a time); busy -> exit 75;
- writes ``<registry-dir>/<guard_pid>.json`` before starting the scraper (failure -> exit 74);
- starts the scraper in its own process group (pgid = guard pid), passing the lock fd;
- treats EOF on stdin (the worker died) as "stop": SIGTERM the group, SIGKILL after 2 s;
- marks SIGTERM, ignores SIGINT, and exits with the scraper's code (signal N -> 128+N);
- never writes to stdout/stderr (the adapter parses those logs).

``busy()`` and ``reclaim()`` are used by the worker (startup and the maps lane).
"""
from __future__ import annotations
from datetime import datetime, timezone
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

BUSY_EXIT = 75
REGISTRY_EXIT = 74
LAUNCH_EXIT = 127
EOF_GRACE_SECONDS = 2.0
RECLAIM_TERM_SECONDS = 3.0
ORPHAN_TERMS = ("google-maps-scraper", "chrome-headless-shell")
MAPS_ORPHAN = {"code": "maps_orphan",
               "message": "上次的地图采集进程无法自动结束，地图检索已暂停。请重启电脑，或手动结束 google-maps-scraper 进程"}


MAPS_ORPHAN_SERVER = ("上次的地图采集进程无法自动结束，地图检索已暂停。"
                      "请让管理员重启后台服务（docker compose restart worker）")
SERVER_MODE = False  # set by the worker in server mode (AWS trial §3.6)


def maps_orphan_problem(server=False):
    problem = dict(MAPS_ORPHAN)
    if server:
        problem["message"] = MAPS_ORPHAN_SERVER
    return problem


def run_dir(data_dir) -> Path:
    return Path(data_dir) / "run"


def registry_dir(data_dir) -> Path:
    return run_dir(data_dir) / "maps"


def lock_file(data_dir) -> Path:
    return run_dir(data_dir) / "maps.lock"


def _open_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def _try_lock(fd) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _write_json(path: Path, payload):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)


# ---------------------------------------------------------------- guard process

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="maps_guard")
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    terminating = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: terminating.set())
    registry = Path(args.registry_dir)
    work = Path(args.run_dir)
    try:
        registry.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = _open_lock(registry.parent / "maps.lock")
    except OSError:
        return REGISTRY_EXIT
    if not _try_lock(lock_fd):
        try:
            _write_json(work / "guard.json", {"status": "busy"})
        except OSError:
            pass
        return BUSY_EXIT
    entry = registry / f"{os.getpid()}.json"
    try:
        _write_json(entry, dict(guard_pid=os.getpid(), pgid=os.getpgrp(), worker_pid=os.getppid(),
                                started_at=datetime.now(timezone.utc).isoformat(), run_dir=str(work),
                                binary=command[0] if command else ""))
    except OSError:
        return REGISTRY_EXIT
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, pass_fds=(lock_fd,))
    except (OSError, ValueError, IndexError):
        entry.unlink(missing_ok=True)
        return LAUNCH_EXIT

    def watch_stdin():
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        if child.poll() is not None:
            return
        # The worker is gone: end the whole group, then ourselves.
        entry.unlink(missing_ok=True)
        try:
            os.killpg(os.getpgrp(), signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + EOF_GRACE_SECONDS
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch_stdin, daemon=True).start()
    code = child.wait()
    entry.unlink(missing_ok=True)
    return code if code >= 0 else 128 - code


# ---------------------------------------------------------------- worker side

def busy(data_dir) -> bool:
    """True while some process holds maps.lock (a query or its leftovers is alive)."""
    path = lock_file(data_dir)
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if _try_lock(fd):
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        return True
    finally:
        os.close(fd)


def entries(data_dir):
    result = []
    folder = registry_dir(data_dir)
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            payload = json.loads(path.read_text())
            payload["_path"] = path
            result.append(payload)
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    return result


def _ps():
    """{pid: (pgid, command)} or None when ps is unavailable."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,command="], capture_output=True, text=True,
                             timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def _group_alive(pgid) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _ours(entry, table) -> bool:
    binary = entry.get("binary") or ""
    members = [command for pid, (pgid, command) in table.items() if pgid == entry.get("pgid")]
    return any("leadworkbench.maps_guard" in command or (binary and binary in command) for command in members)


def _end_group(pgid):
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + RECLAIM_TERM_SECONDS
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _lsof_holders(path):
    if not shutil.which("lsof"):
        return []
    try:
        out = subprocess.run(["lsof", "-t", str(path)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(pid) for pid in out.split() if pid.isdigit()]


def reclaim(data_dir, timeout=10, *, protect_pids=frozenset()):
    """End Maps processes left by an earlier worker (design §9.3 steps 1–4).

    Returns {"action": ..., "problem": None | MAPS_ORPHAN}. Registry entries whose
    guard is in ``protect_pids`` (this worker's live queries) are never touched."""
    path = lock_file(data_dir)
    fd = _open_lock(path)
    try:
        if _try_lock(fd):
            for entry in entries(data_dir):
                if entry.get("guard_pid") not in protect_pids:
                    entry["_path"].unlink(missing_ok=True)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return {"action": "clean", "problem": None}
        table = _ps()
        protected_alive = False
        for entry in entries(data_dir):
            pgid = entry.get("pgid")
            if entry.get("guard_pid") in protect_pids:
                protected_alive = protected_alive or (isinstance(pgid, int) and _group_alive(pgid))
                continue
            if not isinstance(pgid, int) or not _group_alive(pgid):
                entry["_path"].unlink(missing_ok=True)
                continue
            if table is None:
                continue  # no identity check possible: never kill
            if _ours(entry, table):
                _end_group(pgid)
                entry["_path"].unlink(missing_ok=True)
                log.warning("reclaimed Maps process group %s", pgid,
                            extra={"event": "maps_reclaim", "action": "killed", "pgid": pgid})
            else:
                entry["_path"].unlink(missing_ok=True)
                log.warning("Maps registry entry %s does not match a Maps process", pgid,
                            extra={"event": "maps_reclaim", "action": "unverified", "pgid": pgid})
        if protected_alive:
            return {"action": "protected", "problem": None}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _try_lock(fd):
                fcntl.flock(fd, fcntl.LOCK_UN)
                return {"action": "reclaimed", "problem": None}
            time.sleep(0.1)
        if table is not None:
            browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
            for pid in _lsof_holders(path):
                command = table.get(pid, (0, ""))[1]
                if pid != os.getpid() and (any(term in command for term in ORPHAN_TERMS)
                                           or (browsers and browsers in command)):
                    try:
                        os.kill(pid, signal.SIGKILL)
                        log.warning("ended orphan Maps lock holder %s", pid,
                                    extra={"event": "maps_reclaim", "action": "lsof_killed", "pid": pid})
                    except ProcessLookupError:
                        pass
            time.sleep(0.2)
            if _try_lock(fd):
                fcntl.flock(fd, fcntl.LOCK_UN)
                return {"action": "reclaimed", "problem": None}
        log.error("Maps lock still held after reclaim", extra={"event": "maps_reclaim", "action": "orphan"})
        return {"action": "orphan", "problem": maps_orphan_problem(SERVER_MODE)}
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
