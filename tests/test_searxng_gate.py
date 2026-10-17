"""The local metasearch endpoint still consumes upstream query capacity."""
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from leadworkbench import social_policy
from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.social_worker import process_social_item


def queued_search(store, name):
    company = store.manual_company(name, f"https://{name.lower()}.example")
    run = store.create_social_run([company["id"]])
    with store.connect() as con:
        con.execute("UPDATE social_work SET state='skipped' WHERE run_id=? AND stage='website'", (run["id"],))
    return run


@pytest.mark.asyncio
async def test_search_queries_across_companies_share_normal_interval(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(social_policy.time, "time", lambda: clock[0])
    store = Store(tmp_path)
    first, second = queued_search(store, "First"), queued_search(store, "Second")
    search = AsyncMock(return_value={"status": "no_results", "results": []})
    assert await process_social_item(store, search=search)
    with store.connect() as con:
        gate = social_policy.gate(con, "searxng")
    assert 15 <= gate["not_before"] - clock[0] <= 30
    assert not await process_social_item(store, search=search)
    assert search.await_count == 1
    assert store.social_run(second["id"])["summary"]["pending"] > 0
    clock[0] = gate["not_before"]
    assert await process_social_item(store, search=search)
    assert search.await_count == 2
    assert store.social_run(first["id"])["platform_gates"]["searxng"]["next_attempt_at"]


def test_provider_failures_back_off_then_require_configuration_recovery(monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(social_policy.time, "time", lambda: clock[0])
    item = {"stage": "search", "target": "Fixture query"}
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        for attempt, wait in enumerate((600, 1800, 7200, 7200), 1):
            assert social_policy.observe(con, item, {"status": "blocked", "reason": "provider_unavailable"})
            gate = social_policy.gate(con, "searxng")
            assert gate["failures"] == attempt
            assert gate["not_before"] == clock[0] + wait
            assert not social_policy.allowed(con, "searxng")
            clock[0] += wait
        assert gate["state"] == "waiting_configuration"
        assert not social_policy.allowed(con, "searxng")
        assert social_policy.allowed(con, "instagram")


def test_unconfigured_search_stays_paused_without_affecting_other_sources():
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        assert social_policy.observe(con, {"stage": "search", "target": "Fixture"},
                                     {"status": "blocked", "reason": "not_configured"})
        gate = social_policy.public_gate(con, "searxng")
        assert gate["state"] == "waiting_configuration" and gate["manual_required"]
        assert gate["next_attempt_at"] is None
        assert not social_policy.allowed(con, "searxng")
        assert social_policy.allowed(con, "facebook")
        assert social_policy.platform_for({"stage": "website", "target": "https://fixture.example"}) == ""


def test_search_resume_preserves_cooldown_refreshes_endpoint_and_is_not_a_market_platform(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(social_policy.time, "time", lambda: clock[0])
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    store = app.state.store
    queued_search(store, "Fixture")
    store.set_social_settings("http://127.0.0.1:18888")
    with store.connect() as con:
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    ("searxng", "waiting_recovery", 2000, "Provider unavailable", 2))
    client = TestClient(app)
    try:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        path = "/api/social/platforms/searxng/resume"
        assert client.post(path).status_code == 409
        assert store.claim_social_item() is None
        clock[0] = 2000
        assert client.post(path).status_code == 200
        item = store.claim_social_item()
        assert item["stage"] == "search"
        assert item["settings"]["searxng_url"] == "http://127.0.0.1:18888/search"
        payload = dict(country="US", state="Fixture", city="Fixture", keywords=["clothing"],
                       company_types=[], social_platforms=["searxng"])
        assert client.post("/api/tasks", json=payload).status_code == 422
    finally:
        client.close()
