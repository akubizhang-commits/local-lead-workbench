"""Actual city of a company and its nearby-city flag (P1-18, design §5.2).

Pure functions plus the batch refresh of the derived ``company_cities`` table
(the same dirty-table pattern as contact_facets).
"""
import json
import logging
import re

from .address_city import city_from_address
from .market_store import normalized

log = logging.getLogger(__name__)
# Census LSAD words: the first match is removed once, never a bare " county".
LSAD = (" unified government", " consolidated government", " metropolitan government", " metro government",
        " urban county", " city", " town", " village", " cdp")


def _name_keys(name):
    keys = set()
    n = normalized(name)
    if not n:
        return keys
    keys.add(n)
    if n.endswith(" (balance)"):
        n = n[:-len(" (balance)")].strip()
        keys.add(n)
    for suffix in LSAD:
        if n.endswith(suffix) and n[:-len(suffix)].strip():
            n = n[:-len(suffix)].strip()
            keys.add(n)
            break
    if n.startswith("urban ") and n[len("urban "):].strip():
        n = n[len("urban "):].strip()
        keys.add(n)
    match = re.fullmatch(r"(.+?)\s*\((.+)\)", n)
    if match:
        n = match[1].strip()
        keys.update({n, match[2].strip()})
    keys.add(re.split(r"\s*[-/]\s*", n)[0].strip())
    return {key for key in keys if key}


def target_city_keys(city, official_name=None) -> frozenset:
    """Every postal-style name a target city may appear as in addresses (design §5.2 steps 1–6)."""
    return frozenset(_name_keys(city) | _name_keys(official_name))


def city_row(company, first_market):
    """(city, city_key, target_keys JSON, target_scope, nearby_global, parsed) of one company.

    first_market is {scope, official_name} of the market that first found the company, or None."""
    parsed_city = city_from_address(company["address"], company["country"]) or ""
    city_key = normalized(parsed_city)
    if not normalized(company["city"]):
        scope, keys = "none", []
    else:
        market = first_market or {}
        scope = "municipality" if market.get("scope") == "whole_municipality" else "city"
        keys = sorted(target_city_keys(company["city"], market.get("official_name")))
    parsed = 1 if parsed_city else 0
    nearby = int(bool(parsed and scope == "city" and keys and city_key not in keys))
    return parsed_city, city_key, json.dumps(keys), scope, nearby, parsed


FAILED_ROW = ("", "", "[]", "none", 0, 0)


def first_markets(con, company_ids, chunk=5000):
    """{company_id: {scope, official_name}} of each company's first market, one query per chunk (R3-3)."""
    result = {}
    ids = list(company_ids)
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        rows = con.execute(f"""SELECT m.company_id,g.payload FROM market_memberships m
            LEFT JOIN task_geography g ON g.task_id=m.market_id
            WHERE m.company_id IN ({','.join('?' * len(part))})
            ORDER BY m.company_id,m.first_seen,m.rowid""", part)
        for company_id, payload in rows:
            if company_id in result:
                continue
            try:
                geography = json.loads(payload) if payload else {}
            except ValueError:
                geography = {}
            geography = geography if isinstance(geography, dict) else {}
            result[company_id] = {"scope": geography.get("scope"), "official_name": geography.get("official_name")}
    return result


def compute_rows(con, companies):
    """[(company_id, *city_row)] for company rows (dicts or sqlite3.Row with id/address/country/city)."""
    markets = first_markets(con, [c["id"] for c in companies])
    rows = []
    for company in companies:
        try:
            values = city_row(company, markets.get(company["id"]))
        except Exception:
            log.warning("company city failed: %s", company["id"], exc_info=True,
                        extra={"event": "company_city_failed"})
            values = FAILED_ROW
        rows.append((company["id"], *values))
    return rows


def refresh_rows(con, company_ids):
    """Recompute and store company_cities rows for these companies (inside the caller's transaction)."""
    ids = list(company_ids)
    companies = []
    for start in range(0, len(ids), 5000):
        part = ids[start:start + 5000]
        companies += con.execute(f"SELECT id,address,country,city FROM companies WHERE id IN ({','.join('?' * len(part))})",
                                 part).fetchall()
    rows = compute_rows(con, companies)
    con.executemany("""INSERT OR REPLACE INTO company_cities(company_id,city,city_key,target_keys,target_scope,
        nearby_global,parsed) VALUES(?,?,?,?,?,?,?)""", rows)
    return len(rows)
