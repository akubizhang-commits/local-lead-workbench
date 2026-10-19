"""P1 batch 2 migration entries on legacy databases (design §3, §7.5, §12.1)."""
from contextlib import closing
import json
import shutil
import sqlite3

import pytest

from db_hash import content_hash
from leadworkbench import migrations, p1b_migrations
from leadworkbench.db import Store
from legacy_db import UPGRADE_FIXTURES, load_upgrade_fixture

P1B_IDS = [id for id, _ in p1b_migrations.MIGRATIONS]
LEGACY_TABLES = ("companies", "contacts", "reviews", "observations", "listings", "tasks", "runs",
                 "changes", "schedules")
KOBETT = "515 Westchester Dr, Campbell, CA 95008, United States"


def ledger(path):
    with closing(sqlite3.connect(path)) as con:
        return con.execute("SELECT id, applied_at FROM schema_migrations ORDER BY rowid").fetchall()


def counts(path):
    with closing(sqlite3.connect(path)) as con:
        return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in LEGACY_TABLES}


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_legacy_upgrade_runs_every_p1b_entry(tmp_path, name):
    path = load_upgrade_fixture(tmp_path, name)
    before = counts(path)
    Store(tmp_path)
    ids = [row[0] for row in ledger(path)]
    assert [i for i in ids if i.startswith("p1b_")] == P1B_IDS
    after = counts(path)
    assert all(after[t] >= before[t] for t in LEGACY_TABLES)
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        companies = {r[0] for r in con.execute("SELECT id FROM companies")}
        assert {r[0] for r in con.execute("SELECT company_id FROM company_cities")} == companies
        assert con.execute("SELECT count(*) FROM company_cities_dirty").fetchone()[0] == 0
    first = ledger(path), content_hash(path)
    Store(tmp_path)
    assert (ledger(path), content_hash(path)) == first


def test_fill_parses_addresses_and_resumes_in_batches(tmp_path, monkeypatch):
    path = load_upgrade_fixture(tmp_path, "v2_9423c53")
    with closing(sqlite3.connect(path)) as con:
        con.execute("""INSERT INTO companies(id,name,address,phone,website,city,country,source_kind,created_at)
            VALUES('kobett','Kobett Metals',?,'','','San Jose','US','maps','2026-10-18T08:00:00+00:00')""", (KOBETT,))
        con.commit()
    monkeypatch.setattr(p1b_migrations, "CITY_BATCH", 2)
    batches = []
    real = Store._refresh_cities_locked
    monkeypatch.setattr(Store, "_refresh_cities_locked",
                        lambda self, con, limit=None: batches.append(limit) or real(self, con, limit))
    Store(tmp_path)
    assert len(batches) >= 3 and set(batches) == {2}
    with closing(sqlite3.connect(path)) as con:
        total = con.execute("SELECT count(*) FROM companies").fetchone()[0]
        assert con.execute("SELECT count(*) FROM company_cities").fetchone()[0] == total
        city, nearby, parsed = con.execute(
            "SELECT city,nearby_global,parsed FROM company_cities WHERE company_id='kobett'").fetchone()
    assert (city, nearby, parsed) == ("Campbell", 1, 1)


def _task(con, id, name):
    con.execute("""INSERT INTO tasks(id,name,country,state,city,keywords,company_types,"limit",auto_enrich,created_at)
        VALUES(?,?,'US','California','San Jose','["x"]','["wholesale"]',0,0,'2026-10-01T00:00:00+00:00')""", (id, name))


def _schedule(con, id, task_id, *, enabled=1, anchor="2026-09-01T00:00:00+00:00", kind="discovery", company_id=None):
    con.execute("""INSERT INTO schedules(id,task_id,company_id,kind,interval_months,timezone,next_due_at,enabled,anchor)
        VALUES(?,?,?,?,1,'Asia/Shanghai','2026-11-01T00:00:00+00:00',?,?)""",
                (id, task_id, company_id, kind, enabled, anchor))


def duplicate_plans(path):
    with closing(sqlite3.connect(path)) as con:
        company = con.execute("SELECT id FROM companies LIMIT 1").fetchone()[0]
        for id, name in (("A", "Market A"), ("B", "Market B"), ("C", "Market C")):
            _task(con, id, name)
        _schedule(con, "a2", "A", anchor="2026-08-02T00:00:00+00:00")
        _schedule(con, "a1", "A", anchor="2026-08-01T00:00:00+00:00")
        _schedule(con, "a3", "A", anchor=None)
        _schedule(con, "a0", "A", enabled=0, anchor="2026-01-01T00:00:00+00:00")
        _schedule(con, "b1", "B")
        _schedule(con, "c_null_anchor", "C", anchor=None)
        _schedule(con, "c_null_kind", "C", kind=None, anchor="2026-09-05T00:00:00+00:00")
        _schedule(con, "r1", None, kind="refresh", company_id=company)
        _schedule(con, "r2", None, kind="refresh", company_id=company)
        con.commit()


