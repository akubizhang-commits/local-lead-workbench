from datetime import datetime, timezone
from pathlib import Path
import pytest

from leadworkbench.db import Store, Conflict, calendar_next


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def record(**changes):
    return {
        "source_key": "maps:one",
        "source_url": "https://www.google.com/maps/place/?q=place_id:one",
        "name": "Fixture Apparel", "address": "Los Angeles, CA",
        "phone": "123", "website": "https://fixture.example",
        "observed_at": "2026-09-22T01:00:00+00:00", **changes,
    }


def test_source_replay_does_not_duplicate_and_keeps_provenance(store):
    a = store.ingest(record(), "run1", "clothing wholesaler Los Angeles")
    b = store.ingest(record(), "run1", "clothing wholesaler Los Angeles")
    assert a["id"] == b["id"]
    assert len(store.companies()) == 1
    detail = store.detail(a["id"])
    assert detail["observations"][0]["source_url"].startswith("https://www.google.com")
    assert detail["company"]["source_kind"] == "maps"


def test_new_observation_preserves_manual_phone_and_notes(store):
    a = store.ingest(record(), "run1", "query")
    store.update_company(a["id"], a["revision"], {"phone": "manual"}, "keep", "verified")
    store.ingest(record(phone="new"), "run2", "query")
    result = store.company(a["id"])
    assert result["phone"] == "manual"
    assert result["notes"] == "keep"
    reviews = store.reviews()
    assert any(r["field"] == "phone" and r["new_value"] == "new" for r in reviews)
    assert any(o["value"] == "new" for o in store.detail(a["id"])["observations"])


