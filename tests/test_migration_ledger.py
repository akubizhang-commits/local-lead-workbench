"""Unified migration ledger (P1-9, design §3.2)."""
from contextlib import closing
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest

from leadworkbench import migrations
from leadworkbench.db import AUDIT_DDL, SCHEMA, Store
from leadworkbench.errors import Invalid
from leadworkbench.market_social import MARKET_SOCIAL_DDL
from leadworkbench.market_store import MIGRATION
from leadworkbench.maps_policy import DDL as MAPS_DDL
from leadworkbench.p0_migrations import INDEXES, MIGRATIONS as P0
from leadworkbench.social_discovery_store import SOCIAL_DISCOVERY_DDL
from leadworkbench.social_policy import DDL as POLICY_DDL
from leadworkbench.social_store import SOCIAL_DDL
from legacy_db import FIXTURES, UPGRADE_FIXTURES, dump_database, load_upgrade_fixture, normalize

# Differences the refactor is allowed to introduce; everything else must equal the snapshot.
EXPECTED_NEW_TABLES = {"market_exhausted_items"}  # 0011 creates it at start-up instead of on demand
ONCE_IDS_0001_0011 = {
    "0001_base_v1", "0002_market_v2", "0003_maps_access_policy", "0004_website_host_gates",
    "0005_listing_aliases", "0006_audit_tables", "0007_social_tables", "0008_social_discovery_tables",
    "0009_social_legacy_links", "0010_market_social_tables", "0011_market_exhausted_items"}
EXPECTED_NEW_LEDGER = ONCE_IDS_0001_0011
# work_items is not listed: 0002 archives legacy capped discovery items into the run snapshot and
# replaces them with one planning item (unchanged historical behaviour, covered by 0a).
LEGACY_TABLES = ("companies", "contacts", "reviews", "observations", "listings", "tasks", "runs",
                 "changes", "schedules")


def _snapshot(name):
    return json.loads((FIXTURES / f"expected_upgrade_{name}.json").read_text(encoding="utf-8"))


def ledger(path):
    with closing(sqlite3.connect(path)) as con:
        return con.execute("SELECT id, applied_at FROM schema_migrations ORDER BY rowid").fetchall()


def content_hash(path):
    with closing(sqlite3.connect(path)) as con:
        return hashlib.sha256("\n".join(con.iterdump()).encode()).hexdigest()


def counts(path, tables):
    with closing(sqlite3.connect(path)) as con:
        present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables if t in present}


