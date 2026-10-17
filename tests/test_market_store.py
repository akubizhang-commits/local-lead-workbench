"""Market persistence tests use synthetic records and temporary SQLite files."""
import json
import sqlite3

import pytest

from leadworkbench.db import Store


def definition(**extra):
    return dict(name="Fixture Market", country="US", state="California", city="Los Angeles",
                keywords=["clothing wholesaler"], company_types=["wholesale"], auto_enrich=False, **extra)


def record(n=0, **extra):
    return {
        "source_key": f"maps:{n}", "source_url": f"https://maps.example/{n}",
        "name": f"Fixture {n}", "address": f"{n + 1} Example Street, Los Angeles, CA 90001",
        "phone": f"1212555{n:04d}", "website": f"https://fixture{n}.example",
        "observed_at": "2026-09-22T01:00:00+00:00", **extra,
    }


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def market_run(store, **extra):
    market = store.create_market(definition(**extra))
    return market, store.start_task(market["id"])


def end_run(store, run):
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])


def test_market_identity_planning_jobs_and_resume(store):
    market, run = market_run(store)
    payload = definition()
    payload.update(name="Different name", city="  LOS   ANGELES ", keywords=["Clothing Wholesaler", "clothing wholesaler"])
    assert store.create_market(payload)["id"] == market["id"]
    assert market["limit"] == 0 and market["mode"] == "market"
    item = store.claim_item(run["id"])
    assert item["stage"] == "planning" and item["target"] == market["id"]
    job = {"query": "fixture", "bounds": [1, 2, 3, 4], "key": "cell-1"}
    store.save_market_plan(run["id"], {"jobs": [job], "scope_label": "Fixture city"})
    store.append_market_jobs(run["id"], [dict(reversed(list(job.items()))), job])
    assert store.count_market_jobs(run["id"]) == 1
    assert store.run(run["id"])["snapshot"]["market_plan"]["scope_label"] == "Fixture city"
    store.add_enrichment(run["id"], store.manual_company("Manual", "https://fixture.example")["id"])
    store.finish_item(item["id"], "succeeded", {})
    assert store.claim_item(run["id"])["stage"] == "discovery"
    store.control(run["id"], "pause")
    store.control(run["id"], "resume")
    assert store.count_market_jobs(run["id"]) == 1


def test_market_progress_present_before_and_after_processing(store):
    market, run = market_run(store)
    for key in ("new", "existing", "changed", "market_total", "discovery_done", "discovery_total"):
        assert run["progress"][key] == 0
    store.save_market_plan(run["id"], {"jobs": [{"key": "one", "query": "fixture"}]})
    assert store.task(market["id"])["latest_run"]["progress"]["discovery_total"] == 1
    end_run(store, run)
    final = store.run(run["id"])["progress"]
    assert final["done"] == final["total"] == 2
    assert final["discovery_done"] == 1


def test_two_runs_membership_changed_not_seen_and_manual_preservation(store):
    market, first = market_run(store)
    a = store.ingest_market(record(), first["id"], "fixture", market["id"])
    store.ingest_market(record(1), first["id"], "fixture", market["id"])
    assert store.ingest_market(record(), first["id"], "again", market["id"])["_outcome"] == "new"
    end_run(store, first)
    second = store.start_task(market["id"])
    current = store.company(a["id"])
    store.update_company(a["id"], current["revision"], {"phone": "manual"}, "keep", "verified")
    changed = store.ingest_market(record(phone="19999999999", address="100 New Street, Los Angeles, CA 90001"),
                                  second["id"], "fixture", market["id"])
    assert changed["id"] == a["id"]
    assert changed["_outcome"] == "existing" and changed["_changed"]
    assert changed["phone"] == "manual" and changed["notes"] == "keep"
    assert changed["address"].startswith("100 New Street")
    assert any(r["field"] == "phone" for r in store.reviews())
    store.ingest_market(record(2), second["id"], "fixture", market["id"])
    store.control(second["id"], "pause")
    store.control(second["id"], "resume")
    store.ingest_market(record(2), second["id"], "again", market["id"])
    progress = store.progress(second["id"])
    assert (progress["new"], progress["existing"], progress["changed"], progress["market_total"]) == (1, 1, 1, 3)
    assert store.company_page(market_id=market["id"], segment="not_seen")["items"][0]["id"] != a["id"]
    assert store.company_page(market_id=market["id"], segment="changed")["total"] == 1
    assert store.company_page(market_id=market["id"], run_id=first["id"], segment="new")["total"] == 2
    assert store.task(market["id"])["market_stats"]["not_seen"] == 1