def test_revision_prevents_lost_updates(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    store.update_company(a["id"], a["revision"], {}, "new notes", None)
    with pytest.raises(Conflict):
        store.update_company(a["id"], a["revision"], {}, "stale", None)


def test_contact_attribution_and_deduplication(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    contact = {"name": "Alex", "role": "Buyer", "email": "buyer@fixture.example",
               "kind": "email", "role_status": "procurement", "association_status": "explicit",
               "source_url": "https://fixture.example/team", "evidence": "Alex Buyer buyer@fixture.example",
               "observed_at": "2026-09-22T01:00:00+00:00"}
    payload = {"status": "completed", "observations": [], "contacts": [contact], "social_links": []}
    store.apply_enrichment(a["id"], payload)
    store.apply_enrichment(a["id"], payload)
    assert len(store.detail(a["id"])["contacts"]) == 1
    assert store.company(a["id"])["last_checked_at"]


def test_failed_refresh_does_not_mark_checked_or_clear_fields(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    store.apply_enrichment(a["id"], {"status": "failed", "observations": [], "contacts": []})
    b = store.company(a["id"])
    assert b["name"] == "Fixture"
    assert b["last_checked_at"] is None


def test_month_end_and_missed_occurrences_preserve_calendar_anchor():
    anchor = datetime(2026, 1, 31, 10, tzinfo=timezone.utc)
    assert calendar_next(anchor, anchor, 1, "UTC").isoformat() == "2026-02-28T10:00:00+00:00"
    after = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert calendar_next(anchor, after, 1, "UTC").day == 31


def test_schedule_dispatch_is_idempotent_and_coalesces_missed_runs(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    schedule = store.create_schedule(company_id=a["id"], kind="refresh", interval_months=3, timezone="UTC")
    with store.connect() as con:
        con.execute("UPDATE schedules SET next_due_at=? WHERE id=?", ("2025-01-01T00:00:00+00:00", schedule["id"]))
    store.dispatch_due()
    store.dispatch_due()
    assert len(store.runs()) == 1


def test_recovery_requeues_interrupted_work(store):
    task = store.create_task({"name": "Test", "country": "US", "state": "California", "city": "Los Angeles",
                              "keywords": ["clothing wholesaler"], "company_types": ["wholesale"], "limit": 20,
                              "auto_enrich": False})
    run = store.start_task(task["id"])
    item = store.claim_item(run["id"])
    assert item["state"] == "running"
    store.recover()
    assert store.claim_item(run["id"])["id"] == item["id"]


def test_backup_restores_consistent_business_data(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    backup = store.backup()
    store.update_company(a["id"], a["revision"], {}, "later", None)
    store.restore(backup["name"])
    assert store.company(a["id"])["notes"] == ""


def test_restore_recovers_jobs_that_were_inflight_in_backup(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(a["id"])
    item = store.claim_item(run["id"])
    backup = store.backup()
    store.control(run["id"], "cancel")
    store.restore(backup["name"])
    claimed = store.claim_item(run["id"])
    assert claimed is not None and claimed["id"] == item["id"]


def test_changed_contact_role_requires_revalidation(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    c = {"name": "Alex", "role": "Buyer", "email": "alex@fixture.example",
         "role_status": "explicit", "association_status": "same_block",
         "source_url": "https://fixture.example/team", "evidence": "Alex Buyer"}
    store.apply_enrichment(a["id"], {"status": "completed", "contacts": [c]})
    assert not store.reviews()
    c.update(role="Former Buyer", role_status="historical", evidence="Alex Former Buyer")
    store.apply_enrichment(a["id"], {"status": "completed", "contacts": [c]})
    # P0-2: the current contact is unchanged; the new role waits as a proposal.
    contact = store.detail(a["id"])["contacts"][0]
    assert (contact["review_status"], contact["role_status"]) == ("auto_recorded", "explicit")
    review = store.reviews()[0]
    assert review["contact_change"] == "proposal"
    assert review["contact"]["role_status"] == "historical"


def test_competing_new_phone_values_require_review(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    obs = [{"field": "phone", "value": value, "source_url": f"https://fixture.example/{value}",
            "evidence": value} for value in ("111", "222")]
    store.apply_enrichment(a["id"], {"status": "completed", "observations": obs})
    assert store.company(a["id"])["phone"] == "111"
    assert any(r["new_value"] == "222" for r in store.reviews())


def test_partial_refresh_does_not_claim_successful_check(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    store.apply_enrichment(a["id"], {"status": "partial", "observations": [], "warnings": ["timeout"]})
    assert store.company(a["id"])["last_checked_at"] is None


def test_export_not_truncated_to_ui_limit(store):
    from leadworkbench.export import export_data
    import csv, io
    for n in range(501):
        store.manual_company(f"Fixture {n}", "https://fixture.example")
    rows = list(csv.reader(io.StringIO(export_data(store).decode("utf-8-sig"))))
    assert len(rows) == 502


def test_active_run_query_not_limited_by_history(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    run = store.refresh(a["id"])
    # Separate inactive history created after the old pending run.
    with store.connect() as con:
        for n in range(201):
            con.execute("""INSERT INTO runs(id,kind,status,progress,created_at,snapshot)
                VALUES(?,'refresh','cancelled','{}',?,'{}')""", (f"history-{n}", f"2027-{n:03d}"))
    assert store.active_runs()[0]["id"] == run["id"]


def test_repeated_contact_transitions_always_create_review_for_new_revision(store):
    a = store.manual_company("Fixture", "https://fixture.example")
    for index, role in enumerate(("Buyer", "Sales", "Buyer", "Manager", "Buyer", "Sales")):
        c = {"name": "Alex", "role": role, "email": "alex@fixture.example", "source_url": "https://fixture.example/team",
             "evidence": f"Alex {role}", "role_status": "explicit", "association_status": "same_block"}
        store.apply_enrichment(a["id"], {"status": "completed", "contacts": [c]})
        reviews = [r for r in store.reviews() if r["kind"] == "contact"]
        assert len(reviews) == (0 if index == 0 else 1)
        if reviews:
            store.review_decision(reviews[0]["id"], "accept")
