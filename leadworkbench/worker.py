"""Single local worker. A process lock protects recovery and database restoration."""
import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import fcntl
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import json
import time
from datetime import datetime, timezone

from .db import Store, now
from .sources.website import enrich_website

_HEALTH_LOCK = threading.Lock()


def worker_health(directory):
    # Two API threads probing worker-single.lock at once would see each other's
    # flock and report a dead worker as alive; probe one at a time (P1-1).
    with _HEALTH_LOCK:
        return _worker_health(directory)


def _worker_health(directory):
    try:
        payload = json.loads((Path(directory) / "worker-heartbeat.json").read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(payload["last_seen"])).total_seconds()
        # A fresh file from a dead worker must not claim the worker is alive.
        with worker_lock(directory, "worker-single.lock"):
            locked = False
    except RuntimeError:
        locked = True
    except (OSError, ValueError, KeyError, TypeError):
        return {"alive": False, "last_seen": None}
    result = {"alive": locked and 0 <= age < 30, "last_seen": payload["last_seen"]}
    if result["alive"] and isinstance(payload.get("problem"), dict):
        result["problem"] = payload["problem"]
    return result


log = logging.getLogger(__name__)

# Current worker problems shown in the heartbeat ({"code","message"} or absent).
# The Maps slot (orphaned scraper) is separate: database recovery must not clear it.
_PROBLEMS: dict[str, dict] = {}
_MAPS_PROBLEMS: dict[str, dict] = {}


def set_problem(directory, problem=None, *, slot=None):
    problems = _MAPS_PROBLEMS if slot == "maps" else _PROBLEMS
    key = str(Path(directory))
    if problem:
        problems[key] = problem
    else:
        problems.pop(key, None)


def heartbeat_payload(directory):
    payload = {"pid": os.getpid(), "last_seen": now()}
    key = str(Path(directory))
    problem = _PROBLEMS.get(key) or _MAPS_PROBLEMS.get(key)
    if problem:
        payload["problem"] = problem
    return payload


async def heartbeat(directory):
    path = Path(directory) / "worker-heartbeat.json"
    while True:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(heartbeat_payload(directory), ensure_ascii=False))
        temporary.replace(path)
        await asyncio.sleep(5)


LOCKED_MESSAGE = "worker 正在运行，需先停止后台进程才能恢复备份"


class LockTimeout(RuntimeError):
    """worker_lock 等待超时。是 RuntimeError 的子类，现有 pytest.raises(RuntimeError, match=...) 照样通过。"""


@contextmanager
def worker_lock(directory, filename="worker.lock", *, wait_seconds=0.0, message=None):
    """Exclusive file lock, retried every 0.1 s for up to `wait_seconds`.

    Waiting uses time.sleep: call with wait_seconds > 0 only from synchronous
    code (API thread pool or asyncio.to_thread), never on the event loop."""
    file = Path(directory) / filename
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a+") as handle:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise LockTimeout(message or LOCKED_MESSAGE) from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class OperationLock:
    """Non-blocking holder of worker.lock; released while the worker is idle."""

    def __init__(self, directory, filename="worker.lock"):
        file = Path(directory) / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        self._handle = file.open("a+")
        self.held = False

    def try_acquire(self) -> bool:
        if self.held:
            return True
        try:
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.held = True
        return True

    def release(self):
        if self.held:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self.held = False

    def close(self):
        if self._handle.closed:
            return
        self.release()
        self._handle.close()


async def process_run(store, run_id, *, discover=None, enrich=None, planner=None):
    """Compatibility batch API: drain one run until complete or interrupted."""
    while await process_run_item(store, run_id, discover=discover, enrich=enrich, planner=planner):
        pass


async def process_run_item(store, run_id, *, discover=None, enrich=None, planner=None, stages=None):
    """Execute one work item, resolving worker source overrides at call time."""
    from .work_item_executor import execute_run_item

    options = {"stages": stages} if stages is not None else {}
    return await execute_run_item(
        store, run_id, discover=discover,
        enrich=enrich_website if enrich is None else enrich, planner=planner, **options)


STORE_RETRY_SECONDS = 120
STORE_BACKOFF = (0.5, 1, 2, 4, 5)
LOOP_BACKOFF = (1, 2, 5, 10, 30)
LOOP_MAX_FAILURES = 20
# Retention (P1-8): first run 60 s after start, then every 6 hours, only while worker.lock is held.
RETENTION_FIRST_DELAY = 60
RETENTION_INTERVAL = 6 * 3600


def run_retention(directory):
    from . import retention
    try:
        return retention.run(directory, "auto")
    except Exception:  # never let clean-up stop the worker
        log.warning("retention run failed", exc_info=True, extra={"event": "retention_failed"})
        return None
STARTING = {"code": "starting", "message": "后台采集等待数据库初始化…"}


def _enter_lock(directory, filename, wait_seconds):
    """Acquire a worker lock in a worker thread; the returned stack releases it."""
    stack = ExitStack()
    stack.enter_context(worker_lock(directory, filename, wait_seconds=wait_seconds))
    return stack


PROBLEM_MESSAGES = {
    "disk_full": "后台采集暂停：磁盘空间不足",
    "readonly_storage": "后台采集暂停：数据目录不可写",
    "db_busy": "后台采集暂停：本地数据库正忙，正在重试",
    "db_corrupt": "后台采集暂停：本地数据库文件损坏，请从备份恢复",
    "storage_unavailable": "后台采集暂停：数据库暂时不可用，正在重试",
}