def assert_deduplicated(path):
    with closing(sqlite3.connect(path)) as con:
        enabled = dict(con.execute("SELECT id,enabled FROM schedules WHERE id IN "
                                   "('a0','a1','a2','a3','b1','c_null_anchor','c_null_kind','r1','r2')").fetchall())
        assert enabled == {"a0": 0, "a1": 1, "a2": 0, "a3": 0, "b1": 1, "c_null_anchor": 0, "c_null_kind": 1,
                           "r1": 1, "r2": 1}
        changes = con.execute("SELECT market_id,field,old_value,new_value,reason FROM market_changes "
                              "ORDER BY market_id,old_value").fetchall()
        assert [(m, f) for m, f, *_ in changes] == [("A", "schedule"), ("A", "schedule"), ("C", "schedule")]
        assert {json.loads(old)["schedule_id"] for _, _, old, _, _ in changes} == {"a2", "a3", "c_null_anchor"}
        assert all(json.loads(old)["enabled"] is True and json.loads(old)["interval_months"] == 1
                   and json.loads(new) == {"enabled": False}
                   and reason == "升级：同一市场只保留最早的启用计划，此计划已停用（未删除）"
                   for _, _, old, new, reason in changes)
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                           "AND name='schedules_one_enabled_market'").fetchone()
        return con.execute("SELECT count(*) FROM schedules").fetchone()[0]


def test_schedule_dedupe_keeps_the_earliest_enabled_plan(tmp_path):
    path = load_upgrade_fixture(tmp_path, "v2_9423c53")
    duplicate_plans(path)
    with closing(sqlite3.connect(path)) as con:
        total = con.execute("SELECT count(*) FROM schedules").fetchone()[0]
    shutil.copyfile(path, tmp_path / "pre-upgrade.sqlite3")
    store = Store(tmp_path)
    assert assert_deduplicated(path) == total
    first = content_hash(path, tables=["schedules", "market_changes"])
    Store(tmp_path)
    assert content_hash(path, tables=["schedules", "market_changes"]) == first
    # Restoring a backup of the same old database runs the same executor.
    (store.directory / "backups").mkdir(exist_ok=True)
    shutil.copyfile(tmp_path / "pre-upgrade.sqlite3", store.directory / "backups" / "old.sqlite3")
    store.restore("old.sqlite3")
    assert assert_deduplicated(store.path) == total


NOISE = [("phone", "+1 408-435-5020", "(408) 435-5020"),
         ("name", "Alpha", "Alpha – Roofing, Panels, Sun Control, Silicon Valley"),
         ("business_types", '["retailer"]', '["manufacturer"]')]


def test_batch1_database_upgrades_without_closing_noise_reviews(tmp_path, monkeypatch):
    path = load_upgrade_fixture(tmp_path, "v2_9423c53")
    # Build a database as the P1 batch 1 release left it: the first 26 entries only.
    old = tuple(e for e in migrations.REGISTRY if not e.id.startswith("p1b_"))
    monkeypatch.setattr(migrations, "REGISTRY", old)
    monkeypatch.setattr(migrations, "ONCE_IDS", tuple(e.id for e in old if e.kind == "once"))
    monkeypatch.setattr(migrations, "REGISTRY_IDS", frozenset(e.id for e in old if e.kind == "once"))
    Store(tmp_path)
    monkeypatch.undo()
    with closing(sqlite3.connect(path)) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='company_cities'").fetchone()
        company = con.execute("SELECT id FROM companies LIMIT 1").fetchone()[0]
        for n, (field, old_value, new_value) in enumerate(NOISE):
            con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,
                status,created_at,fingerprint) VALUES(?,?,'field_update',?,?,?,'https://alpha.example/','x','pending',
                '2026-10-17T08:00:00+00:00',?)""", (f"noise{n}", company, field, old_value, new_value, f"noise-fp-{n}"))
        con.commit()
    duplicate_plans(path)
    Store(tmp_path)
    assert_deduplicated(path)
    with closing(sqlite3.connect(path)) as con:
        assert dict(con.execute("SELECT id,status FROM reviews WHERE id LIKE 'noise%'").fetchall()) == {
            "noise0": "pending", "noise1": "pending", "noise2": "pending"}
        assert con.execute("SELECT count(*) FROM review_resolutions").fetchone()[0] == 0
