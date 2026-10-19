"""P1-20: round summary with entry tier changes (design §7.4, §7.5)."""
import json
import logging

import pytest

from db_hash import content_hash
from leadworkbench import market_admin
from leadworkbench.db import Store


def record(n, **extra):
    return {"source_key": f"maps:{n}", "source_url": f"https://maps.example/{n}", "name": f"Fixture {n}",
            "address": f"{n + 1} Example Street, San Jose, CA 95112", "phone": f"1408555{n:04d}",
            "website": f"https://fixture{n}.example", "observed_at": f"2026-10-0{n + 1}T01:00:00+00:00", **extra}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def market(store):
    return store.create_market(dict(country="US", state="California", city="San Jose", keywords=["metal roofing supply"],
                                    company_types=["wholesale"], auto_enrich=False))


def end(store, run):
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])


def summary_row(store, run_id):
    with store.connect() as con:
        row = con.execute("SELECT payload FROM market_round_summaries WHERE run_id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def first_round(store):
    task = market(store)
    run = store.start_task(task["id"])
    ids = [store.ingest_market(record(n), run["id"], "q", task["id"])["id"] for n in range(2)]
    end(store, run)
    return task, ids


def test_start_writes_a_baseline_and_finish_writes_the_numbers(store):
    task, ids = first_round(store)
    first = store.task(task["id"])["round_summary"]
    assert (first["round"], first["new"], first["total"], first["tiers_available"]) == (1, 2, 2, True)
    run = store.start_task(task["id"])
    with store.connect() as con:
        baseline = dict(con.execute("SELECT company_id,rank FROM market_round_baselines WHERE run_id=?", (run["id"],)).fetchall())
    assert baseline == {ids[0]: 7, ids[1]: 7}  # company phone only
    assert summary_row(store, run["id"]) == {"pending": True}
    store.ingest_market(record(0), run["id"], "q", task["id"])
    store.ingest_market(record(5), run["id"], "q", task["id"])
    company = store.company(ids[0])
    store.add_manual_contact(ids[0], company["revision"], name="Bob Buyer", email="bob@fixture0.example", procurement=True)
    end(store, run)
    payload = summary_row(store, run["id"])
    assert {k: payload[k] for k in ("round", "status", "new", "existing", "not_seen", "total", "tiers_available",
                                    "tier_improved", "tier_worsened")} == {
        "round": 2, "status": "completed", "new": 1, "existing": 1, "not_seen": 1, "total": 3,
        "tiers_available": True, "tier_improved": 1, "tier_worsened": 0}
    # The new company adds a company-phone entry, Fixture 0 moved from company phone to named procurement.
    assert payload["tier_delta"] == {"named_procurement": 1}
    assert store.task(task["id"])["round_summary"] == payload


def test_cancel_writes_a_summary(store):
    task, _ = first_round(store)
    run = store.start_task(task["id"])
    store.control(run["id"], "cancel")
    payload = summary_row(store, run["id"])
    assert payload["status"] == "cancelled" and payload["tiers_available"] is True


def test_resumed_round_overwrites_the_summary(store):
    task, _ = first_round(store)
    run = store.start_task(task["id"])
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='failed' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])
    assert summary_row(store, run["id"])["status"] == "failed"
    with store.connect() as con:
        con.execute("UPDATE runs SET status='queued' WHERE id=?", (run["id"],))
    end(store, run)
    payload = summary_row(store, run["id"])
    assert payload["status"] == "completed" and payload["tiers_available"] is True


def test_old_round_is_computed_on_read_without_writing(store):
    task, _ = first_round(store)
    run_id = store.task(task["id"])["latest_run"]["id"]
    with store.connect() as con:
        con.execute("DELETE FROM market_round_summaries")
    before = content_hash(store.path, tables=["market_round_summaries"])
    summary = store.task(task["id"])["round_summary"]
    assert summary["run_id"] == run_id and summary["tiers_available"] is False
    assert summary["tier_improved"] is None and summary["tier_delta"] is None and summary["new"] == 2
    assert content_hash(store.path, tables=["market_round_summaries"]) == before


def test_round_started_before_the_upgrade_has_no_tiers(store):
    task, _ = first_round(store)
    run = store.start_task(task["id"])
    with store.connect() as con:  # as if the round began before the baseline existed
        con.execute("DELETE FROM market_round_summaries WHERE run_id=?", (run["id"],))
        con.execute("DELETE FROM market_round_baselines WHERE run_id=?", (run["id"],))
    store.ingest_market(record(0), run["id"], "q", task["id"])
    end(store, run)
    payload = summary_row(store, run["id"])
    assert payload["tiers_available"] is False and payload["tier_worsened"] is None
    assert (payload["existing"], payload["not_seen"]) == (1, 1)


def test_summary_failure_never_blocks_the_round(store, monkeypatch, caplog):
    task, _ = first_round(store)
    run = store.start_task(task["id"])
    def boom(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(market_admin.MarketAdminMixin, "_round_summary_payload", boom)
    with caplog.at_level(logging.WARNING):
        end(store, run)
    assert store.run(run["id"])["status"] == "completed"
    assert any(getattr(r, "event", "") == "round_summary_failed" for r in caplog.records)
    assert summary_row(store, run["id"]) == {"pending": True}
    monkeypatch.undo()
    before = content_hash(store.path, tables=["market_round_summaries"])
    summary = store.task(task["id"])["round_summary"]
    assert summary["run_id"] == run["id"] and summary["tiers_available"] is False
    assert content_hash(store.path, tables=["market_round_summaries"]) == before
