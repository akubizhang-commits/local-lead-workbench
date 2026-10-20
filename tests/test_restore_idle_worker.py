"""An idle resident worker releases worker.lock so restore works without stopping it."""
import asyncio
import json
import threading
import time

import pytest

from leadworkbench import social_worker, worker, worker_scheduler
from leadworkbench.db import Store
from leadworkbench.worker import OperationLock, serve, worker_lock
from leadworkbench.worker_scheduler import ParallelWorkScheduler

BUSY = "后台正在处理作业，请等待当前作业完成或暂停相关任务后再恢复备份"


def _try_lock(directory, hold, got, wait=2.0):
    # Runs in a thread: worker_lock waits with time.sleep.
    with worker_lock(directory, wait_seconds=wait):
        got.set()
        hold.wait(5)


async def _until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def _quiet_social(store):
    return False


async def _stop(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)


async def test_scheduler_idle_with_no_tasks_returns_true():
    scheduler = ParallelWorkScheduler()
    assert await scheduler.idle() is True
    async def done():
        return False
    scheduler._tasks["social"] = asyncio.create_task(done())
    await asyncio.sleep(0)
    assert await scheduler.idle(grace=0) is True


def test_worker_lock_wait_times_out_with_custom_message(tmp_path):
    hold, got = threading.Event(), threading.Event()
    holder = threading.Thread(target=_try_lock, args=(tmp_path, hold, got, 0))
    holder.start()
    try:
        assert got.wait(2)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="自定义"):
            with worker_lock(tmp_path, wait_seconds=0.5, message="自定义提示"):
                pass
        assert time.monotonic() - started >= 0.45
    finally:
        hold.set()
        holder.join(5)


async def test_idle_worker_releases_operation_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(social_worker, "process_social_item", _quiet_social)
    dispatched = []
    original = Store.dispatch_due
    monkeypatch.setattr(Store, "dispatch_due", lambda self: (dispatched.append(1), original(self))[1])
    task = asyncio.create_task(serve(tmp_path))
    assert await _until(lambda: dispatched)
    hold, got = threading.Event(), threading.Event()
    locker = asyncio.create_task(asyncio.to_thread(_try_lock, tmp_path, hold, got, 3.0))
    assert await _until(got.is_set, 2.5)
    before = len(dispatched)
    await asyncio.sleep(1.5)
    assert len(dispatched) == before  # No scheduling while a restore holds the lock.
    hold.set()
    await locker
    assert await _until(lambda: len(dispatched) > before, 3)
    await _stop(task)


async def test_release_only_when_scheduler_empty(tmp_path, monkeypatch):
    calls = []
    async def social(store):
        calls.append(1)
        if len(calls) == 2:
            await asyncio.sleep(0.5)  # One lane outlives the idle grace period.
        return False
    monkeypatch.setattr(social_worker, "process_social_item", social)
    schedulers, released = [], []
    original_init = ParallelWorkScheduler.__init__
    def init(self):
        original_init(self)
        schedulers.append(self)
    monkeypatch.setattr(ParallelWorkScheduler, "__init__", init)
    original_release = OperationLock.release
    def release(self):
        if self.held:
            released.append(dict(schedulers[0]._tasks))
        original_release(self)
    monkeypatch.setattr(OperationLock, "release", release)
    task = asyncio.create_task(serve(tmp_path))
    assert await _until(lambda: len(calls) >= 3, 5)
    await _stop(task)
    assert released and all(tasks == {} for tasks in released)