def storage_problem(exc, directory=None):
    """(code, Chinese message) for storage failures, None for other errors."""
    from .errors import classify_storage_error
    classified = classify_storage_error(exc, directory)
    if classified is not None:
        return classified.code, PROBLEM_MESSAGES.get(classified.code, PROBLEM_MESSAGES["storage_unavailable"])
    if isinstance(exc, (sqlite3.OperationalError, sqlite3.DatabaseError, OSError)):
        return "storage_unavailable", PROBLEM_MESSAGES["storage_unavailable"]
    return None


async def open_store_with_retry(directory):
    """Open the store, waiting for another process to finish initialising it (P1-5)."""
    from .migrations import MigrationLockTimeout, SchemaNotReady
    retryable = (sqlite3.OperationalError, sqlite3.DatabaseError, MigrationLockTimeout, SchemaNotReady, OSError)
    deadline = time.monotonic() + STORE_RETRY_SECONDS
    attempt = 0
    while True:
        try:
            store = await asyncio.to_thread(Store, directory)
        except retryable as exc:
            problem = storage_problem(exc, directory)
            code = problem[0] if problem else "starting"
            if time.monotonic() >= deadline:
                log.error("worker could not open the database", exc_info=True,
                          extra={"event": "worker_store_failed", "code": code})
                reason = problem[1].removeprefix("后台采集暂停：") if problem else str(exc)
                print(f"后台采集进程无法打开数据库（{reason}），已退出。日志：{Path(directory) / 'logs' / 'worker.log'}",
                      file=sys.stderr)
                raise SystemExit(3) from exc
            log.warning("database not ready (%s): %s", code, exc, extra={"event": "worker_store_retry", "code": code})
            set_problem(directory, STARTING)
            await asyncio.sleep(STORE_BACKOFF[min(attempt, len(STORE_BACKOFF) - 1)])
            attempt += 1
            continue
        set_problem(directory)
        return store


async def serve(directory):
    from . import worker_scheduler
    from .worker_scheduler import ParallelWorkScheduler
    from .social_worker import process_social_item

    # worker-single.lock is held for the whole lifecycle (single instance and
    # health checks). worker.lock is held only while dispatching or running
    # work; it is released during the idle sleep so a restore can proceed.
    # Waiting for the single lock (1 s: the API's health check may hold it
    # briefly) happens in a thread so the event loop never sleeps.
    with await asyncio.to_thread(_enter_lock, directory, "worker-single.lock", 1):
        set_problem(directory)
        pulse = asyncio.create_task(heartbeat(directory))
        scheduler = ParallelWorkScheduler()
        op = None
        try:
            # Step 2 (P1-4): reclaim Maps processes left by a previous worker,
            # before recover() requeues their queries. ps/lsof/waits run in a thread.
            from . import maps_guard
            reclaimed = await asyncio.to_thread(maps_guard.reclaim, directory)
            set_problem(directory, reclaimed.get("problem"), slot="maps")
            op = OperationLock(directory)
            if not op.try_acquire():
                raise RuntimeError(LOCKED_MESSAGE)
            store = await open_store_with_retry(directory)
            store.recover()
            store.recover_social()
            failures = 0
            retention_due = time.monotonic() + RETENTION_FIRST_DELAY
            while True:
                try:
                    if not op.held and not op.try_acquire():
                        await asyncio.sleep(1)  # Restore in progress: no dispatch, no database writes.
                        continue
                    store.dispatch_due()
                    # Invariant: turn() is the only place lanes start and it runs only
                    # while op is held, so "op not held" implies no lane task exists.
                    await scheduler.turn(store, process_run_item, process_social_item)
                    if time.monotonic() >= retention_due:
                        retention_due = time.monotonic() + RETENTION_INTERVAL
                        await asyncio.to_thread(run_retention, directory)
                    if await scheduler.idle(grace=worker_scheduler.IDLE_GRACE_SECONDS):
                        op.release()
                except Exception as exc:
                    failures += 1
                    problem = storage_problem(exc, directory)
                    if problem:
                        log.warning("worker loop storage error (%s): %s", problem[0], exc,
                                    extra={"event": "worker_loop_error", "code": problem[0]})
                        set_problem(directory, {"code": problem[0], "message": problem[1]})
                    else:
                        log.exception("worker loop failed", extra={"event": "worker_loop_error"})
                        if failures >= LOOP_MAX_FAILURES:
                            raise SystemExit(70) from exc
                    # Same invariant as the normal path: release worker.lock once no
                    # lane runs, so a restore can proceed while the error persists.
                    if op.held and await scheduler.idle(grace=worker_scheduler.IDLE_GRACE_SECONDS):
                        op.release()
                    await asyncio.sleep(LOOP_BACKOFF[min(failures - 1, len(LOOP_BACKOFF) - 1)])
                    continue
                if failures:
                    failures = 0
                    set_problem(directory)
                await asyncio.sleep(1)
        finally:
            pulse.cancel()
            cleanup = asyncio.gather(scheduler.aclose(), pulse, return_exceptions=True)
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Repeated stop signals must not interrupt source/browser
                    # cleanup or release the restore lock while a lane is alive.
                    interrupted = True
            (Path(directory) / "worker-heartbeat.json").unlink(missing_ok=True)
            set_problem(directory)
            set_problem(directory, slot="maps")
            if op is not None:
                op.close()
            if interrupted:
                raise asyncio.CancelledError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.environ.get("LEAD_DATA_DIR", "data"))
    args = parser.parse_args()
    from . import config
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
    from .logging_setup import configure
    configure("worker", Path(args.data_dir).expanduser())
    async def runner():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        await serve(args.data_dir)
    try:
        asyncio.run(runner())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
