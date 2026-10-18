"""Performance regression on synthetic data (design §2.2, decision 1).

Default: 5k companies / 25k contacts with 2x budgets, to catch order-of-magnitude
regressions. LEAD_PERF=1: 20k / 100k with the hard budgets (page < 1 s, reviews
< 1 s, CSV < 30 s; XLSX recorded only) plus first-start migration timing on a
database built by the 9423c53 code. LEAD_PERF_OUT=<path> writes the timings.
"""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

from leadworkbench.db import Store
from leadworkbench.export import export_data
from legacy_db import legacy_code_tmp
from perf_fixtures import best_of, build

FULL = os.environ.get("LEAD_PERF") == "1"
COMPANIES, CONTACTS = (20000, 5) if FULL else (5000, 5)
PAGE_BUDGET, REVIEW_BUDGET, CSV_BUDGET = (1.0, 1.0, 30.0) if FULL else (2.0, 2.0, 20.0)
RESULTS = {}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    start = time.perf_counter()
    built = build(tmp_path_factory.mktemp("perf"), COMPANIES, CONTACTS)
    RESULTS["build_seconds"] = round(time.perf_counter() - start, 2)
    start = time.perf_counter()
    built._refresh_facets()
    RESULTS["first_facet_refresh_seconds"] = round(time.perf_counter() - start, 2)
    yield built
    out = os.environ.get("LEAD_PERF_OUT")
    if out:
        Path(out).write_text(json.dumps({"companies": COMPANIES, "contacts": COMPANIES * CONTACTS,
                                         "seconds": RESULTS}, ensure_ascii=False, indent=1), encoding="utf-8")


PAGES = {
    "page_newest": {},
    "page_procurement": {"sort": "procurement"},
    "page_email": {"sort": "email"},
    "page_contact_procurement": {"contact": "procurement"},
    "page_q": {"q": "Supply"},
    "page_market": {"market_id": "market0"},
}


@pytest.mark.parametrize("name", list(PAGES))
def test_company_page_budget(store, name):
    seconds = best_of(lambda: store.company_page(page=1, page_size=50, **PAGES[name]))
    RESULTS[name] = seconds
    assert seconds < PAGE_BUDGET, (name, seconds)


def test_reviews_budget(store):
    seconds = best_of(store.reviews)
    RESULTS["reviews"] = seconds
    assert seconds < REVIEW_BUDGET


def test_csv_export_budget(store):
    seconds = best_of(lambda: export_data(store, "csv"), 1 if not FULL else 3)
    RESULTS["csv"] = seconds
    assert seconds < CSV_BUDGET


def test_xlsx_export_recorded(store):
    # Recorded only (decision 1: XLSX is not a hard budget).
    RESULTS["xlsx"] = best_of(lambda: export_data(store, "xlsx"), 3 if FULL else 1)


def test_page_with_exclusion_rules(store):
    with store.connect() as con:
        con.executemany("INSERT INTO exclusion_rules VALUES(?,?,?,?,?,?,?)",
                        [(f"perf-k{i}", "global", None, "keyword", f"zzkeyword{i}", f"zzkeyword{i}", "2026")
                         for i in range(29)] + [("perf-k29", "global", None, "keyword", "company 1", "company 1", "2026")]
                        + [(f"perf-c{i}", "global" if i % 2 else "market", None if i % 2 else "market0", "category",
                            f"cat{i}", " ".join(c.casefold().split()), "2026")
                           for i, c in enumerate(["Roofing contractor", "Hardware store", "Lumber store",
                                                  "Paint store", "Tile store", "Door supplier", "Window supplier",
                                                  "General contractor", "Siding contractor", "Concrete contractor"])])
    try:
        seconds = best_of(lambda: store.company_page(page=1, page_size=50))
        RESULTS["page_with_40_rules"] = seconds
        RESULTS["page_with_40_rules_market"] = best_of(lambda: store.company_page(market_id="market0"))
        assert store.company_page()["excluded_total"] > 0
        assert seconds < PAGE_BUDGET and RESULTS["page_with_40_rules_market"] < PAGE_BUDGET
    finally:
        with store.connect() as con:
            con.execute("DELETE FROM exclusion_rules WHERE id LIKE 'perf-%'")


def test_first_start_migration_on_9423c53_database(tmp_path, caplog):
    code = legacy_code_tmp()
    if code is None:
        pytest.skip("git unavailable")
    try:
        target = tmp_path / "legacy"
        env = {**os.environ, "PYTHONPATH": f"{code}{os.pathsep}{Path(__file__).parent}", "LEAD_DATA_DIR": str(tmp_path)}
        env.pop("MAPS_SCRAPER_BIN", None)
        subprocess.run([sys.executable, str(Path(__file__).parent / "perf_fixtures.py"), str(target),
                        str(COMPANIES), str(CONTACTS), "legacy"], env=env, cwd=code, check=True)
    finally:
        shutil.rmtree(code, ignore_errors=True)
    caplog.set_level(logging.INFO, logger="leadworkbench.p0_migrations")
    start = time.perf_counter()
    Store(target)
    total = time.perf_counter() - start
    steps = [float(m[1]) for r in caplog.records if (m := re.search(r"用时 ([\d.]+)s", r.getMessage()))]
    RESULTS["first_start_total"] = round(total, 2)
    RESULTS["first_start_longest_transaction"] = max(steps)
    assert total < 15 and max(steps) < 3, (total, steps)
    with Store(target).connect() as con:
        assert con.execute("SELECT count(*) FROM contact_facets").fetchone()[0] == COMPANIES * CONTACTS
