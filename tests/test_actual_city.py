"""P1-18: actual city and nearby-city flags (design §5.2–§5.6)."""
import csv
import io

from fastapi.testclient import TestClient
import pytest

from leadworkbench.address_city import city_from_address
from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.export import export_data
from leadworkbench.regions import catalogue, index

SAN_JOSE = "US-PLACE-0668000"
CAMPBELL = "515 Westchester Dr, Campbell, CA 95008, United States"
CENSUS = [
    ("US-PLACE-1836003", "100 N Senate Ave, Indianapolis, IN 46204, United States"),
    ("US-PLACE-4752006", "1 Broadway, Nashville, TN 37203, United States"),
    ("US-PLACE-2148006", "1 Main St, Louisville, KY 40202, United States"),
    ("US-PLACE-1571550", "1 Bishop St, Honolulu, HI 96813, United States"),
    ("US-PLACE-2146027", "1 Main St, Lexington, KY 40507, United States"),
    ("US-PLACE-1303440", "1 Broad St, Athens, GA 30601, United States"),
    ("US-PLACE-1304204", "1 Broad St, Augusta, GA 30901, United States"),
    ("US-PLACE-3011397", "1 Main St, Butte, MT 59701, United States"),
    ("US-PLACE-0947515", "1 River St, Milford, CT 06460, United States"),
    ("US-PLACE-1608830", "1 Main St, Boise, ID 83702, United States"),
    ("US-PLACE-0665042", "1 Main St, Ventura, CA 93001, United States"),
    ("US-PLACE-0622300", "1 Spring St, Paso Robles, CA 93446, United States"),
]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def place_market(store, place_id, **extra):
    place = index()[place_id]
    state = index()[place["state_id"]]
    return store.create_market(dict(country=place["state_id"][:2], state=state["name"], city=place["name"],
                                    state_id=state["id"], place_id=place_id, keywords=["metal supplier"],
                                    company_types=["wholesale"], auto_enrich=False, **extra))


def za_market(store):
    p = next(r for r in catalogue("ZA")["regions"] if r.get("subtype") == "metropolitan_municipality")
    return store.create_market(dict(country="ZA", state=index()[p["state_id"]]["name"], city=p["name"],
                                    state_id=p["state_id"], place_id=p["id"], keywords=["metal supplier"],
                                    company_types=["wholesale"], auto_enrich=False))


def found(store, market, n, address, **extra):
    run = store.start_task(market["id"])
    company = store.ingest_market({"source_key": f"maps:{n}", "source_url": f"https://maps.example/{n}",
                                   "name": f"Fixture {n}", "address": address, "phone": f"1408555{n:04d}",
                                   "observed_at": "2026-10-18T01:00:00+00:00", **extra}, run["id"], "q", market["id"])
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])
    return company["id"]


def dirty(store):
    with store.connect() as con:
        return {r[0] for r in con.execute("SELECT company_id FROM company_cities_dirty")}


def city(store, company_id):
    with store.connect() as con:
        return dict(con.execute("SELECT * FROM company_cities WHERE company_id=?", (company_id,)).fetchone())


def refreshed(store, company_id):
    store.company(company_id)  # every read refreshes dirty rows first
    return city(store, company_id)


def test_writes_mark_rows_dirty_and_reads_refresh_them(store):
    company = store.manual_company("Manual", "https://manual.example", city="San Jose")
    assert company["actual_city"] == "San Jose" and company["actual_city_basis"] == "target"
    assert dirty(store) == set()
    # update_company returns the detail, whose read refreshes the dirty row at once.
    detail = store.update_company(company["id"], company["revision"], {"address": CAMPBELL})
    assert (detail["actual_city"], detail["actual_city_basis"], detail["nearby"]) == ("Campbell", "address", True)
    assert city(store, company["id"])["city"] == "Campbell"
    for column, value in (("address", "1 Main St, San Jose, CA 95112, United States"), ("country", "ZA"),
                          ("city", "Campbell")):
        with store.connect() as con:
            con.execute(f"UPDATE companies SET {column}=? WHERE id=?", (value, company["id"]))
        assert company["id"] in dirty(store)
        store.company_page()
        assert company["id"] not in dirty(store)
    assert city(store, company["id"])["parsed"] == 0  # ZA address format does not parse a US address


