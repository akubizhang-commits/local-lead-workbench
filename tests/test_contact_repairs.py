"""D2=A: contacts wrongly excluded by the old "标记为不采用" are restored on upgrade (design §3.6)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from leadworkbench.db import Store
from leadworkbench.migrations import ONCE_IDS
from legacy_db import legacy_code_tmp, load_legacy


def contact(store, email):
    with store.connect() as con:
        return con.execute("""SELECT c.*,d.adoption_status FROM contacts c LEFT JOIN contact_decisions d
            ON d.contact_id=c.id WHERE c.email=?""", (email,)).fetchone()


def test_upgrade_restores_wrongly_excluded_contacts(tmp_path):
    load_legacy(tmp_path)
    store = Store(tmp_path)
    bob = contact(store, "bob@alpha.example")
    assert (bob["name"], bob["role"], bob["review_status"], bob["adoption_status"]) == \
        ("Bob Buyer", "Buyer", "auto_recorded", None)
    dana = contact(store, "dana@gamma.example")
    assert (dana["name"], dana["role"], dana["review_status"], dana["adoption_status"]) == \
        ("Dana Smith", "Purchasing Director", "accept", "accepted")
    carla = contact(store, "carl@delta.example")  # legacy pending conflict: untouched
    assert (carla["name"], carla["review_status"]) == ("Carla Other", "pending")
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with store.connect() as con:
        origins = [tuple(r) for r in con.execute("SELECT origin,status FROM contact_change_proposals ORDER BY rowid")]
        reasons = [r[0] for r in con.execute("SELECT reason FROM changes WHERE reason LIKE '数据修复%'")]
    assert origins == [("repair", "rejected"), ("repair", "rejected")]
    assert len(reasons) == 2 and backups[0].name in reasons[0]
    page = {c["name"]: c for c in store.company_page()["items"]}
    assert page["Alpha Roofing Supply"]["procurement_count"] == 1
    assert page["Gamma Sheet Metal"]["procurement_count"] == 1
    # Restarting changes nothing and never re-excludes.
    reopened = Store(tmp_path)
    assert contact(reopened, "bob@alpha.example")["adoption_status"] is None
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    with reopened.connect() as con:
        assert not con.execute("SELECT 1 FROM contact_decisions WHERE adoption_status='excluded'").fetchone()
    # The rejected value does not come back as a review.
    reopened.apply_enrichment(bob["company_id"], dict(status="completed", contacts=[dict(
        name="Robert Other", role="Sales Rep", email="bob@alpha.example", kind="general", role_status="explicit",
        association_status="same_block", source_url="https://alpha.example/about",
        evidence="Robert Other | Sales Rep | bob@alpha.example")]))
    assert not [r for r in reopened.reviews() if r["kind"] == "contact" and r["new_value"] == bob["id"]]


def test_backup_failure_skips_repair_only(tmp_path, monkeypatch):
    load_legacy(tmp_path)

    def broken(self, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Store, "backup", broken)
    store = Store(tmp_path)
    with store.connect() as con:
        ledger = [r[0] for r in con.execute("SELECT id FROM schema_migrations")]
    assert "p0_20261018_repair_rejected" not in ledger
    assert len(ledger) == len(ONCE_IDS) - 1
    assert contact(store, "bob@alpha.example")["adoption_status"] == "excluded"
    monkeypatch.undo()
    assert contact(Store(tmp_path), "bob@alpha.example")["adoption_status"] is None


def test_9423c53_code_can_still_open_and_write_upgraded_database(tmp_path):
    load_legacy(tmp_path)
    store = Store(tmp_path)
    company_id = contact(store, "alice@beta.example")["company_id"]
    code = legacy_code_tmp()
    if code is None:
        pytest.skip("git unavailable")
    script = f"""
import leadworkbench
assert 'legacy-code-' in leadworkbench.__file__
from leadworkbench.db import Store
Store({str(tmp_path)!r}).apply_enrichment({company_id!r}, dict(status='completed', contacts=[dict(
    name='Olga Old', role='Buyer', email='olga@beta.example', kind='procurement', role_status='explicit',
    association_status='same_block', source_url='https://beta.example/old', evidence='Olga Old | Buyer')]))
"""
    try:
        env = {**os.environ, "PYTHONPATH": str(code)}
        env.pop("MAPS_SCRAPER_BIN", None)
        subprocess.run([sys.executable, "-c", script], env=env, cwd=code, check=True)
    finally:
        shutil.rmtree(code, ignore_errors=True)
    store = Store(tmp_path)
    olga = contact(store, "olga@beta.example")
    assert store.company(company_id)["contact_count"] == 2
    with store.connect() as con:
        assert con.execute("SELECT tier FROM contact_facets WHERE contact_id=?", (olga["id"],)).fetchone()[0] == \
            "named_procurement"
