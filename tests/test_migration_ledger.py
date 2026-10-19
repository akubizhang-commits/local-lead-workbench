"""Unified migration ledger (P1-9, design §3.2)."""
import json

import pytest

from leadworkbench.db import Store
from legacy_db import FIXTURES, UPGRADE_FIXTURES, dump_database, load_upgrade_fixture, normalize

# Differences the refactor is allowed to introduce; everything else must equal the snapshot.
EXPECTED_NEW_TABLES: set[str] = set()
EXPECTED_NEW_LEDGER: set[str] = set()


def _snapshot(name):
    return json.loads((FIXTURES / f"expected_upgrade_{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", UPGRADE_FIXTURES)
def test_0a_upgrade_matches_frozen_snapshot(tmp_path, name):
    snapshot = _snapshot(name)
    path = load_upgrade_fixture(tmp_path, name)
    Store(tmp_path)
    after = normalize(dump_database(path), snapshot["volatile"])
    assert set(after["sqlite_master_names"]) == set(snapshot["sqlite_master_names"]) | EXPECTED_NEW_TABLES
    assert set(after["ledger_ids"]) == set(snapshot["ledger_ids"]) | EXPECTED_NEW_LEDGER
    for table in EXPECTED_NEW_TABLES:
        assert after["tables"].pop(table) == []
    assert after["tables"] == snapshot["tables"]
