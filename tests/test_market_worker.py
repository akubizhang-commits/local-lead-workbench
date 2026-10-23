import json
import pytest

from leadworkbench.db import Store
from leadworkbench.worker import process_run


def market(store):
    return store.create_market(dict(name="Fixture market", country="US", state="Fixture state",
                                   city="Fixture city", keywords=["clothing wholesaler"],
                                   company_types=["wholesale"], auto_enrich=False))


def job(key):
    return dict(key=key, query=f"clothing wholesaler {key}", geo=None, zoom=15,
                depth=10, bbox=None, level=0)


async def planner(config, directory):
    return dict(jobs=[job("a"), job("b")], coverage={"scope_label": "Fixture scope"},
                status="completed", warnings=[])


def record(index, phone="1234567890"):
    return dict(source_key=f"fixture:{index}", source_url=f"https://www.google.com/maps?cid={index}",
                name=f"Fixture company {index}", address=f"{index} Fixture road",
                website=f"https://fixture{index}.example", phone=phone)


@pytest.mark.asyncio
async def test_market_has_no_total_company_cutoff_and_repeated_tiles_deduplicate(tmp_path):
    store = Store(tmp_path)
    task = market(store)
    async def discover(*args, **kwargs):
        # Two overlapping query batches should not stop at the first 120 hits.
        indexes = range(100) if args[0].endswith("a") else range(50, 150)
        return dict(status="completed", records=[record(i) for i in indexes], warnings=[])
    first = store.start_task(task["id"])
    await process_run(store, first["id"], planner=planner, discover=discover)
    assert len(store.companies(max_rows=None)) == 150
    assert store.run(first["id"])["progress"]["new"] == 150
    second = store.start_task(task["id"])
    await process_run(store, second["id"], planner=planner, discover=discover)
    assert len(store.companies(max_rows=None)) == 150
    progress = store.run(second["id"])["progress"]
    assert progress["new"] == 0
    assert progress["existing"] == 150
    assert progress["changed"] == 0
    assert progress["enrichment_total"] == 0
    assert progress["enrichment_done"] == 0


@pytest.mark.asyncio
async def test_market_discovery_runs_before_website_enrichment(tmp_path):
    store = Store(tmp_path)
    payload = dict(name="Fixture", country="US", state="Fixture", city="Fixture",
                   keywords=["wholesale"], company_types=[], auto_enrich=True)
    task = store.create_market(payload)
    calls = []
    async def discover(query, *args, **kwargs):
        calls.append("discovery")
        return dict(status="completed", records=[record(1)], warnings=[])
    async def enrich(*args, **kwargs):
        calls.append("website")
        return dict(status="completed", contacts=[], observations=[], warnings=[])
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=planner, discover=discover, enrich=enrich)
    assert calls == ["discovery", "discovery", "website"]
    progress = store.run(run["id"])["progress"]
    assert progress["enrichment_total"] == 1
    assert progress["enrichment_done"] == 1


@pytest.mark.asyncio
async def test_market_empty_tile_does_not_abort_other_tiles(tmp_path):
    store = Store(tmp_path)
    task = market(store)
    calls = []
    async def discover(query, *args, **kwargs):
        calls.append(query)
        if query.endswith("a"):
            return dict(status="blocked", records=[], stop_reason="empty_output", warnings=["No validated listings"])
        return dict(status="completed", records=[record(1)], warnings=[])
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=planner, discover=discover)
    assert len(calls) == 2
    assert len(store.companies()) == 1
    assert store.run(run["id"])["status"] == "partial"


@pytest.mark.asyncio
async def test_dense_query_subdivides_and_does_not_finish_at_batch_cap(tmp_path):
    store = Store(tmp_path)
    task = market(store)
    async def dense_plan(config, directory):
        return dict(jobs=[{**job("root"), "bbox": [34, 35, -119, -118]}],
                    coverage={"scope_label": "Fixture"}, status="completed", warnings=[])
    calls = []
    async def discover(query, *args, **kwargs):
        calls.append(kwargs["geo"])
        return dict(status="completed", records=[record(len(calls))], warnings=[],
                    saturated=len(calls) == 1, stop_reason="company_limit" if len(calls) == 1 else "finished")
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=dense_plan, discover=discover)
    assert len(calls) == 5
    assert store.count_market_jobs(run["id"]) == 5
    assert len(store.companies()) == 5
    assert store.run(run["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_dense_timeout_with_records_subdivides(tmp_path):
    # A tile that times out while still returning records is too dense to finish
    # in one pass: it must subdivide (not just keep re-running the same big tile).
    store = Store(tmp_path)
    task = market(store)
    async def dense_plan(config, directory):
        return dict(jobs=[{**job("root"), "bbox": [34, 35, -119, -118]}],
                    coverage={"scope_label": "Fixture"}, status="completed", warnings=[])
    calls = []
    async def discover(query, *args, **kwargs):
        calls.append(kwargs["geo"])
        # Root times out with results; children finish cleanly.
        if len(calls) == 1:
            return dict(status="partial", records=[record(1)], warnings=[], stop_reason="timeout")
        return dict(status="completed", records=[record(len(calls))], warnings=[], stop_reason="finished")
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=dense_plan, discover=discover)
    # Root (1) subdivided into 4 children => 5 jobs total.
    assert store.count_market_jobs(run["id"]) == 5
    assert len(calls) == 5
    assert store.run(run["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_empty_timeout_does_not_subdivide(tmp_path):
    # A tile that times out with zero records is a stuck/empty tile, not density:
    # it must NOT subdivide (that was the old over-gridding failure mode).
    store = Store(tmp_path)
    task = market(store)
    async def dense_plan(config, directory):
        return dict(jobs=[{**job("root"), "bbox": [34, 35, -119, -118]}],
                    coverage={"scope_label": "Fixture"}, status="completed", warnings=[])
    calls = []
    async def discover(query, *args, **kwargs):
        calls.append(kwargs["geo"])
        return dict(status="partial", records=[], warnings=[], stop_reason="timeout")
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=dense_plan, discover=discover)
    # No subdivision: only the single root job ever exists.
    assert store.count_market_jobs(run["id"]) == 1


@pytest.mark.asyncio
async def test_saturated_leaf_remains_explicitly_unresolved(tmp_path):
    store = Store(tmp_path)
    task = market(store)
    async def leaf_plan(config, directory):
        return dict(jobs=[{**job("leaf"), "bbox": [34, 35, -119, -118], "level": 3}],
                    coverage={"scope_label": "Fixture"}, status="completed", warnings=[])
    async def discover(*args, **kwargs):
        return dict(status="completed", records=[record(1)], warnings=[], saturated=True)
    run = store.start_task(task["id"])
    await process_run(store, run["id"], planner=leaf_plan, discover=discover)
    assert store.run(run["id"])["status"] == "partial"
    assert store.task(task["id"])["coverage"]["unresolved"]