@pytest.mark.parametrize("place_id,address", CENSUS)
def test_census_style_targets_are_not_nearby(store, place_id, address):
    market = place_market(store, place_id)
    company = found(store, market, 1, address)
    row = store.company_page(market_id=market["id"])["items"][0]
    assert row["id"] == company and row["nearby"] is False and row["actual_city_basis"] == "address"
    assert store.company_page()["items"][0]["nearby"] is False
    assert store.task(market["id"])["market_stats"]["nearby"] == 0
    assert city(store, company)["nearby_global"] == 0


def test_campbell_in_a_san_jose_market_is_nearby(store):
    market = place_market(store, SAN_JOSE)
    near = found(store, market, 1, CAMPBELL)
    same = found(store, market, 2, "1 Main St, San Jose, CA 95112, United States")
    unparsed = found(store, market, 3, "Somewhere without a postcode")
    page = {r["id"]: r for r in store.company_page(market_id=market["id"])["items"]}
    assert (page[near]["actual_city"], page[near]["nearby"]) == ("Campbell", True)
    assert page[same]["nearby"] is False
    assert (page[unparsed]["actual_city"], page[unparsed]["actual_city_basis"], page[unparsed]["nearby"]) == (
        "San Jose", "target", False)
    assert store.task(market["id"])["market_stats"]["nearby"] == 1
    assert [r["id"] for r in store.company_page(market_id=market["id"], nearby="only")["items"]] == [near]
    assert {r["id"] for r in store.company_page(market_id=market["id"], nearby="hide")["items"]} == {same, unparsed}
    assert [r["id"] for r in store.company_page(actual_city=" campbell ")["items"]] == [near]
    assert [r["id"] for r in store.company_page(nearby="only")["items"]] == [near]


def test_global_counterexamples(store):
    za = za_market(store)
    municipal = found(store, za, 1, "1 Oxford St, East London, 5201, South Africa")
    row = refreshed(store, municipal)
    assert (row["target_scope"], row["nearby_global"], row["city"]) == ("municipality", 0, "East London")
    assert store.company_page(market_id=za["id"])["items"][0]["nearby"] is False
    blank = store.manual_company("Blank city", "https://blank.example")
    store.update_company(blank["id"], blank["revision"], {"address": CAMPBELL})
    row = refreshed(store, blank["id"])
    assert (row["target_scope"], row["nearby_global"], row["parsed"]) == ("none", 0, 1)
    typed = store.manual_company("Typed city", "https://typed.example", city="San Jose")
    store.update_company(typed["id"], typed["revision"], {"address": CAMPBELL})
    row = refreshed(store, typed["id"])
    assert (row["target_scope"], row["nearby_global"]) == ("city", 1)


def test_first_market_decides_the_scope(store):
    za = za_market(store)
    company = found(store, za, 1, "1 Oxford St, East London, 5201, South Africa")
    store.company(company)
    other = store.create_market(dict(country="ZA", state="Eastern Cape", city="East London", keywords=["x"],
                                     company_types=[], auto_enrich=False))
    run = store.start_task(other["id"])
    store.ingest_market({"source_key": "maps:1", "source_url": "https://maps.example/1", "name": "Fixture 1",
                         "address": "1 Oxford St, East London, 5201, South Africa", "phone": "14085550001",
                         "observed_at": "2026-10-19T01:00:00+00:00"}, run["id"], "q", other["id"])
    assert company in dirty(store)
    store.company(company)
    assert city(store, company)["target_scope"] == "municipality"


