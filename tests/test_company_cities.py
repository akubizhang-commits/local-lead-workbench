"""Pure company_cities rules (design §5.2, §5.6)."""
import json
import re
import sqlite3

import pytest

from leadworkbench.company_cities import city_row, first_markets, target_city_keys
from leadworkbench.regions import catalogue, index

# Census-style target names and the city their addresses use.
CENSUS = [
    ("US-PLACE-1836003", "indianapolis"), ("US-PLACE-4752006", "nashville"), ("US-PLACE-2148006", "louisville"),
    ("US-PLACE-1571550", "honolulu"), ("US-PLACE-2146027", "lexington"), ("US-PLACE-1303440", "athens"),
    ("US-PLACE-1304204", "augusta"), ("US-PLACE-3011397", "butte"), ("US-PLACE-0947515", "milford"),
    ("US-PLACE-1608830", "boise"), ("US-PLACE-0665042", "ventura"), ("US-PLACE-0622300", "paso robles"),
]


@pytest.mark.parametrize("place_id,postal", CENSUS)
def test_target_city_keys(place_id, postal):
    place = index()[place_id]
    assert postal in target_city_keys(place["name"], place["official_name"])


def test_target_city_keys_special_cases():
    greeley = "Greeley County unified government (balance)"
    assert target_city_keys(greeley, greeley) == {
        "greeley county unified government (balance)", "greeley county unified government", "greeley county"}
    san_jose = target_city_keys("San Jose", "San Jose city")
    assert san_jose == {"san jose", "san jose city"} and "campbell" not in san_jose
    assert "san buenaventura" in target_city_keys("San Buenaventura (Ventura)")
    assert target_city_keys("", None) == frozenset()


def test_every_census_style_catalogue_name_gets_a_postal_alias():
    names = [p for p in catalogue("US")["regions"]
             if p["kind"] == "place" and re.search(r"\(balance\)|government|^Urban ", p["name"])]
    assert names
    for place in names:
        keys = target_city_keys(place["name"], place["official_name"])
        assert any("balance" not in k and "government" not in k and not k.startswith("urban ") for k in keys), place


def company(**extra):
    return {"id": "c", "address": "515 Westchester Dr, Campbell, CA 95008, United States",
            "country": "US", "city": "San Jose", **extra}


def test_city_row_scopes_and_global_nearby():
    city, key, keys, scope, nearby, parsed = city_row(company(), {"scope": "whole_place", "official_name": "San Jose city"})
    assert (city, key, scope, nearby, parsed) == ("Campbell", "campbell", "city", 1, 1)
    assert json.loads(keys) == ["san jose", "san jose city"]
    assert city_row(company(), None)[3:5] == ("city", 1)
    municipality = city_row(company(), {"scope": "whole_municipality", "official_name": None})
    assert municipality[3:5] == ("municipality", 0)
    none = city_row(company(city=""), None)
    assert none[2:5] == ("[]", "none", 0)
    same = city_row(company(address="1 Main St, San Jose, CA 95112, United States"), None)
    assert same[4:] == (0, 1)
    unparsed = city_row(company(address="Somewhere"), None)
    assert unparsed[0] == "" and unparsed[4:] == (0, 0)


def test_first_markets_uses_one_query_for_many_companies():
    con = sqlite3.connect(":memory:")
    con.executescript("""CREATE TABLE market_memberships(market_id TEXT,company_id TEXT,first_seen TEXT);
        CREATE TABLE task_geography(task_id TEXT,payload TEXT);
        INSERT INTO market_memberships VALUES('za','a','2026-01-01'),('us','a','2026-02-01'),('us','b','2026-01-05');
        INSERT INTO task_geography VALUES('za','{"scope":"whole_municipality","official_name":"City of Cape Town"}');""")
    statements = []
    con.set_trace_callback(statements.append)
    result = first_markets(con, ["a", "b", "c"])
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1
    assert result == {"a": {"scope": "whole_municipality", "official_name": "City of Cape Town"},
                      "b": {"scope": None, "official_name": None}}
