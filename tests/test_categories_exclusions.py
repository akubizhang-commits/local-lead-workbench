"""P0-3: source categories, market type hints and exclusion rules (design §4.5)."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from leadworkbench.api import create_app
from leadworkbench.db import Conflict, Store
from leadworkbench.export import export_data
from leadworkbench.sources.maps import _record
from legacy_db import load_legacy


def listing(n, name, category):
    return _record(dict(title=name, link=f"https://www.google.com/maps/search/?api=1&query_place_id=P{n}",
                        place_id=f"P{n}", address=f"{n} Main St, San Jose, CA 95112", category=category),
                   f"2026-10-01T00:00:{n:02d}+00:00")


def market(store, keyword="roofing", types=("wholesale",)):
    task = store.create_market(dict(country="US", state="California", city="San Jose", keywords=[keyword],
                                    company_types=list(types), auto_enrich=False))
    return task, store.start_task(task["id"])


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    task, run = market(store)
    ids = {}
    for n, name, category in [(1, "Alpha Roofing Supply", "Roofing supply store"),
                              (2, "Beta Metals", "Metal supplier"),
                              (3, "Gamma Roofing Contractor", "Roofing contractor"),
                              (4, "Delta Builders (Plus)", "General contractor")]:
        ids[name.split()[0].lower()] = store.ingest_market(listing(n, name, category), run["id"], "roofing", task["id"])["id"]
    return store, task, run, ids


def names(page):
    return sorted(item["name"] for item in page["items"])


def test_categories_written_on_ingest_and_backfilled_on_upgrade(setup, tmp_path):
    store, task, run, ids = setup
    item = next(i for i in store.company_page()["items"] if i["id"] == ids["alpha"])
    assert item["categories"] == ["Roofing supply store"]
    legacy = tmp_path / "legacy"
    load_legacy(legacy)
    upgraded = Store(legacy)
    with upgraded.connect() as con:
        assert con.execute("SELECT count(*) FROM company_categories").fetchone()[0] == 5
        assert con.execute("SELECT count(*) FROM company_type_hints WHERE business_type='wholesaler'").fetchone()[0] == 5
    assert {c["category"] for c in upgraded.company_categories()} >= {"Roofing contractor", "Metal supplier"}


def test_category_filter_is_or_and_combines_with_other_filters(setup):
    store, task, run, ids = setup
    page = store.company_page(category=["Roofing supply store", "metal  SUPPLIER"])
    assert names(page) == ["Alpha Roofing Supply", "Beta Metals"]
    assert names(store.company_page(category=["Roofing supply store", "Metal supplier"], q="Beta")) == ["Beta Metals"]
    assert names(store.company_page(category=["Metal supplier"], market_id=task["id"], status="candidate")) == ["Beta Metals"]
    assert store.company_page(category=["Metal supplier"], status="verified")["total"] == 0


def test_type_hint_counts_until_a_real_business_type_exists(setup):
    store, task, run, ids = setup
    alpha = next(i for i in store.company_page()["items"] if i["id"] == ids["alpha"])
    assert alpha["type_hints"] == [dict(business_type="wholesaler", market_id=task["id"], market_name=task["name"])]
    assert store.company_page(type="wholesale")["total"] == 4
    company = store.company(ids["alpha"])
    store.update_company(ids["alpha"], company["revision"], {}, business_types=["retailer"])
    alpha = next(i for i in store.company_page()["items"] if i["id"] == ids["alpha"])
    assert alpha["type_hints"] == []
    assert store.company_page(type="wholesale")["total"] == 3
    assert names(store.company_page(type="retail")) == ["Alpha Roofing Supply"]


def test_exclusion_modes_and_totals(setup):
    store, task, run, ids = setup
    rule = store.create_exclusion_rule("global", "category", "Roofing contractor")
    page = store.company_page()
    assert page["total"] == 3 and page["excluded_total"] == 1
    only = store.company_page(excluded="only")
    assert names(only) == ["Gamma Roofing Contractor"]
    assert only["items"][0]["exclusion"] == dict(rule_id=rule["id"], kind="category", value="Roofing contractor",
                                                  scope="global")
    assert store.company_page(excluded="include")["total"] == 4
    assert store.company_page(q="Gamma")["excluded_total"] == 1
    assert store.company_page(q="Beta")["excluded_total"] == 0
    with store.connect() as con:
        assert con.execute("SELECT status FROM companies WHERE id=?", (ids["gamma"],)).fetchone()[0] == "candidate"
    store.delete_exclusion_rule(rule["id"])
    assert store.company_page()["total"] == 4


def test_market_rule_scope_with_shared_company(setup):
    store, task, run, ids = setup
    other, other_run = market(store, keyword="metal")
    store.ingest_market(listing(2, "Beta Metals", "Metal supplier"), other_run["id"], "metal", other["id"])
    store.create_exclusion_rule("market", "keyword", "metals", other["id"])
    assert "Beta Metals" in names(store.company_page(market_id=task["id"]))
    assert "Beta Metals" not in names(store.company_page(market_id=other["id"]))
    # Not viewing a market: rules of any market the company belongs to apply.
    assert "Beta Metals" not in names(store.company_page())
    assert store.task(other["id"])["market_stats"]["excluded"] == 1
    assert store.task(task["id"])["market_stats"]["excluded"] == 0


def test_category_rule_wins_and_earliest_rule_within_kind(setup):
    store, task, run, ids = setup
    first_keyword = store.create_exclusion_rule("global", "keyword", "roofing")
    store.create_exclusion_rule("global", "keyword", "contractor")
    category = store.create_exclusion_rule("global", "category", "Roofing contractor")
    rows = {i["name"]: i["exclusion"]["rule_id"] for i in store.company_page(excluded="only")["items"]}
    assert rows["Gamma Roofing Contractor"] == category["id"]
    assert rows["Alpha Roofing Supply"] == first_keyword["id"]
    assert set(rows) == {"Gamma Roofing Contractor", "Alpha Roofing Supply", "Delta Builders (Plus)"}


def test_export_and_category_counts_follow_exclusions(setup):
    store, task, run, ids = setup
    store.create_exclusion_rule("global", "category", "Roofing contractor")
    rows = list(csv.reader(io.StringIO(export_data(store, "csv").decode("utf-8-sig"))))
    assert sorted(r[1] for r in rows[1:]) == ["Alpha Roofing Supply", "Beta Metals", "Delta Builders (Plus)"]
    counts = {c["category"]: c["count"] for c in store.company_categories()}
    assert "Roofing contractor" not in counts and counts["Metal supplier"] == 1
    assert {c["category"]: c["count"] for c in store.company_categories(excluded="include")}["Roofing contractor"] == 1


def test_keyword_special_characters_are_literal(setup):
    store, task, run, ids = setup
    store.create_exclusion_rule("global", "keyword", "(plus)")
    assert names(store.company_page(excluded="only")) == ["Delta Builders (Plus)"]
    store.create_exclusion_rule("global", "keyword", "a+b")
    assert store.company_page(excluded="only")["total"] == 1


def test_rule_validation_and_idempotency(setup):
    store, task, run, ids = setup
    first = store.create_exclusion_rule("global", "keyword", "Roofing  Contractor")
    again = store.create_exclusion_rule("global", "keyword", "roofing contractor")
    assert (again["id"], again["reused"], again["matched"]) == (first["id"], True, 1)
    with pytest.raises(ValueError, match="至少 2 个字符"):
        store.create_exclusion_rule("global", "keyword", "x")
    with pytest.raises(KeyError):
        store.create_exclusion_rule("market", "keyword", "metal", "missing")
    with store.connect() as con:
        con.executemany("INSERT INTO exclusion_rules VALUES(?,?,?,?,?,?,?)",
                        [(f"r{i}", "global", None, "keyword", f"kw{i}", f"kw{i}", "2026") for i in range(199)])
    with pytest.raises(Conflict, match="上限 200 条"):
        store.create_exclusion_rule("global", "keyword", "another")
    with pytest.raises(KeyError):
        store.delete_exclusion_rule("missing")


def test_api_endpoints(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        store = app.state.store
        task, run = market(store)
        store.ingest_market(listing(1, "Alpha Roofing Supply", "Roofing supply store"), run["id"], "roofing", task["id"])
        created = client.post("/api/exclusion-rules", json={"scope": "market", "market_id": task["id"],
                                                             "kind": "category", "value": "Roofing supply store"})
        assert created.status_code == 201 and created.json()["matched"] == 1
        assert client.post("/api/exclusion-rules", json={"scope": "market", "market_id": task["id"], "kind": "category",
                                                         "value": "roofing supply store"}).status_code == 200
        assert client.post("/api/exclusion-rules", json={"scope": "global", "market_id": task["id"], "kind": "keyword",
                                                         "value": "roof"}).status_code == 422
        assert client.post("/api/exclusion-rules", json={"scope": "market", "kind": "keyword",
                                                         "value": "roof"}).status_code == 422
        assert client.post("/api/exclusion-rules", json={"scope": "market", "market_id": "missing", "kind": "keyword",
                                                         "value": "roof"}).status_code == 404
        assert client.post("/api/exclusion-rules", json={"scope": "global", "kind": "keyword",
                                                         "value": "x"}).status_code == 400
        assert client.post("/api/exclusion-rules", json={"scope": "global", "kind": "keyword", "value": "a\x01b"}).status_code == 422
        assert client.post("/api/exclusion-rules", json={"scope": "global", "kind": "keyword", "value": "ok",
                                                         "extra": 1}).status_code == 422
        rules = client.get("/api/exclusion-rules", params={"market_id": task["id"]}).json()
        assert [r["matched"] for r in rules] == [1]
        page = client.get("/api/company-page", params={"market_id": task["id"]}).json()
        assert page["total"] == 0 and page["excluded_total"] == 1
        page = client.get("/api/company-page", params={"excluded": "include", "category": ["Roofing supply store"]}).json()
        assert page["total"] == 1 and page["items"][0]["categories"] == ["Roofing supply store"]
        assert client.get("/api/company-page", params={"category": ["x"] * 21}).status_code == 422
        assert client.get("/api/company-page", params={"category": ["x" * 121]}).status_code == 422
        assert client.get("/api/company-page", params={"excluded": "nope"}).status_code == 422
        assert client.get("/api/company-categories", params={"excluded": "include"}).json()[0]["count"] == 1
        assert client.delete(f"/api/exclusion-rules/{rules[0]['id']}").status_code == 204
        assert client.delete(f"/api/exclusion-rules/{rules[0]['id']}").status_code == 404