def test_missing_rows_are_computed_for_display_only(store, monkeypatch):
    market = place_market(store, SAN_JOSE)
    near = found(store, market, 1, CAMPBELL)
    store.company_page()
    with store.connect() as con:
        con.execute("DELETE FROM company_cities WHERE company_id=?", (near,))
    monkeypatch.setattr(Store, "_refresh_cities_locked", lambda self, con, limit=None: 0)
    row = store.company_page(market_id=market["id"])["items"][0]
    assert (row["actual_city"], row["nearby"]) == ("Campbell", True)
    assert store.company(near)["actual_city"] == "Campbell"
    with store.connect() as con:
        assert not con.execute("SELECT 1 FROM company_cities WHERE company_id=?", (near,)).fetchone()
    assert store.company_page(actual_city="Campbell")["items"] == []
    assert store.company_page(market_id=market["id"], nearby="only")["items"] == []
    assert [r["id"] for r in store.company_page(market_id=market["id"], nearby="hide")["items"]] == [near]


def test_filters_combine_with_category_and_exclusions(store):
    market = place_market(store, SAN_JOSE)
    a = found(store, market, 1, CAMPBELL, category="Metal supplier")
    b = found(store, market, 2, "2 Hamilton Ave, Campbell, CA 95008, United States", category="Roofing contractor")
    found(store, market, 3, "1 Main St, San Jose, CA 95112, United States", category="Metal supplier")
    assert [r["id"] for r in store.company_page(market_id=market["id"], nearby="only",
                                                category=["Metal supplier"])["items"]] == [a]
    store.create_exclusion_rule("market", "category", "Roofing contractor", market["id"])
    assert [r["id"] for r in store.company_page(market_id=market["id"], nearby="only")["items"]] == [a]
    assert [r["id"] for r in store.company_page(market_id=market["id"], nearby="only", excluded="only")["items"]] == [b]
    cities = store.company_cities(market_id=market["id"])
    assert cities == [{"city": "Campbell", "city_key": "campbell", "count": 1, "nearby": True},
                      {"city": "San Jose", "city_key": "san jose", "count": 1, "nearby": False}]
    included = {c["city_key"]: c["count"] for c in store.company_cities(market_id=market["id"], excluded="include")}
    assert included == {"campbell": 2, "san jose": 1}
    assert {c["city_key"] for c in store.company_cities()} == {"campbell", "san jose"}


def test_exports_filter_nearby_and_read_the_stored_city(store):
    market = place_market(store, SAN_JOSE)
    near = found(store, market, 1, CAMPBELL, website="https://near.example")
    found(store, market, 2, "1 Main St, San Jose, CA 95112, United States", website="https://same.example")
    found(store, market, 3, "Unparsed address", website="https://unparsed.example")
    rows = list(csv.DictReader(io.StringIO(export_data(store, "csv", "companies", market_id=market["id"],
                                                       nearby="only").decode("utf-8-sig"))))
    assert [r["id"] for r in rows] == [near]
    entries = list(csv.reader(io.StringIO(export_data(store, "csv", "entries", market_id=market["id"]).decode("utf-8-sig"))))
    header = entries[0]
    position = header.index("实际城市")
    companies = {c["name"]: c for c in store.company_page(market_id=market["id"])["items"]}
    name = header.index("企业名称")
    for line in entries[1:]:
        company = companies[line[name]]
        assert line[position] == (city_from_address(company["address"], company["country"]) or company["city"])


def test_api_parameters(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        assert client.get("/api/company-page", params={"nearby": "near"}).status_code == 422
        assert client.get("/api/company-page", params={"actual_city": "x" * 121}).status_code == 422
        assert client.get("/api/company-page", params={"nearby": "only", "actual_city": "Campbell"}).json()["total"] == 0
        assert client.get("/api/company-cities").json() == []
        body = {"format": "csv", "layout": "entries", "nearby": "far"}
        assert client.post("/api/export-jobs", json=body).status_code == 422