async def test_cancel_while_restore_holds_lock_exits_promptly(tmp_path, monkeypatch):
    monkeypatch.setattr(social_worker, "process_social_item", _quiet_social)
    task = asyncio.create_task(serve(tmp_path))
    assert await _until(lambda: (tmp_path / "worker-heartbeat.json").exists())
    hold, got = threading.Event(), threading.Event()
    locker = asyncio.create_task(asyncio.to_thread(_try_lock, tmp_path, hold, got, 3.0))
    try:
        assert await _until(got.is_set, 2.5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        started = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert time.monotonic() - started < 2
        assert not (tmp_path / "worker-heartbeat.json").exists()
    finally:
        hold.set()
        await locker


async def test_startup_lock_failure_closes_handle(tmp_path, monkeypatch):
    closed = []
    original = OperationLock.close
    monkeypatch.setattr(OperationLock, "close", lambda self: (closed.append(1), original(self))[1])
    with worker_lock(tmp_path):
        with pytest.raises(RuntimeError, match="worker 正在运行"):
            await serve(tmp_path)
    assert closed == [1]


async def test_store_init_failure_releases_operation_lock(tmp_path, monkeypatch):
    def broken(directory):
        raise RuntimeError("boom")
    monkeypatch.setattr(worker, "Store", broken)
    closed = []
    original = OperationLock.close
    monkeypatch.setattr(OperationLock, "close", lambda self: (closed.append(1), original(self))[1])
    with pytest.raises(RuntimeError, match="boom"):
        await serve(tmp_path)
    with worker_lock(tmp_path):
        pass
    assert closed == [1]


async def test_busy_lane_keeps_lock(tmp_path, monkeypatch):
    release = asyncio.Event()
    started = asyncio.Event()
    async def busy(store):
        started.set()
        await release.wait()
        return False
    monkeypatch.setattr(social_worker, "process_social_item", busy)
    task = asyncio.create_task(serve(tmp_path))
    await asyncio.wait_for(started.wait(), 3)
    def attempt():
        with worker_lock(tmp_path, wait_seconds=0.5, message=BUSY):
            pass
    with pytest.raises(RuntimeError, match=BUSY):
        await asyncio.to_thread(attempt)
    release.set()
    await _stop(task)


def _client(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    return client


MARKET = dict(country="US", state="California", city="San Jose", keywords=["metal roof"], company_types=["wholesale"])


def test_api_restore_with_idle_worker_then_worker_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(social_worker, "process_social_item", _quiet_social)
    claimed = []
    async def fake_item(store, run_id, *, stages=None, **kwargs):
        item = store.claim_item(run_id, stages=stages)
        if not item:
            store.finalize(run_id)
            return False
        claimed.append(item["stage"])
        store.finish_item(item["id"], "succeeded", {})
        store.finalize(run_id)
        return True
    monkeypatch.setattr(worker, "process_run_item", fake_item)
    loop = asyncio.new_event_loop()
    holder = {}
    def run():
        asyncio.set_event_loop(loop)
        holder["task"] = loop.create_task(serve(tmp_path))
        try:
            loop.run_until_complete(holder["task"])
        except asyncio.CancelledError:
            pass
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    heartbeat = tmp_path / "worker-heartbeat.json"
    try:
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeat.exists()
        with _client(tmp_path) as client:
            assert client.post("/api/companies", json={"name": "Kept Co", "website": "https://kept.example"}).status_code == 200
            name = client.post("/api/backups").json()["name"]
            assert client.post("/api/companies", json={"name": "Dropped Co", "website": "https://dropped.example"}).status_code == 200
            response = client.post("/api/backups/restore", json={"name": name})
            assert response.status_code == 200, response.text
            assert [c["name"] for c in client.get("/api/company-page").json()["items"]] == ["Kept Co"]
            restored_at = json.loads(heartbeat.read_text())["last_seen"]
            task = client.post("/api/tasks", json=MARKET).json()
            assert client.post(f"/api/tasks/{task['id']}/runs").status_code == 200
            deadline = time.monotonic() + 5
            while "planning" not in claimed and time.monotonic() < deadline:
                time.sleep(0.05)
            assert "planning" in claimed
            deadline = time.monotonic() + 8
            while json.loads(heartbeat.read_text())["last_seen"] == restored_at and time.monotonic() < deadline:
                time.sleep(0.2)
            assert json.loads(heartbeat.read_text())["last_seen"] != restored_at
    finally:
        loop.call_soon_threadsafe(holder["task"].cancel)
        thread.join(5)
        assert not thread.is_alive()
        loop.close()


@pytest.mark.parametrize("state,expected", [("queued", 409), ("paused", 200)])
def test_restore_rules_unchanged(tmp_path, state, expected):
    with _client(tmp_path) as client:
        name = client.post("/api/backups").json()["name"]
        task = client.post("/api/tasks", json=MARKET).json()
        run = client.post(f"/api/tasks/{task['id']}/runs").json()
        if state == "paused":
            assert client.post(f"/api/runs/{run['id']}/pause").status_code == 200
        response = client.post("/api/backups/restore", json={"name": name})
        assert response.status_code == expected, response.text
