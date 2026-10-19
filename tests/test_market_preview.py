"""P1-21: keyword presets, required keywords and the local query preview (design §8)."""
from fastapi.testclient import TestClient
import pytest

from db_hash import content_hash
from leadworkbench import market_plan
from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.industry_presets import expand, merge_keywords, presets
from leadworkbench.market_worker import MAX_DISCOVERY_JOBS
from leadworkbench.regions import catalogue, index, validate_selection

SAN_JOSE = dict(country="US", state="California", city="San Jose", state_id="US-CA",
                place_id="US-PLACE-0668000", company_types=["wholesale"])


def za_metro():
    p = next(r for r in catalogue("ZA")["regions"] if r.get("subtype") == "metropolitan_municipality")
    return dict(country="ZA", state=index()[p["state_id"]]["name"], city=p["name"], state_id=p["state_id"],
                place_id=p["id"], company_types=["wholesale"])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        client.data_dir = tmp_path
        yield client


def estimate(**extra):
    return market_plan.estimate_queries(validate_selection({**SAN_JOSE, **extra}))


def test_san_jose_catalogue_estimates():
    one = estimate(keywords=["metal roofing supply"])
    assert (one["geometry"], one["initial_min"], one["initial_max"], one["max"]) == ("catalogue", 17, 17, 421)
    assert one["query_terms"] == ["metal roofing supply in San Jose, California, US"]
    two = estimate(keywords=["metal roofing supply", "sheet metal supplier"])
    assert (two["initial_min"], two["max"]) == (34, 842)


def test_round_budget_caps_the_maximum():
    # R3-6: ten terms would need 4210 jobs; the whole round stops at MAX_DISCOVERY_JOBS.
    many = estimate(keywords=[f"term {i}" for i in range(10)])
    assert many["initial_min"] == 170
    assert many["max"] <= MAX_DISCOVERY_JOBS
    assert many["max"] == min(10 * 421, many["max"]) and many["max"] < 10 * 421
    assert many["max"] > MAX_DISCOVERY_JOBS - 4  # no split fits any more


def test_geocoder_and_text_only_estimates():
    geocoder = market_plan.estimate_queries(dict(country="US", state="California", city="Nowhereville",
                                                 keywords=["a", "b"]))
    assert (geocoder["geometry"], geocoder["initial_min"], geocoder["initial_max"], geocoder["max"]) == (
        "geocoder", 2, 34, None)
    za = market_plan.estimate_queries(validate_selection({**za_metro(), "keywords": ["metal supplier"]}))
    assert (za["geometry"], za["initial_min"], za["initial_max"], za["max"]) == ("text_only", 1, 1, 1)


def test_estimate_is_cached_and_results_are_copies(monkeypatch):
    market_plan._estimate_cached.cache_clear()
    calls = []
    real = market_plan.split_job
    monkeypatch.setattr(market_plan, "split_job", lambda job: calls.append(1) or real(job))
    first = estimate(keywords=["cache probe"])
    count = len(calls)
    assert count > 0
    first["query_terms"].append("mutated")
    first["terms"].clear()
    second = estimate(keywords=["cache probe"])
    assert len(calls) == count
    assert second["query_terms"] == ["cache probe in San Jose, California, US"]
    assert second["terms"] == ["cache probe"]


def test_presets_expand_by_company_type():
    assert {p["id"] for p in presets()} >= {"apparel", "building", "metals"} and len(presets()) == 10
    assert expand("metals", ["retail"]) == ["metal supply store"]
    assert expand("metals", []) == ["metal supplier"]
    assert merge_keywords(["Metal Supplier"], "metals", ["wholesale", "import"]) == ["metal supplier", "steel importer"]
    with pytest.raises(ValueError):
        expand("unknown", [])


def test_preview_and_task_list_never_touch_the_network(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network used")
    monkeypatch.setattr(market_plan, "_fetch_city", boom)
    monkeypatch.setattr(market_plan.aiohttp, "ClientSession", boom)
    response = client.post("/api/tasks/preview", json={**SAN_JOSE, "keywords": ["metal roofing supply"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keywords"] == ["metal roofing supply"] and body["max"] == 421 and body["existing_market"] is None
    geocoded = client.post("/api/tasks/preview", json={"country": "US", "state": "California",
                                                       "city": "Nowhereville", "keywords": ["x"]})
    assert geocoded.json()["geometry"] == "geocoder"
    client.post("/api/tasks", json={**SAN_JOSE, "keywords": ["metal roofing supply"]})
    tasks = client.get("/api/tasks").json()
    assert tasks[0]["query_estimate"]["max"] == 421 and tasks[0]["needs_keywords"] is False


def test_preview_with_only_a_preset_expands_by_type(client):
    body = client.post("/api/tasks/preview", json={**SAN_JOSE, "company_types": ["retail"],
                                                   "industry": "metals"}).json()
    assert body["keywords"] == ["metal supply store"]
    created = client.post("/api/tasks", json={**SAN_JOSE, "company_types": ["retail"], "industry": "metals"}).json()
    assert created["keywords"] == ["metal supply store"]


def test_expanded_keywords_over_thirty_are_rejected_on_the_keywords_field(client):
    payload = {**SAN_JOSE, "company_types": ["wholesale", "retail", "import"], "industry": "metals",
               "keywords": [f"keyword {i}" for i in range(28)]}
    for path in ("/api/tasks/preview", "/api/tasks"):
        response = client.post(path, json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == [
            {"loc": ["body", "keywords"], "msg": "关键词和预设展开后最多 30 个", "type": "value_error"}]
    assert client.get("/api/tasks").json() == []


def test_preview_finds_the_existing_market_and_writes_nothing(client):
    created = client.post("/api/tasks", json={**SAN_JOSE, "keywords": ["metal roofing supply"]}).json()
    path = client.data_dir / "workbench.sqlite3"
    before = content_hash(path)
    body = client.post("/api/tasks/preview", json={**SAN_JOSE, "keywords": ["Metal Roofing Supply"]}).json()
    assert body["existing_market"] == {"id": created["id"], "name": created["name"], "archived": False}
    assert content_hash(path) == before


def test_preview_rejects_an_invalid_place_like_create(client):
    payload = {**SAN_JOSE, "place_id": "US-PLACE-INVALID", "keywords": ["x"]}
    preview, create = client.post("/api/tasks/preview", json=payload), client.post("/api/tasks", json=payload)
    assert preview.status_code == create.status_code == 400
    assert preview.json()["code"] == "invalid" and preview.json()["detail"] == create.json()["detail"]


def test_za_municipality_preview_is_text_only(client):
    body = client.post("/api/tasks/preview", json={**za_metro(), "keywords": ["metal supplier"]}).json()
    assert body["geometry"] == "text_only"


def test_legacy_market_without_keywords_keeps_working(tmp_path):
    store = Store(tmp_path)
    task = store.create_market(dict(country="US", state="California", city="Los Angeles",
                                    keywords=[], company_types=["wholesale"], auto_enrich=False))
    assert task["needs_keywords"] is True
    assert task["legacy_terms"] == sorted(market_plan.TYPE_QUERIES["wholesale"])
    assert task["query_estimate"]["terms"] == sorted(market_plan.TYPE_QUERIES["wholesale"])
    run = store.start_task(task["id"])
    assert run["status"] == "queued"