def test_cross_market_new_is_local_and_strong_identity_keeps_branches(store):
    market, run = market_run(store)
    first = store.ingest_market(record(), run["id"], "fixture", market["id"])
    other_payload = definition()
    other_payload["city"] = "San Diego"
    other = store.create_market(other_payload)
    other_run = store.start_task(other["id"])
    found = store.ingest_market(record(), other_run["id"], "fixture", other["id"])
    assert found["id"] == first["id"] and found["_outcome"] == "new"
    same = store.ingest_market(record(source_key="maps:alternate"), run["id"], "fixture", market["id"])
    assert same["id"] == first["id"]
    branch = store.ingest_market(record(source_key="maps:branch", address="200 Other Road, Los Angeles, CA 90002"),
                                 run["id"], "fixture", market["id"])
    assert branch["id"] != first["id"]
    weak = store.ingest_market(record(source_key="maps:weak", address="", phone=""),
                               run["id"], "fixture", market["id"])
    assert weak["id"] != first["id"]
    assert store.company_page(market_id=other["id"])["total"] == 1


def test_enrichment_material_changes_only_once_per_run(store):
    market, first = market_run(store)
    company = store.ingest_market(record(), first["id"], "fixture", market["id"])
    end_run(store, first)
    run = store.start_task(market["id"])
    store.ingest_market(record(), run["id"], "fixture", market["id"])
    assert store.progress(run["id"])["changed"] == 0
    payload = {"status": "completed", "contacts": [{
        "name": "Alex", "role": "Buyer", "email": "alex@example.org", "kind": "procurement",
        "role_status": "explicit", "association_status": "same_block",
        "source_url": "https://fixture.example/team", "evidence": "Alex Buyer alex@example.org",
    }]}
    store.apply_enrichment(company["id"], payload, run["id"])
    store.apply_enrichment(company["id"], payload, run["id"])
    assert store.progress(run["id"])["changed"] == 1
    end_run(store, run)
    third = store.start_task(market["id"])
    store.ingest_market(record(), third["id"], "fixture", market["id"])
    payload["contacts"][0]["observed_at"] = "2026-10-01T00:00:00+00:00"
    store.apply_enrichment(company["id"], payload, third["id"])
    assert store.progress(third["id"])["changed"] == 0


def test_cross_source_phone_formats_and_page_types_do_not_churn_each_round(store):
    market, first = market_run(store)
    company = store.ingest_market(record(phone="+1 212 555 0100"), first["id"], "fixture", market["id"])
    payload = {"status": "completed", "observations": [
        {"field": "phone", "value": "+1(212)-555-0100", "source_url": "https://fixture.example/contact",
         "evidence": "Public phone"},
        {"field": "business_types", "value": ["wholesaler"], "source_url": "https://fixture.example/about",
         "evidence": "Wholesale"},
        {"field": "business_types", "value": ["retailer", "wholesaler"], "source_url": "https://fixture.example/contact",
         "evidence": "Retail and wholesale"},
    ]}
    store.apply_enrichment(company["id"], payload, first["id"])
    end_run(store, first)
    second = store.start_task(market["id"])
    store.ingest_market(record(phone="+12125550100", source_url="https://maps.example/0?hl=en&utm_source=x#card"),
                        second["id"], "fixture", market["id"])
    payload["observations"][-1]["value"] = ["wholesaler", "retailer"]
    store.apply_enrichment(company["id"], payload, second["id"])
    assert store.progress(second["id"])["changed"] == 0
    assert set(store.company(company["id"])["business_types"]) == {"retailer", "wholesaler"}
    payload["observations"][0]["value"] = "+12125550101"
    store.apply_enrichment(company["id"], payload, second["id"])
    assert store.progress(second["id"])["changed"] == 1


