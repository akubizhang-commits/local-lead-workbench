import csv
import io
import pytest
from fastapi.testclient import TestClient
from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.worker import process_run


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as c:
        c.headers["X-Local-Token"] = c.get("/api/session").json()["token"]
        yield c


def test_token_required_and_cross_origin_blocked(client):
    assert client.get("/api/companies", headers={"X-Local-Token": ""}).status_code == 401
    assert client.get("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_manual_entry_is_explicit_and_export_excludes_refusal(client):
    a = client.post("/api/companies", json={"name": "=2+3", "website": "https://fixture.example"}).json()
    assert a["source_kind"] == "manual"
    output = client.get("/api/exports?format=csv")
    assert output.status_code == 200
    assert "'=2+3" in output.text
    assert "source_url" in output.text.splitlines()[0]
    r = client.patch(f"/api/companies/{a['id']}", json={"revision": a["revision"], "status": "do_not_contact"})
    assert r.status_code == 200
    assert len(list(csv.reader(io.StringIO(client.get("/api/exports?format=csv").text)))) == 1


def test_invalid_task_and_private_website_rejected(client):
    assert client.post("/api/tasks", json={"name": "Bad"}).status_code == 422
    assert client.post("/api/companies", json={"name": "Bad", "website": "http://127.0.0.1"}).status_code == 422


def test_market_api_ignores_old_quantity_limit_and_reuses_same_market(client):
    payload = dict(mode="market", name="Fixture", country="US", state="California", city="Los Angeles",
                   company_types=["wholesale"], keywords=[], auto_enrich=False, limit=1000)
    first = client.post("/api/tasks", json=payload)
    assert first.status_code == 200
    assert first.json()["limit"] == 0
    second = client.post("/api/tasks", json={**payload, "name": "Renamed", "limit": 1})
    assert second.json()["id"] == first.json()["id"]
    page = client.get("/api/company-page", params={"market_id": first.json()["id"], "page_size": 50})
    assert page.status_code == 200 and page.json()["total"] == 0
    assert client.get("/api/company-page?page=0").status_code == 422


@pytest.mark.asyncio
async def test_worker_real_store_with_source_boundary_double(tmp_path):
    store = Store(tmp_path)
    task = store.create_task({"name": "Fixture task", "country": "US", "state": "California", "city": "Los Angeles",
                             "keywords": ["apparel wholesaler"], "company_types": ["wholesale"],
                             "limit": 1, "auto_enrich": True})
    run = store.start_task(task["id"])
    async def discover(query, output_dir, **kwargs):
        assert "Los Angeles" in query
        return {"status": "completed", "records": [{"source_key": "test-fixture", "source_url": "https://www.google.com/maps/test",
                 "name": "Synthetic fixture", "website": "https://fixture.example", "address": "Los Angeles"}],
                "warnings": [], "stop_reason": "completed"}
    async def enrich(url, **kwargs):
        assert url == "https://fixture.example"
        return {"status": "completed", "observations": [], "contacts": [], "social_links": []}
    await process_run(store, run["id"], discover=discover, enrich=enrich)
    assert store.run(run["id"])["status"] == "completed"
    assert len(store.companies()) == 1
    assert store.companies()[0]["last_checked_at"]


@pytest.mark.asyncio
async def test_missing_source_is_blocked_not_empty_success(tmp_path):
    store = Store(tmp_path)
    task = store.create_task({"name": "Fixture", "country": "US", "state": "California", "city": "Los Angeles",
                             "keywords": ["wholesale"], "company_types": [], "limit": 2, "auto_enrich": False})
    run = store.start_task(task["id"])
    async def missing(*args, **kwargs):
        return {"status": "blocked", "records": [], "warnings": ["未安装采集器"], "stop_reason": "missing_binary"}
    await process_run(store, run["id"], discover=missing)
    assert store.run(run["id"])["status"] == "blocked"
    assert store.companies() == []


@pytest.mark.asyncio
async def test_resumed_discovery_replay_does_not_consume_remaining_allowance(tmp_path):
    store = Store(tmp_path)
    task = store.create_task({"name": "Fixture", "country": "US", "state": "California", "city": "Los Angeles",
                             "keywords": ["wholesale"], "company_types": [], "limit": 3, "auto_enrich": False})
    run = store.start_task(task["id"])
    records = [{"source_key": f"fixture-{n}", "source_url": f"https://www.google.com/maps/{n}",
                "name": f"Fixture {n}"} for n in range(3)]
    store.ingest(records[0], run["id"], "prior interrupted query")
    async def replay(*args, limit, **kwargs):
        return {"status": "completed", "records": records[:limit], "warnings": []}
    await process_run(store, run["id"], discover=replay)
    assert len(store.companies()) == 3


@pytest.mark.asyncio
async def test_partial_source_has_partial_run_status(tmp_path):
    store = Store(tmp_path)
    a = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(a["id"])
    async def partial(*args, **kwargs):
        return {"status": "partial", "observations": [], "contacts": [], "warnings": ["team page failed"]}
    await process_run(store, run["id"], discover=lambda: None, enrich=partial)
    assert store.run(run["id"])["status"] == "partial"


@pytest.mark.asyncio
async def test_refresh_summary_keeps_counts_separate_from_detailed_errors(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(company["id"])
    async def enrich(*args, **kwargs):
        return {"status": "partial", "pages_checked": 3, "pages_skipped": 8,
                "contacts": [{"email": "info@fixture.example", "source_url": "https://fixture.example/contact",
                              "evidence": "Public contact"}],
                "observations": [], "warnings": ["Response body byte limit exceeded"]}
    await process_run(store, run["id"], enrich=enrich)
    result = store.run(run["id"])
    assert "读取 3 页" in result["message"]
    assert "提取 1 条联系记录" in result["message"]
    assert "跳过 8 个无关页面" in result["message"]
    assert "Response body" not in result["message"]
    assert "Response body" in result["error"]


@pytest.mark.asyncio
async def test_source_block_stops_further_discovery_queries(tmp_path):
    store = Store(tmp_path)
    task = store.create_task({"name": "Fixture", "country": "US", "state": "California", "city": "Los Angeles",
                             "keywords": ["wholesale", "importer"], "company_types": [], "limit": 3, "auto_enrich": False})
    run = store.start_task(task["id"])
    calls = []
    async def captcha(query, *args, **kwargs):
        calls.append(query)
        return {"status": "blocked", "records": [], "warnings": ["CAPTCHA_REQUIRED"]}
    await process_run(store, run["id"], discover=captcha)
    assert len(calls) == 1
    assert store.run(run["id"])["status"] == "blocked"
