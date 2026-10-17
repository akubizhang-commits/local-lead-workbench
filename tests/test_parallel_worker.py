"""Independent worker lanes, using events and temporary stores only."""
import asyncio
from collections import Counter

import pytest

from leadworkbench import worker, worker_scheduler
from leadworkbench.db import Store


class ActiveRuns:
    def __init__(self, *run_ids):
        self.run_ids = list(run_ids)

    def active_runs(self):
        return [{"id": run_id} for run_id in self.run_ids]


def parallel_scheduler():
    scheduler_type = getattr(worker_scheduler, "ParallelWorkScheduler", None)
    assert scheduler_type is not None, "serve needs a persistent three-lane scheduler"
    return scheduler_type()


def market_lane(stages):
    assert stages in (("planning", "discovery"), ("refresh",))
    return "maps" if stages == ("planning", "discovery") else "website"


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_lane", ["maps", "website", "social"])
async def test_slow_lane_does_not_block_other_lanes_or_overlap_itself(slow_lane):
    scheduler = parallel_scheduler()
    store = ActiveRuns("A", "B")
    release = asyncio.Event()
    completed = asyncio.Event()
    entered = {lane: asyncio.Event() for lane in ("maps", "website", "social")}
    calls = Counter()
    runs = {"maps": [], "website": []}
    running = Counter()
    peak = Counter()

    async def item(lane):
        calls[lane] += 1
        running[lane] += 1
        peak[lane] = max(peak[lane], running[lane])
        entered[lane].set()
        try:
            if lane == slow_lane:
                await release.wait()
                completed.set()
            return True
        finally:
            running[lane] -= 1

    async def market(store, run_id, *, stages):
        lane = market_lane(stages)
        runs[lane].append(run_id)
        return await item(lane)

    async def social(store):
        return await item("social")

    try:
        for _ in range(4):
            # A turn must return even though one source has not been released.
            await asyncio.wait_for(scheduler.turn(store, market, social), 2)
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in entered.values())), 2)
        assert calls[slow_lane] == 1
        assert all(calls[lane] == 4 for lane in entered if lane != slow_lane)
        assert peak == {"maps": 1, "website": 1, "social": 1}
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        await scheduler.turn(store, market, social)
        assert calls[slow_lane] == 2
        for lane in runs:
            assert runs[lane] == (
                ["A", "B"] if lane == slow_lane else ["A", "B", "A", "B", "A"])
    finally:
        await scheduler.aclose()
    assert not any(running.values())


@pytest.mark.asyncio
async def test_each_market_lane_rotates_survivors_and_new_or_resumed_runs():
    scheduler = parallel_scheduler()
    store = ActiveRuns("A", "B")
    calls = {"maps": [], "website": []}

    async def market(store, run_id, *, stages):
        calls[market_lane(stages)].append(run_id)
        return True

    async def social(store):
        return False

    try:
        await scheduler.turn(store, market, social)
        store.run_ids = ["B"]  # A paused.
        await scheduler.turn(store, market, social)
        store.run_ids = ["A", "B", "C"]  # A resumed; C is new.
        await scheduler.turn(store, market, social)
        await scheduler.turn(store, market, social)
        store.run_ids = ["A", "C"]  # B cancelled.
        await scheduler.turn(store, market, social)
        await scheduler.turn(store, market, social)
        assert calls == {
            "maps": ["A", "B", "B", "A", "C", "A"],
            "website": ["A", "B", "B", "A", "C", "A"],
        }
    finally:
        await scheduler.aclose()


@pytest.mark.asyncio
async def test_lane_skips_ineligible_runs_and_social_runs_without_markets():
    scheduler = parallel_scheduler()
    store = ActiveRuns("maps-only", "website-only")
    worked = []

    async def market(store, run_id, *, stages):
        lane = market_lane(stages)
        if run_id != f"{lane}-only":
            return False
        worked.append(lane)
        return True

    async def social(store):
        worked.append("social")
        return True

    try:
        await scheduler.turn(store, market, social)
        assert Counter(worked) == {"maps": 1, "website": 1, "social": 1}
        store.run_ids = []
        await scheduler.turn(store, market, social)
        assert Counter(worked) == {"maps": 1, "website": 1, "social": 2}
    finally:
        await scheduler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
async def test_failed_lane_is_reaped_and_replaced_without_stopping_others(caplog, failure):
    scheduler = parallel_scheduler()
    store = ActiveRuns("A", "B")
    calls = {"maps": [], "website": [], "social": []}

    async def market(store, run_id, *, stages):
        lane = market_lane(stages)
        calls[lane].append(run_id)
        if lane == "maps" and run_id == "A":
            raise failure("synthetic lane failure")
        return True

    async def social(store):
        calls["social"].append("social")
        return True

    try:
        await scheduler.turn(store, market, social)
        await scheduler.turn(store, market, social)
        assert calls == {
            "maps": ["A", "B"], "website": ["A", "B"],
            "social": ["social", "social"],
        }
        assert any("maps" in record.message.lower() for record in caplog.records)
        if failure is ValueError:
            assert "synthetic lane failure" in caplog.text
    finally:
        await scheduler.aclose()