def test_new_source_baseline_and_website_spelling_are_not_material_changes(store):
    market, first = market_run(store)
    company = store.ingest_market(record(website="https://www.fixture.example/", business_types=["retailer"]),
                                  first["id"], "fixture", market["id"])
    end_run(store, first)
    second = store.start_task(market["id"])
    store.ingest_market(record(website="http://fixture.example"), second["id"], "fixture", market["id"])
    store.apply_enrichment(company["id"], {
        "status": "completed", "observations": [{
            "field": "business_types", "value": ["wholesaler"], "source_url": "https://fixture.example/about",
            "evidence": "Wholesale",
        }],
    }, second["id"])
    assert store.progress(second["id"])["changed"] == 0


def test_existing_empty_phone_fill_is_changed_but_initial_new_company_fill_is_not(store):
    market, first = market_run(store)
    existing = store.ingest_market(record(phone=""), first["id"], "fixture", market["id"])
    new = store.ingest_market(record(1, phone=""), first["id"], "fixture", market["id"])
    payload = {"status": "completed", "observations": [{
        "field": "phone", "value": "12125550100", "source_url": "https://fixture.example/contact", "evidence": "Public phone",
    }]}
    store.apply_enrichment(new["id"], payload, first["id"])
    assert store.progress(first["id"])["changed"] == 0
    end_run(store, first)
    second = store.start_task(market["id"])
    updated = store.ingest_market(record(), second["id"], "fixture", market["id"])
    assert updated["id"] == existing["id"] and updated["_changed"] is True
    assert store.progress(second["id"])["changed"] == 1
    store.ingest_market(record(), second["id"], "fixture", market["id"])
    assert store.progress(second["id"])["changed"] == 1


def test_constant_disagreeing_sources_do_not_count_a_change_every_round(store):
    market, first = market_run(store)
    company = store.ingest_market(record(phone="1111111111"), first["id"], "fixture", market["id"])
    payload = {"status": "completed", "observations": [{
        "field": "phone", "value": "2222222222", "source_url": "https://fixture.example/contact",
        "evidence": "Published phone 2222222222",
    }]}
    store.apply_enrichment(company["id"], payload, first["id"])
    end_run(store, first)
    second = store.start_task(market["id"])
    store.ingest_market(record(phone="1111111111"), second["id"], "fixture", market["id"])
    store.apply_enrichment(company["id"], payload, second["id"])
    assert store.progress(second["id"])["changed"] == 0


@pytest.mark.parametrize("changes,other_scope", [
    ({"latitude": 45, "longitude": -75, "phone": "19999999999", "website": "https://other.example"},
     {"country": "CA", "state": "Ontario", "city": "Ottawa"}),
    ({"latitude": 40.71, "longitude": -74.00}, {"state": "New York", "city": "New York"}),
    ({"phone": "19999999999"}, {}),
    ({"website": "https://other.example"}, {}),
])
def test_alias_requires_geography_and_noncontradictory_channels(store, changes, other_scope):
    market, first = market_run(store)
    source = record(address="123 North Main Street", latitude=34.05, longitude=-118.24)
    a = store.ingest_market(source, first["id"], "fixture", market["id"])
    other = store.create_market({**definition(), **other_scope})
    other_run = store.start_task(other["id"])
    b = store.ingest_market(
        {**source, "source_key": "maps:other", "source_url": "https://maps.example/other", **changes},
        other_run["id"], "fixture", other["id"])
    assert a["id"] != b["id"]
    assert any(r["kind"] == "duplicate_candidate" for r in store.reviews())


def test_street_only_alias_needs_nearby_coordinates(store):
    market, run = market_run(store)
    source = record(address="123 North Main Street", latitude=34.05, longitude=-118.24)
    a = store.ingest_market(source, run["id"], "fixture", market["id"])
    b = store.ingest_market({**source, "source_key": "near", "latitude": 34.0501},
                            run["id"], "fixture", market["id"])
    assert b["id"] == a["id"]
    c = store.ingest_market({**source, "source_key": "unknown", "latitude": None, "longitude": None},
                            run["id"], "fixture", market["id"])
    assert c["id"] != a["id"]


