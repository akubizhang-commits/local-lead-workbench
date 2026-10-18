"""Upgrading 9423c53 databases with the P0 migrations (design §1.1, §9.2)."""
from contextlib import closing
import shutil
import sqlite3
import threading

import pytest

from leadworkbench.db import Store
from leadworkbench.p0_migrations import MIGRATIONS
from legacy_db import load_legacy

FIXTURES = ("legacy_v2_9423c53.sql", "legacy_export_source.sql")
LEGACY_TABLES_SQL = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
INDEXES = {"contacts_company", "contacts_company_email", "contact_versions_contact", "reviews_status",
           "reviews_company_field", "reviews_contact", "locations_company", "changes_company",
           "observations_company_field", "companies_newest", "companies_status", "listings_company",
           "work_items_target"}


def snapshot(path, tables=None):
    with closing(sqlite3.connect(path)) as con:
        names = tables or [r[0] for r in con.execute(LEGACY_TABLES_SQL)]
        return {name: {r[0]: r[1:] for r in con.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')}
                for name in names}


def full_dump(path):
    with closing(sqlite3.connect(path)) as con:
        return [line for line in con.iterdump() if not line.startswith(("ANALYZE", "INSERT INTO sqlite_stat"))
                and "sqlite_stat" not in line]


def ledger(store):
    with store.connect() as con:
        return [r[0] for r in con.execute("SELECT id FROM schema_migrations ORDER BY rowid")]


def changed_rows(before, after):
    """Rows of legacy tables that were removed or modified (new rows are allowed)."""
    diff = {}
    for table, rows in before.items():
        for rowid, values in rows.items():
            if after[table].get(rowid) != values:
                diff.setdefault(table, []).append(rowid)
    return diff


ALLOWED_CHANGES = {}  # legacy rows a migration may rewrite, per fixture (repair item only)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_upgrade_keeps_legacy_rows_and_records_ledger(tmp_path, fixture):
    path = load_legacy(tmp_path, fixture)
    before = snapshot(path)
    store = Store(tmp_path)
    after = snapshot(path, list(before))
    for table, rows in before.items():
        assert len(after[table]) >= len(rows), table
    assert changed_rows(before, after) == ALLOWED_CHANGES.get(fixture, {})
    assert ledger(store) == [id for id, _ in MIGRATIONS]
    with store.connect() as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert INDEXES <= names
        assert con.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] > 0
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM contact_facets").fetchone()[0] == \
            con.execute("SELECT count(*) FROM contacts").fetchone()[0]
        assert not con.execute("SELECT 1 FROM contact_facets_dirty").fetchone()


@pytest.mark.parametrize("fixture", FIXTURES)
def test_second_start_changes_nothing(tmp_path, fixture):
    path = load_legacy(tmp_path, fixture)
    Store(tmp_path)
    first = full_dump(path)
    Store(tmp_path)
    assert full_dump(path) == first


def test_restored_legacy_backup_is_upgraded(tmp_path):
    legacy = tmp_path / "legacy"
    path = load_legacy(legacy)
    store = Store(tmp_path / "data")
    (store.directory / "backups").mkdir(exist_ok=True)
    shutil.copyfile(path, store.directory / "backups" / "legacy.sqlite3")
    store.restore("legacy.sqlite3")
    assert ledger(store) == [id for id, _ in MIGRATIONS]
    with store.connect() as con:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='contacts_company'").fetchone()
        assert con.execute("SELECT count(*) FROM contacts").fetchone()[0] == 5


def test_two_processes_starting_together_apply_each_item_once(tmp_path):
    load_legacy(tmp_path)
    errors = []
    barrier = threading.Barrier(2)

    def start():
        try:
            barrier.wait()
            Store(tmp_path)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with closing(sqlite3.connect(tmp_path / "workbench.sqlite3")) as con:
        rows = [r[0] for r in con.execute("SELECT id FROM schema_migrations")]
    assert sorted(rows) == sorted(id for id, _ in MIGRATIONS)