@pytest.mark.asyncio
async def test_close_waits_for_all_lane_cleanup_and_reaps_final_errors(caplog):
    scheduler = parallel_scheduler()
    store = ActiveRuns("A")
    entered = {lane: asyncio.Event() for lane in ("maps", "website", "social")}
    cleaning = {lane: asyncio.Event() for lane in entered}
    release = asyncio.Event()
    finished = set()

    async def item(lane):
        entered[lane].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[lane].set()
            await release.wait()
            finished.add(lane)
            if lane == "social":
                raise ValueError("synthetic cleanup failure")

    async def market(store, run_id, *, stages):
        await item(market_lane(stages))

    async def social(store):
        await item("social")

    await scheduler.turn(store, market, social)
    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 2)
    close = asyncio.create_task(scheduler.aclose())
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in cleaning.values())), 2)
        assert not close.done()
    finally:
        release.set()
        await asyncio.wait_for(close, 2)
    assert finished == {"maps", "website", "social"}
    assert "synthetic cleanup failure" in caplog.text
    await scheduler.aclose()
    with pytest.raises(RuntimeError):
        await scheduler.turn(store, market, social)


@pytest.mark.asyncio
@pytest.mark.parametrize("stages", [("planning", "discovery"), ("refresh",)])
async def test_process_run_item_forwards_lane_filter_and_source_overrides(monkeypatch, stages):
    from leadworkbench import work_item_executor

    store = object()
    discover, enrich, planner = object(), object(), object()
    seen = []

    async def execute(s, run_id, **options):
        seen.append((s, run_id, options))
        return True

    monkeypatch.setattr(work_item_executor, "execute_run_item", execute)
    assert await worker.process_run_item(
        store, "A", discover=discover, enrich=enrich, planner=planner, stages=stages)
    assert seen == [(store, "A", {
        "discover": discover, "enrich": enrich, "planner": planner, "stages": stages,
    })]


