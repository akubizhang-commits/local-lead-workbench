"""contact_facets keep the established counting rules (design §1.2, risk R1)."""
import json
import threading

import pytest

from leadworkbench.contact_projection import contact_key, outreach_category
from leadworkbench.contact_quality import invalid_contact_channel_reason
from leadworkbench.contact_tiers import contact_tier, name_like_local
from leadworkbench.db import Store
from perf_fixtures import build


def legacy_counts(con):
    """The pre-P0 Python/UDF semantics, kept here as the reference implementation."""
    excluded = {r[0] for r in con.execute("SELECT contact_id FROM contact_decisions WHERE adoption_status='excluded'")}
    result = {}
    rows = con.execute("SELECT * FROM contacts").fetchall()
    usable = [dict(r) for r in rows if r["review_status"] not in {"pending", "reject", "invalid", "superseded"}
              and r["id"] not in excluded
              and not invalid_contact_channel_reason(r["kind"], r["email"], r["url"], r["evidence"])]

    def low(email):
        return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in str(email or "").strip(" "))

    by_company = {}
    for row in usable:
        row["category"] = outreach_category(row["kind"], row["role_status"], row["email"], row["role"])
        by_company.setdefault(row["company_id"], []).append(row)
    for company_id, items in by_company.items():
        buyers = {low(r["email"]) for r in items if r["category"] == "procurement"}
        key = lambda r: json.dumps(contact_key(r), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result[company_id] = dict(
            contact_count=len({key(r) for r in items if r["category"] != "irrelevant"}),
            email_count=len({low(r["email"]) for r in items if r["category"] == "email" and low(r["email"]) not in buyers}),
            procurement_count=len({low(r["email"]) if str(r["email"] or "").strip(" ") else key(r)
                                   for r in items if r["category"] == "procurement"}))
    return result


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    store = build(tmp_path_factory.mktemp("facets"), 5000, 5)
    with store.connect() as con:
        # Edge rows the synthetic shape does not produce on its own.
        con.execute("UPDATE contacts SET review_status='pending' WHERE id LIKE 'k0000%1'")
        con.execute("UPDATE contacts SET review_status='reject' WHERE id LIKE 'k0001%2'")
        con.execute("INSERT INTO contact_decisions SELECT id,'excluded','2026' FROM contacts WHERE id LIKE 'k0002%0'")
        con.execute("UPDATE contacts SET email='broken@@x' WHERE id LIKE 'k0003%3'")
        con.execute("UPDATE contacts SET role='Recruiter',kind='procurement' WHERE id LIKE 'k0004%0'")
        con.execute("UPDATE contacts SET email='MiXeD.Case@Co5.Example ' WHERE id LIKE 'k0005%3'")
        con.execute("UPDATE contacts SET email=(SELECT email FROM contacts b WHERE b.id=substr(contacts.id,1,7)||'0')"
                    " WHERE id LIKE 'k0006%1'")
    return store


def test_facet_counts_match_legacy_python_rules_on_5k(synthetic):
    page = synthetic.companies(max_rows=None)
    with synthetic.connect() as con:
        expected = legacy_counts(con)
    assert len(page) == 5000
    mismatches = [c["id"] for c in page if {k: c[k] for k in ("contact_count", "email_count", "procurement_count")}
                  != expected.get(c["id"], dict(contact_count=0, email_count=0, procurement_count=0))]
    assert mismatches == []
    with synthetic.connect() as con:
        assert synthetic.contact_count(con) == sum(v["contact_count"] for v in expected.values())
        assert con.execute("SELECT count(*) FROM contact_facets").fetchone()[0] == \
            con.execute("SELECT count(*) FROM contacts").fetchone()[0]


def test_direct_insert_is_visible_to_read_entry_points(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Direct", "https://direct.example")
    with store.connect() as con:
        con.execute("""INSERT INTO contacts VALUES('x1',?,'Ann Buyer','Buyer','ann@direct.example','',
            'procurement','explicit','same_block','https://direct.example/t','Ann','2026','auto_recorded','fp-x1')""",
                    (company["id"],))
    item = store.company_page()["items"][0]
    assert (item["contact_count"], item["procurement_count"]) == (1, 1)
    assert store.company(company["id"])["contact_count"] == 1


def test_refresh_inside_open_transaction_is_rejected(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Guard", "https://guard.example")
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="inside a write transaction"):
            store.detail(company["id"])
    assert store.detail(company["id"])["company"]["id"] == company["id"]


def test_enrichment_and_page_reads_run_concurrently(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Busy", "https://busy.example")
    errors = []

    def write():
        try:
            for i in range(200):
                store.apply_enrichment(company["id"], dict(status="completed", contacts=[dict(
                    name="", email=f"box{i}@busy.example", kind="general", role_status="unknown",
                    association_status="company_only", source_url="https://busy.example/c",
                    evidence=f"box{i}@busy.example")]))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def read():
        try:
            for _ in range(200):
                store.company_page()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write), threading.Thread(target=read)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.company_page()["items"][0]["contact_count"] == 200


def tier(**contact):
    base = dict(name="", role="", email="", url="", kind="general", role_status="unknown",
                association_status="company_only")
    return contact_tier({**base, **contact})[0]


def test_tier_decision_table_branches():
    assert tier(name="Bob Buyer", role="Buyer", email="bob@x.example", kind="procurement", role_status="explicit",
                association_status="same_block") == "named_procurement"
    assert tier(name="Sam Seller", role="Sales Manager", role_status="explicit",
                association_status="same_block") == "named_role"
    assert tier(name="Pat", email="pat@x.example") == "named_unknown"
    assert tier(name="Ann Lee", role="Buyer", role_status="explicit", association_status="company_only") == "named_unknown"
    assert tier(name="Ann Lee", kind="procurement", role_status="department",
                association_status="same_block") == "named_unknown"
    assert tier(name="Old Boss", role="Former Director", role_status="historical",
                association_status="same_block") == "named_unknown"
    assert tier(email="Christian.Arredondo@x.example") == "named_unknown"
    assert tier(email="info@x.example") == "general_email"
    assert tier(email="purchasing@x.example") == "department_email"
    assert tier(email="desk@x.example", kind="procurement", role_status="department") == "department_email"
    assert tier(url="https://x.example/contact", kind="contact_form") == "form_phone"
    assert tier() == "form_phone"


@pytest.mark.parametrize("local,expected", [
    ("sales", "department_email"), ("orders", "department_email"), ("purchasing", "department_email"),
    ("customer.service", "department_email"), ("customer_care", "department_email"),
    ("online.orders", "department_email"),
    ("info", "general_email"), ("accounts.payable", "general_email"), ("front.desk", "general_email"),
    ("web.master", "general_email"),
])
def test_role_and_department_mailboxes_are_not_named(local, expected):
    assert not name_like_local(local)
    assert tier(email=f"{local}@x.example") == expected


@pytest.mark.parametrize("local", ["christian.arredondo", "john.smith"])
def test_person_style_mailboxes_are_named_unknown(local):
    assert name_like_local(local)
    assert tier(email=f"{local}@x.example") == "named_unknown"
