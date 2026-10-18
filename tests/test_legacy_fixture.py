"""The committed 9423c53 fixtures load as genuine v2 databases."""
from contextlib import closing
import sqlite3

from leadworkbench.db import Store
from legacy_db import load_legacy


def test_legacy_fixture_loads_as_v2_and_opens(tmp_path):
    path = load_legacy(tmp_path)
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    store = Store(tmp_path)
    with store.connect() as con:
        bob = con.execute("""SELECT c.*,d.adoption_status FROM contacts c JOIN contact_decisions d ON d.contact_id=c.id
            WHERE c.email='bob@alpha.example'""").fetchone()
    # QA-F1 as produced by the old code: the row holds the rejected value and is excluded.
    assert bob["adoption_status"] == "excluded" and bob["review_status"] == "reject"


def test_export_source_fixture_has_no_pending_contact_conflicts(tmp_path):
    path = load_legacy(tmp_path, "legacy_export_source.sql")
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not con.execute("SELECT 1 FROM reviews WHERE kind='contact' AND status IN ('pending','reject')").fetchone()
