"""Worker scheduling regressions: temporary stores and synthetic source boundaries only."""
import asyncio

import pytest

from leadworkbench import worker
from leadworkbench.db import Store


def market_run(store, name):
    task = store.create_market(dict(name=name, country="US", state="Fixture", city=name,
                                   keywords=["wholesale"], company_types=[], auto_enrich=False))
    return store.start_task(task["id"])


def plan(name):
    return dict(status="completed", warnings=[], jobs=[
        dict(key=f"{name}-{i}", query=f"{name}-{i}", level=0) for i in range(3)])


def items(store, run_id):
    with store.connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM work_items WHERE run_id=? ORDER BY rowid", (run_id,))]


@pytest.mark.asyncio
async def test_serve_interleaves_multiple_markets_and_social_at_item_boundaries(tmp_path, monkeypatch):
    from leadworkbench import market_plan, social_worker
    from leadworkbench.sources import maps

    store = Store(tmp_path)
    runs = [market_run(store, name) for name in ("A", "B")]
    companies = [store.manual_company(str(i), f"https://fixture{i}.example")["id"] for i in range(8)]
    social_run = store.create_social_run(companies)
    refreshes = [store.refresh(company_id) for company_id in companies[:2]]
    market_events = []
    entered = {lane: asyncio.Event() for lane in ("maps", "website", "social")}
    release = {lane: asyncio.Event() for lane in entered}
    active = dict.fromkeys(entered, 0)
    peak = dict.fromkeys(entered, 0)
    social_progress = asyncio.Event()
    websites_finished = asyncio.Event()
    markets_finished = asyncio.Event()
    social_done = 0

    async def planner(config, directory):
        market_events.append(config["city"])
        return plan(config["city"])

    async def discover(query, *args, **kwargs):
        market_events.append(query[0])
        if query == "A-0":
            entered["maps"].set()
            await release["maps"].wait()
        return dict(status="completed", records=[], warnings=[])

    async def enrich(*args, **kwargs):
        entered["website"].set()
        await release["website"].wait()
        return dict(status="completed", contacts=[], observations=[], pages_checked=1)

    async def read(url, stage):
        entered["social"].set()
        await release["social"].wait()
        return dict(status="succeeded", links=[])

    async def search(*args, **kwargs):
        entered["social"].set()
        await release["social"].wait()
        return dict(status="no_results", results=[])

    original_market = worker.process_run_item
    async def market(s, run_id, *, stages):
        lane = "maps" if stages == ("planning", "discovery") else "website"
        active[lane] += 1
        peak[lane] = max(peak[lane], active[lane])
        try:
            claimed = await original_market(s, run_id, stages=stages)
            if claimed:
                # Signal only after the real executor has persisted the item.
                if lane == "maps" and all(s.run(r["id"])["status"] == "completed" for r in runs):
                    markets_finished.set()
                if lane == "website" and all(s.run(r["id"])["status"] == "completed" for r in refreshes):
                    websites_finished.set()
            return claimed
        finally:
            active[lane] -= 1

    original_social = social_worker.process_social_item
    async def social(s):
        nonlocal social_done
        active["social"] += 1
        peak["social"] = max(peak["social"], active["social"])
        try:
            claimed = await original_social(s, read=read, search=search)
            if claimed:
                social_done += 1
                if social_done >= 3:
                    social_progress.set()
            return claimed
        finally:
            active["social"] -= 1

    async def pulse(directory):
        await asyncio.Event().wait()

    ticks = 0
    original_sleep = asyncio.sleep
    async def tick(delay):
        nonlocal ticks
        ticks += 1
        # Yield to nested executor/source tasks; completion is event-driven,
        # independent of how many event-loop turns any one item needs.
        await original_sleep(0)

    # Advance the fake clock between turns; real workers now pace Maps globally.
    from leadworkbench import maps_policy
    monkeypatch.setattr(maps_policy.time, "time", lambda: 100 + ticks * 121)
    monkeypatch.setattr(worker, "Store", lambda directory: store)
    monkeypatch.setattr(worker, "heartbeat", pulse)
    monkeypatch.setattr(worker, "process_run_item", market)
    monkeypatch.setattr(worker, "enrich_website", enrich)
    monkeypatch.setattr(worker.asyncio, "sleep", tick)
    monkeypatch.setattr(market_plan, "plan_market", planner)
    monkeypatch.setattr(maps, "discover_maps", discover)
    monkeypatch.setattr(social_worker, "process_social_item", social)
    serving = asyncio.create_task(worker.serve(tmp_path))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 2)
        # Planning both markets and starting discovery must not await social
        # or website sources. The single Maps slot now remains occupied.
        assert market_events == ["A", "B", "A"]
        assert active == {"maps": 1, "website": 1, "social": 1}

        release["social"].set()
        await asyncio.wait_for(social_progress.wait(), 2)
        assert market_events == ["A", "B", "A"]
        assert not websites_finished.is_set()
        with store.connect() as con:
            completed = con.execute("""SELECT count(*) FROM social_work WHERE run_id=?
                AND state IN ('succeeded','no_results','skipped')""", (social_run["id"],)).fetchone()[0]
        assert completed >= 3, "social lane did not persist progress while other sources waited"

        release["website"].set()
        await asyncio.wait_for(websites_finished.wait(), 2)
        assert market_events == ["A", "B", "A"]
        assert [store.run(r["id"])["progress"]["done"] for r in runs] == [1, 1]

        release["maps"].set()
        await asyncio.wait_for(markets_finished.wait(), 2)
    finally:
        serving.cancel()
        for event in release.values():
            event.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serving, 2)

    # Fairness is ordered within the Maps lane, never between independent lanes.
    assert market_events == ["A", "B", "A", "B", "A", "B", "A", "B"]
    assert [store.run(r["id"])["progress"]["done"] for r in runs] == [4, 4]
    assert all(store.company(company_id)["last_checked_at"] for company_id in companies[:2])
    assert peak == {"maps": 1, "website": 1, "social": 1}
    assert active == {"maps": 0, "website": 0, "social": 0}
    for filename in ("worker.lock", "worker-single.lock"):
        with worker.worker_lock(tmp_path, filename):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "cancel"])
