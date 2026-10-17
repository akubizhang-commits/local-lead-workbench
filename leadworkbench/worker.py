"""Single local worker. A process lock protects recovery and database restoration."""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import logging
import os
from pathlib import Path
import signal
import json
import time
from datetime import datetime, timezone

from .db import Store, now
from .sources.website import enrich_website

def worker_health(directory):
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
    return {"alive": locked and 0 <= age < 30, "last_seen": payload["last_seen"]}


async def heartbeat(directory):
    path = Path(directory) / "worker-heartbeat.json"
    while True:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid(), "last_seen": now()}))
        temporary.replace(path)
        await asyncio.sleep(5)


LOCKED_MESSAGE = "worker 正在运行，需先停止后台进程才能恢复备份"


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
                    raise RuntimeError(message or LOCKED_MESSAGE) from exc
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


async def serve(directory):
    from . import worker_scheduler
    from .worker_scheduler import ParallelWorkScheduler
    from .social_worker import process_social_item

    # worker-single.lock is held for the whole lifecycle (single instance and
    # health checks). worker.lock is held only while dispatching or running
    # work; it is released during the idle sleep so a restore can proceed.
    with worker_lock(directory, "worker-single.lock"):
        op = OperationLock(directory)
        if not op.try_acquire():
            op.close()
            raise RuntimeError(LOCKED_MESSAGE)
        try:
            store = Store(directory)
        except BaseException:
            op.close()
            raise
        scheduler = ParallelWorkScheduler()
        pulse = asyncio.create_task(heartbeat(directory))
        try:
            store.recover()
            store.recover_social()
            while True:
                if not op.held and not op.try_acquire():
                    await asyncio.sleep(1)  # Restore in progress: no dispatch, no database writes.
                    continue
                store.dispatch_due()
                # Invariant: turn() is the only place lanes start and it runs only
                # while op is held, so "op not held" implies no lane task exists.
                await scheduler.turn(store, process_run_item, process_social_item)
                if await scheduler.idle(grace=worker_scheduler.IDLE_GRACE_SECONDS):
                    op.release()
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
            op.close()
            if interrupted:
                raise asyncio.CancelledError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.environ.get("LEAD_DATA_DIR", "data"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