def test_resume_without_discovery_to_retry_keeps_unresolved(store):
    market, run = market_run(store)
    store.save_market_plan(run["id"], {"jobs": [{"key": "one", "query": "fixture"}]})
    store.record_market_unresolved(run["id"], "internal job budget (4096)")
    end_run(store, run)
    assert store.run(run["id"])["status"] == "partial"
    store.control(run["id"], "resume")
    assert store.claim_item(run["id"]) is None
    store.finalize(run["id"])
    assert store.run(run["id"])["status"] == "partial"
    assert store.task(market["id"])["coverage"]["unresolved"]


@pytest.mark.asyncio
async def test_migrated_unfinished_run_replans_without_losing_history_or_members(tmp_path):
    from leadworkbench.db import SCHEMA
    from leadworkbench.worker import process_run
    config = {**definition(), "id": "task", "limit": 1}
    with sqlite3.connect(tmp_path / "workbench.sqlite3") as con:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO tasks VALUES('task','Old','US','California','Los Angeles','[\"wholesale\"]','[]',1,0,'2026-01-01')")
        for run_id, status, created in (("history", "completed", "2026-01-01"), ("active", "running", "2026-01-02")):
            con.execute("INSERT INTO runs VALUES(?,'task','discovery',NULL,?,'{}','',NULL,?,NULL,?)",
                        (run_id, status, created, json.dumps(config)))
            con.execute("INSERT INTO work_items VALUES(?,?,'discovery','legacy query',?,?)",
                        (run_id + "-item", run_id, "succeeded" if run_id == "history" else "running",
                         json.dumps({"message": "retained original result"})))
        con.execute("""INSERT INTO companies(id,name,source_kind,created_at)
            VALUES('existing','Old company','maps','2026-01-01')""")
        con.execute("""INSERT INTO observations VALUES('observation','existing','name','Old company',
            'https://maps.example/old','original evidence','2026-01-02','maps','active','legacy query','fingerprint')""")
    store = Store(tmp_path)
    assert store.run("history")["snapshot"]["limit"] == 1
    active = store.run("active")
    assert active["snapshot"]["limit"] == 0
    assert active["status"] == "queued"
    assert active["snapshot"]["legacy_discovery_items"][0]["result"]
    assert store.company_page(market_id="task")["total"] == 1
    with store.connect() as con:
        assert con.execute("SELECT stage FROM work_items WHERE run_id='active'").fetchone()[0] == "planning"
        assert con.execute("SELECT target FROM work_items WHERE run_id='history'").fetchone()[0] == "legacy query"

    async def planner(snapshot, directory):
        assert snapshot["limit"] == 0
        return {"status": "completed", "jobs": [{"key": "one", "query": "fixture"}], "warnings": []}

    async def discover(*args, **kwargs):
        return {"status": "completed", "records": [record(10), record(11)], "warnings": []}

    await process_run(store, "active", planner=planner, discover=discover)
    assert store.company_page(market_id="task")["total"] == 3
    assert store.progress("active")["new"] == 3
    assert store.detail("existing")["observations"][0]["evidence"] == "original evidence"


def test_1000_companies_database_pagination_and_unlimited_export(store):
    market, run = market_run(store)
    for n in range(1000):
        store.ingest_market(record(n), run["id"], "fixture", market["id"])
    ids = []
    for page in range(1, 21):
        result = store.company_page(market_id=market["id"], page=page, page_size=50)
        assert result["total"] == 1000 and len(result["items"]) == 50
        ids.extend(c["id"] for c in result["items"])
    assert len(set(ids)) == 1000
    assert len(store.companies(market_id=market["id"], max_rows=None)) == 1000
    assert store.company_page(market_id=market["id"], segment="new")["total"] == 1000


def test_unresolved_coverage_prevents_completed(store):
    market, run = market_run(store)
    store.save_market_plan(run["id"], {"jobs": [{"key": "one", "query": "fixture"}], "scope_label": "City cells"})
    store.record_market_unresolved(run["id"], "internal job budget")
    end_run(store, run)
    assert store.run(run["id"])["status"] != "completed"
    coverage = store.task(market["id"])["coverage"]
    assert coverage["planned"] == 1 and coverage["done"] == 1
    assert coverage["unresolved"] == ["internal job budget"]