async def test_batch_planning_interrupt_keeps_item_pending_without_saving_plan(tmp_path, action):
    store = Store(tmp_path)
    run = market_run(store, "A")

    async def planner(config, directory):
        store.control(run["id"], action)
        return plan("A")

    await worker.process_run(store, run["id"], planner=planner)
    assert store.run(run["id"])["status"] == {"pause": "paused", "cancel": "cancelled"}[action]
    assert [(i["stage"], i["state"]) for i in items(store, run["id"])] == [("planning", "pending")]


@pytest.mark.asyncio
async def test_batch_source_exception_finishes_failed_item_and_continues(tmp_path):
    store = Store(tmp_path)
    run = market_run(store, "A")
    calls = []

    async def planner(*args):
        return plan("A")

    async def discover(query, *args, **kwargs):
        calls.append(query)
        if query == "A-0":
            raise ValueError("synthetic source failure")
        return dict(status="completed", records=[], warnings=[])

    await worker.process_run(store, run["id"], planner=planner, discover=discover)
    assert calls == ["A-0", "A-1", "A-2"]
    assert [i["state"] for i in items(store, run["id"])] == ["succeeded", "failed", "succeeded", "succeeded"]
    assert store.run(run["id"])["status"] == "partial"


@pytest.mark.asyncio
async def test_batch_cancelled_discovery_is_recoverable_and_does_not_replay_completed_items(tmp_path):
    store = Store(tmp_path)
    run = market_run(store, "A")
    entered = asyncio.Event()
    calls = []

    async def planner(*args):
        return plan("A")

    async def interrupted(query, *args, **kwargs):
        calls.append(query)
        if query == "A-1":
            entered.set()
            await asyncio.Event().wait()
        return dict(status="completed", records=[], warnings=[])

    task = asyncio.create_task(worker.process_run(store, run["id"], planner=planner, discover=interrupted))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [i["state"] for i in items(store, run["id"])] == ["succeeded", "succeeded", "pending", "pending"]
    store.recover()

    async def resumed(query, *args, **kwargs):
        calls.append(query)
        return dict(status="completed", records=[], warnings=[])

    await worker.process_run(store, run["id"], planner=planner, discover=resumed)
    assert calls == ["A-0", "A-1", "A-1", "A-2"]
    assert store.run(run["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_shutdown_waits_for_enrichment_cleanup_before_returning_item(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(company["id"])
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def enrich(*args, **kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            # Simulate asynchronous transport/browser cleanup.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            cleaned.set()

    task = asyncio.create_task(worker.process_run(store, run["id"], enrich=enrich))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set(), "worker returned while its enrichment task still owned resources"
    assert items(store, run["id"])[0]["state"] == "pending"
    assert store.company(company["id"])["last_checked_at"] is None


@pytest.mark.asyncio
async def test_worker_enrichment_boundary_remains_patchable(tmp_path, monkeypatch):
    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(company["id"])

    async def enrich(*args, **kwargs):
        return dict(status="completed", contacts=[], observations=[], pages_checked=7)

    monkeypatch.setattr(worker, "enrich_website", enrich)
    # Never invoke the real source even if the default was captured too early.
    def unexpected_request(*args, **kwargs):
        raise AssertionError("real enrichment boundary invoked")
    monkeypatch.setattr("leadworkbench.sources.website.normalize_url", unexpected_request)
    await worker.process_run(store, run["id"])
    assert store.run(run["id"])["status"] == "completed"
    assert "7 页" in store.run(run["id"])["message"]


@pytest.mark.asyncio
async def test_scheduler_rotates_survivors_and_appends_new_and_resumed_runs(tmp_path):
    from leadworkbench.worker_scheduler import WorkScheduler

    store = Store(tmp_path)
    a, b = [market_run(store, name) for name in ("A", "B")]
    scheduler = WorkScheduler()
    events = []

    async def planner(config, directory):
        events.append(config["city"])
        return plan(config["city"])

    async def discover(query, *args, **kwargs):
        events.append(query[0])
        return dict(status="completed", records=[], warnings=[])

    async def market(s, rid):
        return await worker.process_run_item(s, rid, planner=planner, discover=discover)

    async def social(s):
        events.append("social")
        return False

    await scheduler.turn(store, market, social)  # A planning
    store.control(a["id"], "pause")
    await scheduler.turn(store, market, social)  # B planning
    c = market_run(store, "C")
    store.control(a["id"], "resume")
    await scheduler.turn(store, market, social)  # B was already waiting
    await scheduler.turn(store, market, social)  # A rejoined at the tail
    store.control(b["id"], "cancel")
    await scheduler.turn(store, market, social)  # C must not be skipped
    await scheduler.turn(store, market, social)  # A discovery

    assert events == ["A", "social", "B", "social", "B", "social",
                      "A", "social", "C", "social", "A", "social"]
    assert store.run(b["id"])["status"] == "cancelled"
    store.control(a["id"], "pause")
    store.control(c["id"], "pause")
    await scheduler.turn(store, market, social)
    assert events[-2:] == ["social", "social"]  # Social still runs with no eligible markets.


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "cancel"])
async def test_enrichment_control_interrupt_cleans_up_and_preserves_result(tmp_path, action):
    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(company["id"])
    cleaned = asyncio.Event()

    async def enrich(*args, **kwargs):
        try:
            store.control(run["id"], action)
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    await asyncio.wait_for(worker.process_run_item(store, run["id"], enrich=enrich), 2)
    assert cleaned.is_set()
    assert items(store, run["id"])[0]["state"] == "pending"
    assert store.company(company["id"])["last_checked_at"] is None
    assert store.run(run["id"])["status"] == {"pause": "paused", "cancel": "cancelled"}[action]
    if action == "pause":
        store.control(run["id"], "resume")

        async def completed(*args, **kwargs):
            return dict(status="completed", contacts=[], observations=[])

        await worker.process_run_item(store, run["id"], enrich=completed)
        assert store.run(run["id"])["status"] == "completed"
        assert store.company(company["id"])["last_checked_at"]


@pytest.mark.asyncio
async def test_scheduler_source_failure_does_not_delay_social_or_other_markets(tmp_path):
    from leadworkbench.worker_scheduler import WorkScheduler

    store = Store(tmp_path)
    a, b = [market_run(store, name) for name in ("A", "B")]
    scheduler = WorkScheduler()
    events = []

    async def planner(config, directory):
        events.append(config["city"])
        if config["city"] == "A":
            raise ValueError("synthetic planning failure")
        return plan(config["city"])

    async def market(s, rid):
        return await worker.process_run_item(s, rid, planner=planner)

    async def social(s):
        events.append("social")
        return False

    await scheduler.turn(store, market, social)
    await scheduler.turn(store, market, social)
    assert events == ["A", "social", "B", "social"]
    assert store.run(a["id"])["status"] == "failed"
    assert store.run(b["id"])["progress"]["done"] == 1


@pytest.mark.asyncio
async def test_serve_recovers_interrupted_work_and_cleans_heartbeat_on_cancellation(tmp_path, monkeypatch):
    store = Store(tmp_path)
    run = market_run(store, "A")
    store.claim_item(run["id"])
    company = store.manual_company("Fixture", "https://fixture.example")
    social_run = store.create_social_run([company["id"]])
    social_item = store.claim_social_item()
    entered = asyncio.Event()
    from leadworkbench import market_plan

    async def planner(*args):
        # Startup recovered both domains before processing the first item.
        with store.connect() as con:
            state = con.execute("SELECT state FROM social_work WHERE id=?", (social_item["id"],)).fetchone()[0]
        assert state == "pending"
        assert store.social_run(social_run["id"])["status"] in {"queued", "running"}
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "Store", lambda directory: store)
    monkeypatch.setattr(market_plan, "plan_market", planner)
    task = asyncio.create_task(worker.serve(tmp_path))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # Let the real heartbeat task publish its first temporary-store pulse.
        await asyncio.sleep(0)
        assert worker.worker_health(tmp_path)["alive"]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert items(store, run["id"])[0]["state"] == "pending"
    assert not (tmp_path / "worker-heartbeat.json").exists()
    assert worker.worker_health(tmp_path)["alive"] is False
    with worker.worker_lock(tmp_path):
        pass
