"""Same-value field proposals merge into one review; decisions close stale siblings."""
import json

from leadworkbench.db import Store, uid


def observation(page, value="Acme Fixture", field="name"):
    return dict(field=field, value=value, source_url=f"https://acme.example/page-{page}",
                evidence=f"{value} on page {page}")


def scan(store, company_id, pages, **kwargs):
    store.apply_enrichment(company_id, dict(status="completed", contacts=[],
                                            observations=[observation(p, **kwargs) for p in pages]))


def pending(store, company_id, field="name"):
    return [r for r in store.reviews() if r["company_id"] == company_id and r["field"] == field]


# "Acme Fixture Inc." -> "Acme Fixture" only drops a legal suffix, which is now an equivalent
# website name (design §4.4 step 4); the stored name carries an extra word so proposals stay real.
OLD = "Acme Fixture Supply Inc."


def test_88_pages_with_same_rename_yield_one_review_with_all_evidence(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company(OLD, "https://acme.example")
    scan(store, company["id"], range(88))
    reviews = pending(store, company["id"])
    assert len(reviews) == 1
    assert reviews[0]["evidence_count"] == 88
    assert len(reviews[0]["evidence_items"]) == 5
    assert reviews[0]["evidence_items"][0]["source_url"].startswith("https://acme.example/page-")
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM changes WHERE company_id=? AND field='name'",
                           (company["id"],)).fetchone()[0] == 1


def test_accept_closes_same_value_and_rebases_competing_proposal(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company(OLD, "https://acme.example")
    scan(store, company["id"], range(3))
    scan(store, company["id"], [10], value="Acme Fixture Wholesale")
    # Simulate a legacy duplicate that predates merging.
    with store.connect() as con:
        con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid(), company["id"], "field_update", "name", OLD, "acme  fixture",
                     "https://acme.example/legacy", "legacy", "pending", "2999-01-01", uid()))
    target = next(r for r in pending(store, company["id"]) if r["new_value"] == "Acme Fixture")
    assert store.review_decision(target["id"], "accept") == 1
    left = pending(store, company["id"])
    assert [r["new_value"] for r in left] == ["Acme Fixture Wholesale"]
    assert left[0]["old_value"] == "Acme Fixture"
    assert store.review_decision(left[0]["id"], "accept") == 0
    assert store.company(company["id"])["name"] == "Acme Fixture Wholesale"


def test_reject_closes_same_value_and_rescan_does_not_reopen(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company(OLD, "https://acme.example")
    scan(store, company["id"], range(5))
    review = pending(store, company["id"])[0]
    assert store.review_decision(review["id"], "reject") == 0
    scan(store, company["id"], range(5))
    assert pending(store, company["id"]) == []


def test_business_types_in_different_order_are_the_same_proposal(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Acme Fixture Inc.", "https://acme.example")
    store.update_company(company["id"], store.company(company["id"])["revision"], {}, business_types=["importer"])
    store.apply_enrichment(company["id"], dict(status="completed", contacts=[], observations=[
        dict(field="business_types", value=["retailer", "wholesaler"], source_url="https://acme.example/a", evidence="a"),
        dict(field="business_types", value=["wholesaler", "retailer"], source_url="https://acme.example/b", evidence="b"),
    ]))
    reviews = pending(store, company["id"], "business_types")
    assert len(reviews) == 1 and reviews[0]["evidence_count"] == 2


def test_legacy_same_value_reviews_are_folded_once_on_startup(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company("Acme Fixture Inc.", "https://acme.example")
    with store.connect() as con:
        for n in range(5):
            con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (uid(), company["id"], "field_update", "name", "Acme Fixture Inc.", "Acme Fixture",
                         f"https://acme.example/{n}", f"page {n}", "pending", f"2026-01-0{n + 1}", uid()))
        con.execute("DELETE FROM review_evidence")
    for _ in range(2):
        store = Store(tmp_path)
        reviews = pending(store, company["id"])
        assert len(reviews) == 1 and reviews[0]["evidence_count"] == 5
        assert reviews[0]["created_at"] == "2026-01-01"
        with store.connect() as con:
            assert con.execute("SELECT count(*) FROM reviews WHERE status='auto_closed'").fetchone()[0] == 4


def test_api_decision_reports_auto_closed_and_never_409s(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    store = app.state.store
    company = store.manual_company(OLD, "https://acme.example")
    scan(store, company["id"], range(4))
    scan(store, company["id"], range(2), value="Acme Fixture LLC")
    with store.connect() as con:
        con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid(), company["id"], "field_update", "name", OLD, "ACME FIXTURE",
                     "https://acme.example/x", "x", "pending", "2999-01-01", uid()))
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        reviews = client.get("/api/reviews").json()["items"]
        assert len(reviews) == 3
        first = next(r for r in reviews if r["new_value"] == "Acme Fixture")
        response = client.post(f"/api/reviews/{first['id']}/decision", json={"decision": "accept"})
        assert response.status_code == 200 and response.json() == {"ok": True, "auto_closed": 1}
        for review in client.get("/api/reviews").json()["items"]:
            response = client.post(f"/api/reviews/{review['id']}/decision", json={"decision": "reject"})
            assert response.status_code == 200
        assert client.get("/api/reviews").json()["items"] == []
    assert json.loads(json.dumps(store.reviews())) == []