def test_normalized_compound_identity_and_conservative_category(store):
    market, run = market_run(store)
    first = store.ingest_market(record(category="Clothing store"), run["id"], "fixture", market["id"])
    assert first["business_types"] == []
    replay = store.ingest_market(
        record(source_key="alternate", name="  FIXTURE   0  ", category="Clothing wholesaler"),
        run["id"], "fixture", market["id"])
    assert replay["id"] == first["id"]
    assert replay["business_types"] == ["wholesaler"]
    assert any(o["field"] == "category" and o["value"] == "Clothing store"
               for o in store.detail(first["id"])["observations"])


def test_resume_recomputes_unresolved_and_stable_job_key(store):
    market, run = market_run(store)
    store.save_market_plan(run["id"], {"jobs": [{"key": "one", "query": "fixture"}]})
    store.append_market_jobs(run["id"], [{"key": "one", "query": "fixture", "diagnostic": "retry"}])
    assert store.count_market_jobs(run["id"]) == 1
    store.record_market_unresolved(run["id"], "temporary failure")
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='partial' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])
    store.control(run["id"], "resume")
    end_run(store, run)
    assert store.run(run["id"])["status"] == "completed"
    assert store.task(market["id"])["coverage"]["unresolved"] == []


def test_export_metadata_and_invalid_market_filters(store):
    market, run = market_run(store)
    store.ingest_market(record(), run["id"], "fixture", market["id"])
    rows = store.companies(market_id=market["id"], max_rows=None)
    assert rows[0]["market_segment"] == "new"
    assert rows[0]["market_changed"] is False
    assert rows[0]["first_seen"] and rows[0]["last_seen"]
    for method in (store.companies, store.company_page):
        with pytest.raises(KeyError):
            method(market_id="missing")
        with pytest.raises(KeyError):
            method(run_id="missing")
    other = store.create_market({**definition(), "state": "Other state"})
    with pytest.raises(ValueError):
        store.company_page(market_id=other["id"], run_id=run["id"])


@pytest.mark.parametrize("selected,stored", [("wholesale", "wholesaler"), ("retail", "retailer"), ("import", "importer")])
def test_market_type_aliases_filter_page_and_export_without_rewriting_data(store, selected, stored):
    market, run = market_run(store)
    company = store.ingest_market(record(business_types=[stored]), run["id"], "fixture", market["id"])
    store.ingest_market(record(1, business_types=["manufacturer"]), run["id"], "fixture", market["id"])
    assert store.company_page(market_id=market["id"], type=selected)["total"] == 1
    exported = store.companies(market_id=market["id"], type=selected, max_rows=None)
    assert [r["id"] for r in exported] == [company["id"]]
    assert exported[0]["business_types"] == [stored]


def test_coverage_preserves_attribution_and_geocode_fallback_warnings(store):
    market, run = market_run(store)
    store.save_market_plan(run["id"], {
        "status": "partial", "warnings": ["City geometry unavailable"],
        "coverage": {"scope_label": "Root queries only", "bbox": None,
                     "attribution": "OpenStreetMap contributors", "reference_url": "https://example.org/geometry"},
        "jobs": [{"key": "root", "query": "fixture"}],
    })
    end_run(store, run)
    coverage = store.task(market["id"])["coverage"]
    assert coverage["attribution"] == "OpenStreetMap contributors"
    assert coverage["reference_url"] == "https://example.org/geometry"
    assert coverage["bbox"] is None
    assert "City geometry unavailable" in coverage["unresolved"]
    assert store.run(run["id"])["status"] == "partial"


def test_v1_migration_backfill_restore_and_v2_backup(tmp_path):
    from leadworkbench.db import SCHEMA
    path = tmp_path / "workbench.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO tasks VALUES('task','Old','US','CA','LA','[\"clothing\"]','[]',20,0,'2026-01-01')")
    con.execute("""INSERT INTO companies(id,name,source_kind,created_at)
        VALUES('company','Old company','maps','2026-01-01')""")
    con.execute("""INSERT INTO runs VALUES('run','task','discovery',NULL,'completed','{}','',NULL,
        '2026-01-01','2026-01-02','{}')""")
    con.execute("""INSERT INTO observations VALUES('observation','company','name','Old company',
        'https://maps.example/old','evidence','2026-01-01','maps','run','query','fingerprint')""")
    con.execute("PRAGMA user_version=1")
    con.commit()
    (tmp_path / "backups").mkdir()
    with sqlite3.connect(tmp_path / "backups" / "v1.sqlite3") as backup:
        con.backup(backup)
    con.close()
    store = Store(tmp_path)
    assert store.task("task")["limit"] == 0
    assert store.company_page(market_id="task")["total"] == 1
    assert store.task("task")["market_stats"]["new"] == 1
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    backup = store.backup()
    store.restore("v1.sqlite3")
    assert store.company_page(market_id="task")["total"] == 1
    store.restore(backup["name"])
    assert store.company("company")["name"] == "Old company"
    assert Store(tmp_path).task("task")["limit"] == 0


