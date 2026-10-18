"""P0-6: best entry tier, gap reasons, deduplicated entry counts, per-tier market stats (design §6.7)."""
import pytest

from leadworkbench.contact_tiers import company_best_rank, gap_reason
from leadworkbench.db import Store
from leadworkbench.sources.maps import _record


def test_company_best_rank_falls_back_to_phone_then_none():
    assert company_best_rank(2, "") == 2
    assert company_best_rank(None, "+1 408") == 7
    assert company_best_rank(None, " ") == 8


SCANNED = {"state": "succeeded", "result": {"summary": {"pages_checked": 12, "remaining": 0}}}


@pytest.mark.parametrize("company,rank,scan,code,reason", [
    (dict(website=""), 8, None, "no_website", "没有官网，无法从官网获取联系人"),
    (dict(website="https://a.example"), 5, None, "not_scanned", "官网尚未扫描"),
    (dict(website="https://a.example", last_checked_at="x"), 5,
     {"state": "partial", "result": {"summary": {"pages_checked": 3, "remaining": 9}}},
     "scan_incomplete", "官网扫描未全部完成（已读 3 页，还剩 9 个链接）"),
    (dict(website="https://a.example"), 5, {"state": "pending", "result": {}}, "scan_incomplete", "官网扫描未全部完成"),
    (dict(website="https://a.example", last_checked_at="x"), 5, SCANNED, "no_named_people", "官网已扫描 12 页，未公开具名人员"),
    (dict(website="https://a.example", last_checked_at="x"), 4, None, "no_named_people", "官网已扫描，未公开具名人员"),
    (dict(website="https://a.example", last_checked_at="x", named_count=2), 2, SCANNED, "no_procurement",
     "已有具名人员 2 位，但未找到采购岗位"),
])
def test_gap_reasons(company, rank, scan, code, reason):
    gap = gap_reason(company, rank, scan)
    assert (gap["code"], gap["reason"]) == (code, reason) and gap["next_step"]


def test_no_gap_for_named_procurement_or_refused_company():
    assert gap_reason(dict(website=""), 1, None) is None
    assert gap_reason(dict(website="", status="do_not_contact"), 5, None) is None


def contact(name="", role="", email="", kind="general", role_status="unknown", page="https://a.example/c",
            evidence=None):
    return dict(name=name, role=role, email=email, kind=kind, role_status=role_status,
                association_status="same_block" if name else "company_only", source_url=page,
                evidence=evidence or f"{name} {role} {email}".strip())


@pytest.fixture
def market(tmp_path):
    store = Store(tmp_path)
    task = store.create_market(dict(country="US", state="California", city="San Jose", keywords=["roofing"],
                                    company_types=[], auto_enrich=False))
    run = store.start_task(task["id"])

    def add(n, name, phone="", website="https://a.example", category="Roofing supply store"):
        record = _record(dict(title=name, link=f"https://www.google.com/maps/search/?api=1&query_place_id=P{n}",
                              place_id=f"P{n}", phone=phone, web_site=website, category=category),
                         "2026-10-01T00:00:00+00:00")
        return store.ingest_market(record, run["id"], "roofing", task["id"])["id"]
    return store, task, add


def test_best_tier_fields_filter_sort_and_stats(market):
    store, task, add = market
    buyer = add(1, "Buyer Co")
    store.apply_enrichment(buyer, dict(status="completed", contacts=[contact("Bob Buyer", "Buyer", "bob@a.example",
                                                                             "procurement", "explicit")]))
    general = add(2, "General Co")
    store.apply_enrichment(general, dict(status="completed", contacts=[contact(email="info@b.example")]))
    phone_only = add(3, "Phone Co", phone="+1 408-555-0100", website="")
    nothing = add(4, "Nothing Co", website="", category="Roofing contractor")
    page = {c["name"]: c for c in store.company_page()["items"]}
    assert (page["Buyer Co"]["best_tier"], page["Buyer Co"]["best_tier_label"], page["Buyer Co"]["gap"]) == \
        ("named_procurement", "具名采购", None)
    assert page["General Co"]["best_tier"] == "general_email"
    assert page["General Co"]["gap"]["reason"] == "官网已扫描，未公开具名人员"
    fresh = add(5, "Fresh Co")
    assert store.company(fresh)["gap"]["code"] == "not_scanned"
    with store.connect() as con:
        con.execute("DELETE FROM market_memberships WHERE company_id=?", (fresh,))
        con.execute("UPDATE companies SET merged_into=? WHERE id=?", (buyer, fresh))
    assert page["Phone Co"]["best_tier"] == "company_phone" and page["Phone Co"]["gap"]["code"] == "no_website"
    assert page["Nothing Co"]["best_tier_rank"] == 8
    assert [c["name"] for c in store.company_page(best_tier="company_phone")["items"]] == ["Phone Co"]
    assert [c["name"] for c in store.company_page(sort="tier")["items"]] == ["Buyer Co", "General Co", "Phone Co", "Nothing Co"]
    stats = store.task(task["id"])["tier_stats"]
    expected = {code: 0 for code in stats}
    for item in page.values():
        expected[item["best_tier"]] += 1
    assert stats == expected
    assert store.tasks()[0]["tier_stats"] == expected
    store.create_exclusion_rule("global", "category", "Roofing contractor")
    stats = store.task(task["id"])["tier_stats"]
    assert stats["none"] == 0 and store.task(task["id"])["market_stats"]["excluded"] == 1
    assert store.company_page(best_tier="none")["total"] == 0
    detail = store.detail(buyer)
    assert detail["company"]["best_tier"] == "named_procurement"
    assert detail["contacts"][0]["tier_label"] == "具名采购"
    assert store.detail(general)["company"]["gap"]["code"] == "no_named_people"


def test_entries_added_counts_distinct_new_entries(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Entry", "https://entry.example")["id"]
    added = store.apply_enrichment(company, dict(status="completed", contacts=[
        contact(email="info@entry.example", page="https://entry.example/a"),
        contact(email="info@entry.example", page="https://entry.example/b")]))
    assert added == {"entries_added": 1}
    assert store.apply_enrichment(company, dict(status="completed", contacts=[
        contact(email="info@entry.example", page="https://entry.example/c")])) == {"entries_added": 0}
    # Archived by reclassify right after ingestion: not a new entry.
    archived = store.apply_enrichment(company, dict(status="completed", contacts=[
        contact(name="Accept cookies now", role="Buyer", email="cookie@entry.example", kind="procurement",
                role_status="explicit", evidence="Accept cookies now | Buyer")]))
    assert archived == {"entries_added": 0}
