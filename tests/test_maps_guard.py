"""P1-4: Maps guard process ties the scraper's lifetime to the worker (design §9.5)."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from leadworkbench import maps_guard, worker_scheduler
from leadworkbench.sources import maps

FIXTURE = Path(__file__).parent / "fixtures" / "fake_scraper.py"
ROOT = Path(__file__).resolve().parent.parent


def make_scraper(tmp_path, monkeypatch, **config):
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    config.setdefault("log", str(tmp_path / "fake.log"))
    config.setdefault("marker", f"fake-grandchild-{tmp_path.name}")
    config_path = folder / "fake.json"
    config_path.write_text(json.dumps(config))
    shim = folder / "google-maps-scraper"
    shim.write_text(f"#!{sys.executable}\nimport os, runpy\nos.environ['FAKE_SCRAPER_CONFIG'] = {str(config_path)!r}\n"
                    f"runpy.run_path({str(FIXTURE)!r}, run_name='__main__')\n")
    shim.chmod(0o755)
    monkeypatch.setenv("MAPS_SCRAPER_BIN", str(shim))
    return config


def events(config):
    path = Path(config["log"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def ps_lines():
    return subprocess.run(["ps", "-A", "-o", "pid=,pgid=,command="], capture_output=True, text=True).stdout.splitlines()


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def leftovers(config):
    pids = {e["pid"] for e in events(config)}
    return [line for line in ps_lines() if config["marker"] in line
            or any(line.split()[0] == str(pid) for pid in pids if alive(pid))]


def until(predicate, timeout, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def registry_files(data_dir):
    folder = maps_guard.registry_dir(data_dir)
    return sorted(folder.glob("*.json")) if folder.exists() else []


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    yield path
    for line in ps_lines():
        if f"fake-grandchild-{tmp_path.name}" in line or str(tmp_path) in line:
            try:
                os.kill(int(line.split()[0]), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


async def test_1_normal_cancel_and_timeout_leave_nothing(tmp_path, monkeypatch, data_dir):
    config = make_scraper(tmp_path, monkeypatch, seconds=1.2, rows=3)
    result = await maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir, timeout_seconds=30)
    assert result["status"] == "completed" and len(result["records"]) == 3
    assert registry_files(data_dir) == [] and not maps_guard.busy(data_dir)
    assert [e["event"] for e in events(config)] == ["start", "stop"]
    assert until(lambda: not leftovers(config), 3)

    config = make_scraper(tmp_path, monkeypatch, seconds=30, log=str(tmp_path / "cancel.log"))
    started = time.monotonic()
    result = await maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir,
                                      should_stop=lambda: time.monotonic() - started > 1)
    assert result["stop_reason"] == "cancelled"
    assert registry_files(data_dir) == [] and not maps_guard.busy(data_dir)
    assert until(lambda: not leftovers(config), 3)

    config = make_scraper(tmp_path, monkeypatch, seconds=30, log=str(tmp_path / "timeout.log"))
    result = await maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir, timeout_seconds=1)
    assert result["stop_reason"] == "timeout"
    assert registry_files(data_dir) == [] and not maps_guard.busy(data_dir)
    assert until(lambda: not leftovers(config), 3)


WORKER = textwrap.dedent("""
    import asyncio, sys
    from pathlib import Path
    from leadworkbench.sources import maps
    asyncio.run(maps.discover_maps("fixture", Path(sys.argv[1]), data_dir=Path(sys.argv[2]), timeout_seconds=120))