def test_generated_market_name_and_reserved_creator(store):
    payload = definition()
    payload.pop('name')
    market = store.create_market(payload)
    assert market['name'] == 'Los Angeles / California / US · 批发商 · clothing wholesaler'
    assert market['created_by_user_id'] is None
    assert store.create_market({**payload, 'name': 'Ignored client title'})['id'] == market['id']
    with store.connect() as con:
        assert con.execute('SELECT created_by_user_id FROM task_metadata WHERE task_id=?', (market['id'],)).fetchone()[0] is None
    assert store.tasks()[0]['name'] == market['name']


def test_task_input_needs_no_name_and_cannot_spoof_creator():
    from leadworkbench.api import TaskInput
    from pydantic import ValidationError
    payload = definition()
    payload.pop('name')
    assert 'name' not in TaskInput(**payload).model_dump()
    with pytest.raises(ValidationError):
        TaskInput(**payload, created_by_user_id='someone')


def test_generated_name_keywords_are_stable_and_summarized():
    from leadworkbench.market_store import market_name
    payload = {'city': 'Paris', 'state': 'Ile-de-France', 'country': 'FR',
               'keywords': ['z' * 160, 'A', 'b'], 'company_types': []}
    title = market_name(payload)
    assert title == market_name({**payload, 'keywords': list(reversed(payload['keywords']))})
    assert '等3个关键词' in title
    assert len(title) < 120


@pytest.mark.asyncio
async def test_resumed_legacy_round_skips_unrelated_queries(tmp_path, monkeypatch):
    """A round planned under the old rule (types ∪ keywords) only searches the keyword."""
    import json as _json
    from leadworkbench import maps_policy
    from leadworkbench.sources import maps
    from leadworkbench.worker import process_run_item
    from test_market_worker import record
    store = Store(tmp_path)
    task = store.create_market(dict(country="US", state="California", city="San Jose",
                                    keywords=["metal roof"], company_types=["wholesale"], auto_enrich=False))
    location = "San Jose, California, US"
    async def legacy_planner(config, directory):
        jobs = [dict(key=k, query=f"{q} in {location}", geo=None, zoom=15, depth=10, bbox=None, level=0)
                for k, q in (("c", "clothing wholesaler"), ("m", "metal roof"))]
        return dict(jobs=jobs, coverage={"scope_label": location}, status="completed", warnings=[])
    run = store.start_task(task["id"])
    await process_run_item(store, run["id"], planner=legacy_planner)
    store.control(run["id"], "pause")
    store.control(run["id"], "resume")
    calls, observed = [], []
    async def discover(query, *args, **kwargs):
        calls.append(query)
        return dict(status="completed", records=[record(1)], warnings=[], stop_reason="finished")
    real_observe = maps_policy.observe
    def counting_observe(con, result, *args, **kwargs):
        observed.append(result.get("stop_reason"))
        return real_observe(con, result, *args, **kwargs)
    monkeypatch.setattr(maps, "discover_maps", discover)
    monkeypatch.setattr(maps_policy, "observe", counting_observe)
    for _ in range(3):
        await process_run_item(store, run["id"])
    with store.connect() as con:
        rows = {(_json.loads(r["target"])["query"]): r for r in con.execute(
            "SELECT * FROM work_items WHERE run_id=? AND stage='discovery'", (run["id"],))}
    clothing = rows[f"clothing wholesaler in {location}"]
    assert clothing["state"] == "succeeded"
    assert _json.loads(clothing["result"])["stop_reason"] == "skipped_legacy_query"
    assert calls == [f"metal roof in {location}"]
    assert observed == ["finished"]  # Skipped query never touched Maps pacing.
    assert store.run(run["id"])["progress"]["new"] == 1
