"""P1-5: the worker waits for the schema and survives storage errors (design §4.2)."""
import asyncio
import json
import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from leadworkbench import worker, worker_scheduler
from leadworkbench.db import Store


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(worker, "STORE_BACKOFF", (0.01,))
    monkeypatch.setattr(worker, "LOOP_BACKOFF", (0.01,))


async def test_open_store_retries_until_schema_exists(tmp_path, monkeypatch):
    calls = []
    def flaky(directory):
        calls.append(directory)
        if len(calls) <= 3:
            raise sqlite3.OperationalError("no such table: schedules")
        return "store"
    monkeypatch.setattr(worker, "Store", flaky)
    assert await worker.open_store_with_retry(tmp_path) == "store"
    assert len(calls) == 4
    assert str(tmp_path) not in worker._PROBLEMS


async def test_open_store_gives_up_with_exit_code_3(tmp_path, monkeypatch, capsys):
    def broken(directory):
        raise sqlite3.OperationalError("no such table: schedules")
    monkeypatch.setattr(worker, "Store", broken)
    monkeypatch.setattr(worker, "STORE_RETRY_SECONDS", 1)
    with pytest.raises(SystemExit) as raised:
        await worker.open_store_with_retry(tmp_path)
    assert raised.value.code == 3
    assert "后台采集进程无法打开数据库" in capsys.readouterr().err
    worker.set_problem(tmp_path)


class FakeStore:
    def __init__(self):
        self.dispatches = 0
        self.failed = asyncio.Event()
        self.recovered = asyncio.Event()

    def recover(self):
        pass

    def recover_social(self):
        pass

    def dispatch_due(self):
        self.dispatches += 1
        if self.dispatches == 1:
            self.failed.set()
            raise sqlite3.OperationalError("database or disk is full")
        if self.dispatches >= 3:
            self.recovered.set()


class FakeScheduler:
    async def turn(self, *args):
        pass

    async def idle(self, grace):
        return False

    async def aclose(self):
        pass


async def test_loop_survives_storage_error_and_clears_problem(tmp_path, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(worker, "Store", lambda directory: store)
    monkeypatch.setattr(worker_scheduler, "ParallelWorkScheduler", FakeScheduler)
    problems = []
    real = worker.set_problem
    monkeypatch.setattr(worker, "set_problem", lambda d, p=None: (problems.append(p), real(d, p)))
    task = asyncio.create_task(worker.serve(tmp_path))
    try:
        await asyncio.wait_for(store.recovered.wait(), 10)
        assert not task.done()
        assert {"code": "disk_full", "message": "后台采集暂停：磁盘空间不足"} in problems
        assert str(tmp_path) not in worker._PROBLEMS
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_heartbeat_carries_problem(tmp_path):
    worker.set_problem(tmp_path, worker.STARTING)
    try:
        payload = worker.heartbeat_payload(tmp_path)
        assert payload["problem"] == worker.STARTING
    finally:
        worker.set_problem(tmp_path)
    assert "problem" not in worker.heartbeat_payload(tmp_path)


async def test_single_lock_is_taken_in_a_thread(tmp_path, monkeypatch):
    seen = []
    real = asyncio.to_thread
    async def recording(func, *args, **kwargs):
        seen.append((func, args))
        return await real(func, *args, **kwargs)
    monkeypatch.setattr(worker.asyncio, "to_thread", recording)
    with worker.worker_lock(tmp_path, "worker.lock"):
        with pytest.raises(RuntimeError):
            await worker.serve(tmp_path)
    assert seen and seen[0][0] is worker._enter_lock
    assert seen[0][1][1:] == ("worker-single.lock", 1)


def _open(directory, queue):
    try:
        Store(directory)
        queue.put("ok")
    except BaseException as exc:  # pragma: no cover - reported to the parent
        queue.put(f"{type(exc).__name__}: {exc}")


def test_two_processes_initialise_a_fresh_directory(tmp_path):
    context = multiprocessing.get_context("spawn")
    for round in range(20):
        directory = tmp_path / f"d{round}"
        queue = context.Queue()
        processes = [context.Process(target=_open, args=(str(directory), queue)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(60)
        results = [queue.get(timeout=5) for _ in processes]
        assert results == ["ok", "ok"], results
        with sqlite3.connect(directory / "workbench.sqlite3") as con:
            assert con.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone()