@pytest.mark.asyncio
async def test_public_batch_process_run_drains_same_run_without_a_stage_filter(tmp_path):
    store = Store(tmp_path)
    market = store.create_market(dict(
        name="A", country="US", state="Fixture", city="A",
        keywords=["wholesale"], company_types=[], auto_enrich=True))
    run = store.start_task(market["id"])
    events = []

    async def planner(*args):
        events.append("planning")
        return dict(status="completed", warnings=[], jobs=[
            dict(key="query", query="Fixture query", level=0)])

    async def discover(*args, **kwargs):
        events.append("discovery")
        return dict(status="completed", warnings=[], records=[dict(
            source_key="fixture:1", source_url="https://maps.example/1",
            name="Fixture", website="https://fixture.example", address="Fixture")])

    async def enrich(*args, **kwargs):
        events.append("refresh")
        return dict(status="completed", contacts=[], observations=[], warnings=[])

    await worker.process_run(
        store, run["id"], discover=discover, enrich=enrich, planner=planner)
    assert events == ["planning", "discovery", "refresh"]
    assert store.run(run["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_real_items_finish_other_stages_while_discovery_is_waiting(tmp_path):
    from leadworkbench.social_worker import process_social_item

    store = Store(tmp_path)
    market = store.create_market(dict(
        name="A", country="US", state="Fixture", city="A",
        keywords=["wholesale"], company_types=[], auto_enrich=False))
    run = store.start_task(market["id"])
    company = store.manual_company("Fixture", "https://fixture.example")
    store.add_enrichment(run["id"], company["id"])
    store.create_social_run([company["id"]])
    discovery_started = asyncio.Event()
    website_finished = asyncio.Event()
    social_finished = asyncio.Event()
    discovery_cleaned = asyncio.Event()
    scheduler = parallel_scheduler()

    async def planner(*args):
        return dict(status="completed", warnings=[], jobs=[
            dict(key="query", query="Fixture query", level=0)])

    async def discover(*args, **kwargs):
        discovery_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            discovery_cleaned.set()

    async def enrich(*args, **kwargs):
        return dict(status="completed", contacts=[], observations=[], pages_checked=1)

    async def read(*args):
        return dict(status="succeeded", links=[])

    async def search(*args):
        return dict(status="no_results", results=[])

    async def market_item(s, run_id, *, stages):
        result = await worker.process_run_item(
            s, run_id, stages=stages, planner=planner, discover=discover, enrich=enrich)
        if result and stages == ("refresh",):
            website_finished.set()
        return result

    async def social(s):
        result = await process_social_item(s, read=read, search=search)
        if result:
            social_finished.set()
        return result

    await worker.process_run_item(
        store, run["id"], planner=planner, stages=("planning", "discovery"))
    try:
        await scheduler.turn(store, market_item, social)
        await asyncio.wait_for(asyncio.gather(
            discovery_started.wait(), website_finished.wait(), social_finished.wait()), 2)
        with store.connect() as con:
            states = dict(con.execute(
                "SELECT stage,state FROM work_items WHERE run_id=?", (run["id"],)))
            assert con.execute("SELECT count(*) FROM social_work WHERE state='running'").fetchone()[0] == 0
        assert states == {"planning": "succeeded", "discovery": "running", "refresh": "succeeded"}
        assert store.company(company["id"])["last_checked_at"]
        assert store.run(run["id"])["status"] == "running"
        assert not discovery_cleaned.is_set()
    finally:
        await scheduler.aclose()
    assert discovery_cleaned.is_set()
    with store.connect() as con:
        assert con.execute(
            "SELECT state FROM work_items WHERE run_id=? AND stage='discovery'",
            (run["id"],)).fetchone()[0] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["worker.lock", "worker-single.lock"])
async def test_serve_takes_both_locks_before_opening_store(tmp_path, monkeypatch, filename):
    def unexpected_store(directory):
        pytest.fail("Store opened before obtaining both lifecycle locks")

    monkeypatch.setattr(worker, "Store", unexpected_store)
    with worker.worker_lock(tmp_path, filename):
        with pytest.raises(RuntimeError):
            await worker.serve(tmp_path)
    for released in ("worker.lock", "worker-single.lock"):
        with worker.worker_lock(tmp_path, released):
            pass


@pytest.mark.asyncio
async def test_serve_holds_both_locks_through_lane_cleanup_and_keeps_heartbeat_independent(
        tmp_path, monkeypatch):
    from leadworkbench import social_worker

    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    store.refresh(company["id"])
    entered = {lane: asyncio.Event() for lane in ("maps", "website", "social")}
    cleaning = {lane: asyncio.Event() for lane in entered}
    release = asyncio.Event()
    finished = set()
    pulse_ready = asyncio.Event()
    pulse_request = asyncio.Event()
    pulse_done = asyncio.Event()
    tick_requests = asyncio.Queue()
    ticks = asyncio.Queue()
    pulse_stopped = asyncio.Event()

    async def item(lane):
        entered[lane].set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[lane].set()
            await release.wait()
            finished.add(lane)

    async def market(store, run_id, *, stages):
        await item(market_lane(stages))

    async def social(store):
        await item("social")

    async def pulse(directory):
        (directory / "worker-heartbeat.json").write_text("{}")
        pulse_ready.set()
        try:
            await pulse_request.wait()
            pulse_done.set()
            await asyncio.Event().wait()
        finally:
            pulse_stopped.set()

    async def tick(delay):
        assert delay == 1
        ticks.put_nowait(None)
        await tick_requests.get()

    monkeypatch.setattr(worker, "Store", lambda directory: store)
    monkeypatch.setattr(worker, "process_run_item", market)
    monkeypatch.setattr(social_worker, "process_social_item", social)
    monkeypatch.setattr(worker, "heartbeat", pulse)
    monkeypatch.setattr(worker.asyncio, "sleep", tick)
    serving = asyncio.create_task(worker.serve(tmp_path))
    try:
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 2)
        await asyncio.wait_for(pulse_ready.wait(), 2)
        for _ in range(3):
            await asyncio.wait_for(ticks.get(), 2)
            for filename in ("worker.lock", "worker-single.lock"):
                with pytest.raises(RuntimeError):
                    with worker.worker_lock(tmp_path, filename):
                        pass
            tick_requests.put_nowait(None)
        pulse_request.set()
        await asyncio.wait_for(pulse_done.wait(), 2)
        serving.cancel()
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in cleaning.values())), 2)
        # A second stop signal must not cut short asynchronous source cleanup.
        serving.cancel()
        await asyncio.wait({serving}, timeout=0)
        assert not serving.done()
        for filename in ("worker.lock", "worker-single.lock"):
            with pytest.raises(RuntimeError):
                with worker.worker_lock(tmp_path, filename):
                    pass
    finally:
        serving.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serving, 2)
    assert finished == {"maps", "website", "social"}
    assert pulse_stopped.is_set()
    assert not (tmp_path / "worker-heartbeat.json").exists()
    for filename in ("worker.lock", "worker-single.lock"):
        with worker.worker_lock(tmp_path, filename):
            pass