def test_registry_shape():
    ids = [entry.id for entry in migrations.REGISTRY]
    assert len(ids) == 26 and len(set(ids)) == 26
    assert [i for i in ids if i[0] == "R"] == [f"R0{n}" + i[3:] for n, i in
                                               enumerate((i for i in ids if i[0] == "R"), 1)]
    assert set(migrations.ONCE_IDS) == ONCE_IDS_0001_0011 | {id for id, _ in P0}
    p0_positions = [ids.index(id) for id, _ in P0]
    assert p0_positions == sorted(p0_positions)


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_0a_upgrade_matches_frozen_snapshot(tmp_path, name):
    snapshot = _snapshot(name)
    path = load_upgrade_fixture(tmp_path, name)
    Store(tmp_path)
    after = normalize(dump_database(path), snapshot["volatile"])
    frozen_names = {n for n in snapshot["sqlite_master_names"] if not n.startswith("sqlite_stat")}
    assert set(after["sqlite_master_names"]) == frozen_names | EXPECTED_NEW_TABLES
    assert set(after["ledger_ids"]) == set(snapshot["ledger_ids"]) | EXPECTED_NEW_LEDGER
    for table in EXPECTED_NEW_TABLES:
        assert after["tables"].pop(table) == []
    assert after["tables"] == snapshot["tables"]


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_0b_executor_never_opens_another_connection(tmp_path, name, monkeypatch):
    path = load_upgrade_fixture(tmp_path, name)
    inside = threading.Event()
    connects, sources = [], []
    real_run, real_connect, real_source = migrations._run, Store.connect, Store._backup_source
    def run(store, con):
        inside.set()
        try:
            return real_run(store, con)
        finally:
            inside.clear()
    def connect(self):
        if inside.is_set():
            connects.append(1)
        return real_connect(self)
    def source(self):
        sources.append(1)
        return real_source(self)
    monkeypatch.setattr(migrations, "_run", run)
    monkeypatch.setattr(Store, "connect", connect)
    monkeypatch.setattr(Store, "_backup_source", source)
    errors = []
    def start():
        try:
            Store(tmp_path)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "migration deadlocked"
    assert errors == []
    assert connects == []
    assert len(sources) == 1  # every fixture carries one repair candidate: one pre-repair backup
    assert {row[0] for row in ledger(path)} == set(migrations.ONCE_IDS)
    with closing(sqlite3.connect(path)) as con:
        separate = con.execute("SELECT count(*) FROM reviews WHERE kind='duplicate_candidate' "
                               "AND status='auto_separate'").fetchone()[0]
        upgraded_contacts = con.execute("SELECT count(*) FROM contacts").fetchone()[0]
    assert separate >= 1
    backups = list((tmp_path / "backups").glob("auto-pre-repair-*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as con:
        assert not con.execute("SELECT 1 FROM changes WHERE reason LIKE '数据修复：%'").fetchone()
        assert con.execute("SELECT count(*) FROM contacts").fetchone()[0] == upgraded_contacts


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_1_upgrade_records_every_once_entry_and_keeps_rows(tmp_path, name):
    path = load_upgrade_fixture(tmp_path, name)
    before = counts(path, LEGACY_TABLES)
    Store(tmp_path)
    assert [row[0] for row in ledger(path)] == list(migrations.ONCE_IDS)
    after = counts(path, LEGACY_TABLES)
    for table, count in before.items():
        assert after[table] >= count, table
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_2_second_start_is_idempotent(tmp_path, name):
    path = load_upgrade_fixture(tmp_path, name)
    Store(tmp_path)
    first_ledger, first_hash = ledger(path), content_hash(path)
    with closing(sqlite3.connect(path)) as con:
        master = con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    Store(tmp_path)
    assert ledger(path) == first_ledger
    assert content_hash(path) == first_hash
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == master


def test_3_p0_database_only_gains_the_new_ledger_rows(tmp_path):
    path = load_upgrade_fixture(tmp_path, "v2_9423c53")
    Store(tmp_path)
    # Make it look like a database upgraded by the P0 release (913ee04): P0 rows only.
    with closing(sqlite3.connect(path)) as con:
        con.execute("DELETE FROM schema_migrations WHERE id NOT LIKE 'p0_%' AND id NOT LIKE 'contact_facets_%'")
        con.commit()
    p0_rows = dict(ledger(path))
    assert set(p0_rows) == {id for id, _ in P0}
    Store(tmp_path)
    after = dict(ledger(path))
    assert {id: after[id] for id in p0_rows} == p0_rows  # P0 items did not run again
    assert set(after) - set(p0_rows) == ONCE_IDS_0001_0011


def _start(directory):
    from leadworkbench.db import Store as ChildStore
    ChildStore(directory)


def test_4_two_processes_start_together(tmp_path):
    path = load_upgrade_fixture(tmp_path, "v1")
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_start, args=(str(tmp_path),)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(60)
    assert [process.exitcode for process in processes] == [0, 0]
    rows = ledger(path)
    assert sorted(id for id, _ in rows) == sorted(migrations.ONCE_IDS)
    assert len({id for id, _ in rows}) == len(rows)


def _backup_with_ledger(tmp_path, store, ids):
    path = load_upgrade_fixture(tmp_path / "legacy", "v1")
    Store(tmp_path / "legacy")
    with closing(sqlite3.connect(path)) as con:
        for id in ids:
            con.execute("INSERT INTO schema_migrations(id, applied_at) VALUES(?, '2026-01-01')", (id,))
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    name = f"backup-{len(ids)}-{'-'.join(ids) or 'none'}.sqlite3"
    (store.directory / "backups").mkdir(exist_ok=True)
    shutil.copyfile(path, store.directory / "backups" / name)
    shutil.rmtree(tmp_path / "legacy")
    return name


def test_5_restore_upgrades_old_backups_and_refuses_newer_ones(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    legacy = tmp_path / "v1"
    path = load_upgrade_fixture(legacy, "v1")
    (store.directory / "backups").mkdir(exist_ok=True)
    shutil.copyfile(path, store.directory / "backups" / "legacy-v1.sqlite3")
    store.restore("legacy-v1.sqlite3")
    assert [row[0] for row in ledger(store.path)] == list(migrations.ONCE_IDS)
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    old = _backup_with_ledger(tmp_path, store, ["contact_facets_v0", "contact_facets_v0_mark"])
    store.restore(old)
    for future in (["9999_future"], ["contact_facets_v99"]):
        name = _backup_with_ledger(tmp_path, store, future)
        before = content_hash(store.path)
        with pytest.raises(Invalid, match="该备份来自更新版本的应用，当前版本无法恢复"):
            store.restore(name)
        assert content_hash(store.path) == before
    assert not migrations.is_known("0012_retired")
    monkeypatch.setattr(migrations, "RETIRED", frozenset({"0012_retired"}))
    assert migrations.is_known("0012_retired")


def test_6_newer_user_version_is_refused(tmp_path):
    with closing(sqlite3.connect(tmp_path / "workbench.sqlite3")) as con:
        con.execute("PRAGMA user_version=3")
    with pytest.raises(Invalid, match="数据库版本高于应用版本"):
        Store(tmp_path)


@pytest.mark.parametrize("sql", [SCHEMA, MIGRATION, AUDIT_DDL, SOCIAL_DDL, SOCIAL_DISCOVERY_DDL,
                                 POLICY_DDL + MARKET_SOCIAL_DDL, MAPS_DDL, INDEXES])
def test_7_statements_are_complete(sql):
    parts = migrations.statements(sql)
    assert parts and all(sqlite3.complete_statement(part + ";") for part in parts)


def test_ready_requires_every_once_entry(tmp_path):
    store = Store(tmp_path)
    with store.connect() as con:
        assert migrations.ready(con)
        con.execute("DELETE FROM schema_migrations WHERE id='p0_20261018_repair_rejected'")
        assert migrations.ready(con)  # optional entry
        con.execute("DELETE FROM schema_migrations WHERE id='0011_market_exhausted_items'")
        assert not migrations.ready(con)


def test_backup_from_a_write_transaction_stays_busy(tmp_path):
    """Why backups read through _backup_source(): the executor's own connection cannot be the source."""
    store = Store(tmp_path)
    busy = []
    def progress(status, remaining, total):
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            busy.append(status)
            if len(busy) >= 20:
                raise RuntimeError("still busy")
    with closing(sqlite3.connect(store.path, timeout=1)) as con, \
            closing(sqlite3.connect(tmp_path / "copy.sqlite3")) as dest:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE maps_access_policy SET failures=failures WHERE id=1")
        with pytest.raises(Exception):
            con.backup(dest, pages=1, progress=progress, sleep=0.001)
        con.rollback()
    assert len(busy) >= 20


@pytest.mark.parametrize("name", ["data dir", "data?x", "data#x", "data%20x"])
def test_backup_source_uri_survives_special_characters(tmp_path, name):
    store = Store(tmp_path / name)
    for i in range(3):
        store.manual_company(f"Uri {i}", f"https://u{i}.example")
    backup = store.backup()["name"]
    with closing(sqlite3.connect(store.directory / "backups" / backup)) as con:
        assert con.execute("SELECT count(*) FROM companies").fetchone()[0] == 3
