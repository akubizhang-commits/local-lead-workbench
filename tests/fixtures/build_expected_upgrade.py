"""Freeze the upgrade results of the legacy fixtures (P1-9 equivalence snapshot, design §3.2).

Must run on the unrefactored migration code (913ee04). Each fixture is upgraded twice
in fresh directories with ``Store(dir)``; columns that differ between the two runs are
recorded as ``volatile`` and replaced by ``<volatile>`` before rows are sorted.
Outputs: tests/fixtures/expected_upgrade_{v1,v2_early,v2_9423c53}.json
Usage: .venv/bin/python tests/fixtures/build_expected_upgrade.py
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from legacy_db import UPGRADE_FIXTURES, dump_database, load_upgrade_fixture, normalize, volatile_columns  # noqa: E402

COMMIT = "913ee04"


def upgrade(name):
    from leadworkbench.db import Store
    with tempfile.TemporaryDirectory() as folder:
        path = load_upgrade_fixture(folder, name)
        Store(folder)
        return dump_database(path)


def main():
    for name in UPGRADE_FIXTURES:
        first, second = upgrade(name), upgrade(name)
        volatile = volatile_columns(first, second)
        snapshot = dict(generated_by="tests/fixtures/build_expected_upgrade.py", commit=COMMIT, fixture=name,
                        volatile=volatile, **normalize(first, volatile))
        assert normalize(second, volatile) == normalize(first, volatile)
        (HERE / f"expected_upgrade_{name}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