""")


def start_worker(tmp_path, data_dir):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path / "out"), str(data_dir)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_2_worker_kill9_ends_scraper_and_grandchild(tmp_path, monkeypatch, data_dir):
    config = make_scraper(tmp_path, monkeypatch, seconds=60)
    process = start_worker(tmp_path, data_dir)
    try:
        assert until(lambda: any(e["event"] == "start" for e in events(config)), 10)
        assert until(lambda: any(config["marker"] in line for line in ps_lines()), 5)
        process.kill()
        process.wait()
        killed = time.monotonic()
        assert until(lambda: not leftovers(config), 2.5), leftovers(config)
        assert time.monotonic() - killed <= 2.6
        assert until(lambda: registry_files(data_dir) == [], 1)
        assert not maps_guard.busy(data_dir)
    finally:
        process.kill()


def test_3_worker_and_guard_kill9_then_reclaim_before_next_query(tmp_path, monkeypatch, data_dir):
    config = make_scraper(tmp_path, monkeypatch, seconds=60)
    process = start_worker(tmp_path, data_dir)
    try:
        assert until(lambda: registry_files(data_dir) and any(e["event"] == "start" for e in events(config)), 10)
        guard = json.loads(registry_files(data_dir)[0].read_text())["guard_pid"]
        os.kill(guard, signal.SIGKILL)
        process.kill()
        process.wait()
        scraper = events(config)[0]["pid"]
        time.sleep(0.5)
        assert alive(scraper) and maps_guard.busy(data_dir)
        result = maps_guard.reclaim(data_dir)
        reclaimed_at = time.time()
        assert result == {"action": "reclaimed", "problem": None}
        assert not alive(scraper) and not leftovers(config)
        assert registry_files(data_dir) == [] and not maps_guard.busy(data_dir)
    finally:
        process.kill()
    # Test 4 (single concurrency): the next query starts only after the old one is gone.
    second = make_scraper(tmp_path, monkeypatch, seconds=0.6, rows=1, log=str(tmp_path / "second.log"))
    result = asyncio.run(maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir, timeout_seconds=30))
    assert result["status"] == "completed"
    assert events(second)[0]["t"] > reclaimed_at


def test_5_reused_pgid_is_not_killed(tmp_path, data_dir):
    stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
    holder = maps_guard._open_lock(maps_guard.lock_file(data_dir))
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        folder = maps_guard.registry_dir(data_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "424242.json").write_text(json.dumps(dict(guard_pid=424242, pgid=stranger.pid, worker_pid=1,
                                                            binary="/nonexistent/google-maps-scraper")))
        result = maps_guard.reclaim(data_dir, timeout=0.3)
        assert result["action"] == "orphan" and result["problem"]["code"] == "maps_orphan"
        assert stranger.poll() is None
        assert registry_files(data_dir) == []
    finally:
        os.close(holder)
        stranger.kill()
        stranger.wait()


class LaneStore:
    def __init__(self, directory):
        self.directory = directory

    def active_runs(self):
        return [{"id": "run"}]


async def drain(scheduler):
    await scheduler.idle(grace=2)


async def test_6_busy_lock_claims_planning_only(data_dir):
    seen = []
    async def market(store, run_id, *, stages):
        seen.append(stages)
        return False
    async def social(store):
        return False
    scheduler = worker_scheduler.ParallelWorkScheduler()
    store = LaneStore(data_dir)
    holder = maps_guard._open_lock(maps_guard.lock_file(data_dir))
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        await scheduler.turn(store, market, social)
        await drain(scheduler)
    finally:
        os.close(holder)
    await scheduler.turn(store, market, social)
    await drain(scheduler)
    await scheduler.aclose()
    assert ("planning",) in seen and ("planning", "discovery") in seen
    assert seen.index(("planning",)) < seen.index(("planning", "discovery"))


async def test_7_busy_guard_keeps_the_query_pending(tmp_path, monkeypatch, data_dir):
    from leadworkbench import maps_policy
    from leadworkbench.db import Store
    from leadworkbench.worker import process_run_item
    from test_market_worker import market, planner
    make_scraper(tmp_path, monkeypatch, seconds=0.5)
    store = Store(data_dir)
    run = store.start_task(market(store)["id"])
    assert await process_run_item(store, run["id"], planner=planner)  # planning
    with store.connect() as con:
        before = tuple(con.execute("SELECT not_before,manual_required,failures,reason FROM maps_access_policy").fetchone())
        item = con.execute("SELECT id,result FROM work_items WHERE run_id=? AND stage='discovery' AND state='pending' "
                           "ORDER BY rowid LIMIT 1", (run["id"],)).fetchone()
        # Only this query stays pending, so later claims can only return it.
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='discovery' AND id!=?",
                    (run["id"], item["id"]))
    prior = json.loads(item["result"] or "{}").get("maps", {})
    holder = maps_guard._open_lock(maps_guard.lock_file(data_dir))
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        start = time.time()
        assert await process_run_item(store, run["id"], stages=("discovery",))
    finally:
        os.close(holder)
    with store.connect() as con:
        row = con.execute("SELECT state,result FROM work_items WHERE id=?", (item["id"],)).fetchone()
        after = tuple(con.execute("SELECT not_before,manual_required,failures,reason FROM maps_access_policy").fetchone())
    result = json.loads(row["result"])
    meta = result["maps"]
    assert row["state"] == "pending" and result["stop_reason"] == "maps_busy"
    assert isinstance(meta["next_attempt_at"], float) and start + 29 <= meta["next_attempt_at"] <= time.time() + 31
    assert meta.get("attempts", 0) == prior.get("attempts", 0)
    assert meta.get("cycle_attempts", 0) == prior.get("cycle_attempts", 0)
    assert not meta.get("history")
    assert after == before
    assert store.claim_item(run["id"], pace_maps=True, stages=("discovery",)) is None
    real = maps_policy.time.time
    monkeypatch.setattr(maps_policy.time, "time", lambda: real() + 31)
    claimed = store.claim_item(run["id"], pace_maps=True, stages=("discovery",))
    assert claimed and claimed["id"] == item["id"]


async def test_9_running_query_is_never_reclaimed(tmp_path, monkeypatch, data_dir):
    config = make_scraper(tmp_path, monkeypatch, seconds=4, rows=2)
    monkeypatch.setattr(worker_scheduler, "MAPS_BUSY_RECLAIM_SECONDS", 1)
    calls = []
    real_reclaim = maps_guard.reclaim
    monkeypatch.setattr(maps_guard, "reclaim", lambda *a, **k: calls.append((a, k)) or real_reclaim(*a, **k))
    results = []
    async def market(store, run_id, *, stages):
        if "discovery" not in stages or results:
            return False
        results.append(await maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir, timeout_seconds=30))
        return True
    async def social(store):
        return False
    scheduler = worker_scheduler.ParallelWorkScheduler()
    store = LaneStore(data_dir)
    deadline = time.monotonic() + 15
    while not results and time.monotonic() < deadline:
        await scheduler.turn(store, market, social)
        await asyncio.sleep(0.2)
    await scheduler.aclose()
    assert results and results[0]["status"] == "completed"
    assert calls == []
    assert [e["event"] for e in events(config)] == ["start", "stop"]


async def test_9b_protected_guard_is_left_alone(tmp_path, monkeypatch, data_dir):
    config = make_scraper(tmp_path, monkeypatch, seconds=2, rows=1)
    query = asyncio.create_task(maps.discover_maps("fixture", tmp_path / "out", data_dir=data_dir, timeout_seconds=30))
    for _ in range(100):
        if registry_files(data_dir):
            break
        await asyncio.sleep(0.05)
    guard = json.loads(registry_files(data_dir)[0].read_text())["guard_pid"]
    result = await asyncio.to_thread(maps_guard.reclaim, data_dir, 1, protect_pids={guard})
    assert result["action"] == "protected"
    assert (await query)["status"] == "completed"
    assert [e["event"] for e in events(config)] == ["start", "stop"]
