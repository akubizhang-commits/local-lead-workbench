"""P0-5: catalogue fallback, "结果不完整" and "重试定位并补查" (design §7.5). No network."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from leadworkbench import market_plan as market
from leadworkbench.api import create_app
from leadworkbench.db import Conflict, Store
from leadworkbench.market_plan import INCOMPLETE_PREFIX
from leadworkbench.regions import index, unique_place_id
from leadworkbench.worker import process_run
from test_market_plan import CITY

LA = dict(city="Los Angeles", state="California", country="US", company_types=[], keywords=["roofing"])
NOWHERE = {**LA, "city": "Nowhereville Fixture"}


@pytest.fixture
def geocoder(tmp_path, monkeypatch):
    state = {"calls": 0, "mode": "ok"}

    async def fetch(endpoint, params):
        state["calls"] += 1
        if state["mode"] == "timeout":
            raise TimeoutError("fixture timeout")
        if state["mode"] == "cooldown":
            raise market.GeocoderCooldown(market.time.time() + 3600, "City geocoder HTTP 429; shared cooldown")
        return [copy.deepcopy(CITY)]
    monkeypatch.setattr(market, "_fetch_city", fetch)
    monkeypatch.setattr(market, "RATE_LIMIT_FILE", tmp_path / "rate.lock")
    monkeypatch.setattr(market, "REQUEST_SPACING", 0)
    return state


async def test_geocoder_success_does_not_consult_catalogue(tmp_path, geocoder, monkeypatch):
    monkeypatch.setattr(market, "_catalogue_fallback", lambda *a, **k: pytest.fail("catalogue consulted"))
    plan = await market.plan_market(LA, tmp_path)
    assert (plan["mode"], plan["geometry_source"], len(plan["jobs"])) == ("grid", "geocoder", 17)
    assert "unresolved" not in plan


@pytest.mark.parametrize("mode", ["timeout", "cooldown", "cached_failure"])
async def test_geocoder_failure_uses_catalogue_bounds(tmp_path, geocoder, mode):
    if mode == "cached_failure":
        geocoder["mode"] = "timeout"
        await market.plan_market(NOWHERE | {"city": "Los Angeles"}, tmp_path / "warm")
        target = tmp_path / "warm"
    else:
        geocoder["mode"] = mode
        target = tmp_path
    calls = geocoder["calls"]
    plan = await market.plan_market(LA, target)
    place = index()[unique_place_id("US", "California", "Los Angeles")]
    assert (plan["mode"], plan["geometry_source"]) == ("grid", "catalogue")
    assert plan["coverage"]["bbox"] == place["bbox"] and "Census Bureau" in plan["coverage"]["attribution"]
    assert any("已改用内置城市范围" in w for w in plan["warnings"])
    assert any("城市范围定位" in w or "geocoder" in w.lower() for w in plan["warnings"])
    assert plan["status"] == "completed" and "unresolved" not in plan
    if mode == "cached_failure":
        assert geocoder["calls"] == calls  # failure cache honoured, no request


async def test_all_location_failures_mark_incomplete(tmp_path, geocoder):
    geocoder["mode"] = "cooldown"
    plan = await market.plan_market(NOWHERE, tmp_path)
    assert (plan["mode"], plan["status"], plan["incomplete_reason"]) == ("text_only", "completed", "geocode_failed")
    [reason] = plan["unresolved"]
    assert reason.startswith(INCOMPLETE_PREFIX) and "定位服务冷却中" in reason
    assert plan["geocoder_retry_at"]
    calls = geocoder["calls"]
    again = await market.plan_market({**NOWHERE, "_relocate": True}, tmp_path)
    assert geocoder["calls"] == calls and again["mode"] == "text_only"  # cooldown: no request sent


def no_bounds_place(monkeypatch):
    place_id = unique_place_id("US", "California", "Los Angeles")
    place = index()[place_id]
    monkeypatch.delitem(place, "bbox")
    monkeypatch.delitem(place, "geo")
    return {**LA, "geography": {"place_id": place_id, "state_id": place["state_id"]}}


async def test_catalogue_place_without_bounds_tries_geocoder(tmp_path, geocoder, monkeypatch):
    config = no_bounds_place(monkeypatch)
    plan = await market.plan_market(config, tmp_path / "ok")
    assert (plan["mode"], plan["geometry_source"], geocoder["calls"]) == ("grid", "geocoder", 1)
    geocoder["mode"] = "timeout"
    plan = await market.plan_market(config, tmp_path / "fail")
    assert (plan["mode"], plan["incomplete_reason"]) == ("text_only", "catalogue_no_bounds")
    assert plan["unresolved"][0].startswith("结果不完整：内置目录中该城市没有范围坐标")


async def test_mismatched_place_falls_through_to_geocoder(tmp_path, geocoder, monkeypatch):
    monkeypatch.setattr(market, "_catalogue_fallback", lambda *a, **k: None)
    geocoder["mode"] = "timeout"
    config = {**NOWHERE, "geography": {"place_id": unique_place_id("US", "California", "Los Angeles"),
                                       "state_id": "US-CA"}}
    plan = await market.plan_market(config, tmp_path)
    assert geocoder["calls"] == 1 and plan["incomplete_reason"] == "geocode_failed"


def text_only_plan(store, run_id, reason=True):
    plan = dict(jobs=[dict(key="root", query="roofing in Nowhereville", geo=None, bbox=None, level=0, zoom=12, depth=10)],
                coverage={"scope_label": "Nowhereville", "bbox": None}, warnings=[], mode="text_only", status="completed")
    if reason:
        plan.update(unresolved=[market.INCOMPLETE_REASONS["geocode_failed"]], incomplete_reason="geocode_failed")
    store.save_market_plan(run_id, plan)


def new_market(store, keyword="roofing"):
    task = store.create_market(dict(country="US", state="California", city="Nowhereville", keywords=[keyword],
                                    company_types=[], auto_enrich=False))
    return task, store.start_task(task["id"])


def finish(store, run_id, discovery="succeeded"):
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded',result='{}' WHERE run_id=? AND stage='planning'", (run_id,))
        con.execute("UPDATE work_items SET state=? WHERE run_id=? AND stage='discovery'", (discovery, run_id))
    store.finalize(run_id)


def test_text_only_round_is_partial_with_one_reason(tmp_path):
    store = Store(tmp_path)
    task, run = new_market(store)
    text_only_plan(store, run["id"])
    finish(store, run["id"])
    result = store.run(run["id"])
    coverage = result["coverage"]
    assert result["status"] == "partial"
    assert [r for r in coverage["unresolved"] if r.startswith(INCOMPLETE_PREFIX)] == coverage["unresolved"]
    assert len(coverage["unresolved"]) == 1
    assert (coverage["incomplete"], coverage["incomplete_reason"], coverage["retryable"], coverage["relocatable"]) == \
        (True, "geocode_failed", False, True)


def test_historical_text_only_rounds_gain_the_reason_on_read(tmp_path):
    store = Store(tmp_path)
    task, run = new_market(store)
    text_only_plan(store, run["id"], reason=False)
    finish(store, run["id"])
    coverage = store.task(task["id"])["coverage"]
    assert coverage["incomplete"] and coverage["unresolved"][0].startswith(INCOMPLETE_PREFIX)


def test_resume_after_failed_text_search_keeps_reason(tmp_path):
    store = Store(tmp_path)
    task, run = new_market(store)
    text_only_plan(store, run["id"])
    finish(store, run["id"], discovery="failed")
    store.control(run["id"], "resume")
    with store.connect() as con:
        assert con.execute("SELECT unresolved FROM market_runs WHERE run_id=?", (run["id"],)).fetchone()[0] == "[]"
    finish(store, run["id"])
    result = store.run(run["id"])
    assert result["status"] == "partial" and result["coverage"]["unresolved"][0].startswith(INCOMPLETE_PREFIX)


def test_relocate_state_matrix(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(KeyError):
        store.relocate_market_run("missing")
    refresh = store.refresh(store.manual_company("Solo", "https://solo.example")["id"])
    with pytest.raises(ValueError, match="仅市场检索轮次支持补查"):
        store.relocate_market_run(refresh["id"])
    task, run = new_market(store)
    text_only_plan(store, run["id"])

    def blocked(message):
        with pytest.raises(Conflict, match=message):
            store.relocate_market_run(run["id"])
        assert store.run(run["id"])["coverage"]["relocate_block_reason"].startswith(message)

    blocked("轮次正在执行")
    with store.connect() as con:
        con.execute("UPDATE runs SET status='paused' WHERE id=?", (run["id"],))
    blocked("请先点继续")
    with store.connect() as con:
        con.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run["id"],))
    blocked("已取消的轮次不能补查")
    with store.connect() as con:
        con.execute("UPDATE runs SET status='completed' WHERE id=?", (run["id"],))
        con.execute("INSERT INTO runs VALUES('later',?,'discovery',NULL,'paused','{}','',NULL,'9999',NULL,'{}')", (task["id"],))
    blocked("只能对该市场最新一轮补查")
    with store.connect() as con:
        con.execute("UPDATE runs SET created_at='0000' WHERE id='later'")
    blocked("该市场有进行中的轮次")
    with store.connect() as con:
        con.execute("DELETE FROM runs WHERE id='later'")
        # A later social round shares the task id but never blocks relocation.
        con.execute("INSERT INTO runs VALUES('social',?,'social',NULL,'running','{}','',NULL,'9999',NULL,'{}')", (task["id"],))
        con.execute("UPDATE market_runs SET plan=? WHERE run_id=?", (json.dumps({"mode": "grid"}), run["id"]))
    blocked("本轮已按城市范围检索，无需补查")
    with store.connect() as con:
        con.execute("UPDATE market_runs SET plan=? WHERE run_id=?", (json.dumps({"mode": "text_only"}), run["id"]))
    for status in ("completed", "partial", "failed", "blocked"):
        with store.connect() as con:
            con.execute("UPDATE runs SET status=?,finished_at='x' WHERE id=?", (status, run["id"]))
        assert store.run(run["id"])["coverage"]["relocatable"] is True
        relocated = store.relocate_market_run(run["id"])
        assert (relocated["status"], relocated["finished_at"], relocated["message"]) == ("queued", None, "重试城市定位并补查")


async def test_relocate_replans_and_adds_area_searches(tmp_path):
    store = Store(tmp_path)
    task, run = new_market(store)
    text_only_plan(store, run["id"])
    finish(store, run["id"])
    seen = []

    async def planner(config, directory):
        seen.append(config.get("_relocate"))
        root = market._job("roofing in Nowhereville", [37.3, -121.9], [37.2, 37.4, -122.0, -121.8], 0)
        return dict(jobs=[root, *market._grid(root, 4, 1)], coverage={"scope_label": "grid", "bbox": root["bbox"]},
                    warnings=[], mode="grid", status="completed", geometry_source="catalogue")

    async def discover(*args, **kwargs):
        return dict(status="completed", records=[], warnings=[])
    store.relocate_market_run(run["id"])
    await process_run(store, run["id"], planner=planner, discover=discover)
    assert seen == [True]
    assert store.count_market_jobs(run["id"]) == 18
    result = store.run(run["id"])
    assert result["status"] == "completed" and not result["coverage"]["incomplete"]
    assert result["coverage"]["unresolved"] == []


async def test_pause_during_relocate_planning_keeps_marker(tmp_path):
    store = Store(tmp_path)
    task, run = new_market(store)
    text_only_plan(store, run["id"])
    finish(store, run["id"])
    store.relocate_market_run(run["id"])
    seen = []

    async def pausing(config, directory):
        seen.append(config.get("_relocate"))
        if len(seen) == 1:
            store.control(run["id"], "pause")
        return dict(jobs=[], coverage={}, warnings=[], mode="text_only", status="completed",
                    unresolved=[market.INCOMPLETE_REASONS["geocode_failed"]], incomplete_reason="geocode_failed")

    await process_run(store, run["id"], planner=pausing)
    store.control(run["id"], "resume")
    await process_run(store, run["id"], planner=pausing)
    assert seen == [True, True]


def test_api_relocate_route_and_controls(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        store = app.state.store
        task, run = new_market(store)
        text_only_plan(store, run["id"])
        assert client.post(f"/api/runs/{run['id']}/relocate").status_code == 409
        assert client.post(f"/api/runs/{run['id']}/pause").json()["status"] == "paused"
        assert client.post(f"/api/runs/{run['id']}/resume").json()["status"] == "queued"
        finish(store, run["id"])
        response = client.post(f"/api/runs/{run['id']}/relocate")
        assert response.status_code == 200 and response.json()["status"] == "queued"
        assert client.post(f"/api/runs/{run['id']}/cancel").json()["status"] == "cancelled"
        assert client.post(f"/api/runs/{run['id']}/other").status_code == 422
