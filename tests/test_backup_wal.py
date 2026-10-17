"""Backups must open without -wal/-shm on every SQLite build (3.51.0 rejected WAL headers read-only)."""
from contextlib import closing
import hashlib
import sqlite3

import pytest

from leadworkbench.db import Store


def leftovers(folder):
    return sorted(p.name for p in folder.iterdir() if not p.name.endswith(".sqlite3"))


def test_new_backup_header_is_rollback_mode(tmp_path):
    store = Store(tmp_path)
    store.manual_company("Fixture Co", "https://fixture.example")
    name = store.backup()["name"]
    file = tmp_path / "backups" / name
    assert file.read_bytes()[18:20] == b"\x01\x01"
    assert leftovers(tmp_path / "backups") == []
    with closing(sqlite3.connect(f"file:{file}?mode=ro", uri=True)) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_leaves_no_sidecars_in_backups(tmp_path):
    store = Store(tmp_path)
    store.manual_company("Fixture Co", "https://fixture.example")
    name = store.backup()["name"]
    store.restore(name)
    assert leftovers(tmp_path / "backups") == []
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 2  # Original + safety backup.


def test_restore_accepts_legacy_wal_header_backup_without_sidecars(tmp_path):
    store = Store(tmp_path)
    store.manual_company("Legacy Co", "https://legacy.example")
    legacy = tmp_path / "backups" / "legacy.sqlite3"
    legacy.parent.mkdir(exist_ok=True)
    with store.connect() as source, closing(sqlite3.connect(legacy)) as dest:
        source.backup(dest)
        dest.execute("PRAGMA journal_mode=WAL")
    for suffix in ("-wal", "-shm"):
        (legacy.parent / f"legacy.sqlite3{suffix}").unlink(missing_ok=True)
    assert legacy.read_bytes()[18:20] == b"\x02\x02"
    digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
    store.manual_company("After Backup Co", "https://after.example")
    store.restore("legacy.sqlite3")
    assert [c["name"] for c in store.companies()] == ["Legacy Co"]
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == digest
    assert not list(legacy.parent.glob(".restore-*"))


def test_restore_corrupt_backup_is_rejected_and_temp_removed(tmp_path):
    store = Store(tmp_path)
    store.manual_company("Keep Co", "https://keep.example")
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    (folder / "broken.sqlite3").write_bytes(b"not a database" * 100)
    with pytest.raises(ValueError):
        store.restore("broken.sqlite3")
    assert not list(folder.glob(".restore-*"))
    assert [c["name"] for c in store.companies()] == ["Keep Co"]
