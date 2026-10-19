"""P1-2: /api/reviews and /api/companies pagination with backward compatibility (design §8.8)."""
from itertools import groupby
import json

import pytest
from fastapi.testclient import TestClient

from leadworkbench.api import create_app
from leadworkbench.company_comparison import brief, website_key
from leadworkbench.db import Store
from legacy_db import load_upgrade_fixture


def legacy_reviews(store):
    """The pre-P1 Store.reviews() (N+1 queries, ORDER BY created_at DESC only), kept as the reference."""
    from leadworkbench.contact_fields import contact_fields
    with store.connect() as con:
        reviews = [dict(r) for r in con.execute("""SELECT r.*,c.name company_name FROM reviews r
            JOIN companies c ON c.id=r.company_id WHERE r.status='pending' ORDER BY r.created_at DESC""")]
        for review in reviews:
            review["evidence_count"] = max(1, con.execute(
                "SELECT count(*) FROM review_evidence WHERE review_id=?", (review["id"],)).fetchone()[0])
            review["evidence_items"] = [dict(e) for e in con.execute(
                """SELECT source_url,evidence,observed_at FROM review_evidence WHERE review_id=?
                   ORDER BY observed_at DESC,rowid DESC LIMIT 5""", (review["id"],))]
            if review["kind"] == "contact":
                row = con.execute("SELECT * FROM contacts WHERE id=? AND company_id=?",
                                  (review["new_value"], review["company_id"])).fetchone()
                proposal = con.execute("SELECT proposal FROM contact_change_proposals WHERE review_id=?",
                                       (review["id"],)).fetchone()
                if proposal:
                    review["contact_change"] = "proposal"
                elif (review["old_value"] or "") not in ("", "{}"):
                    review["contact_change"] = "legacy"
                else:
                    review["contact_change"] = "new"
                review["contact"] = {**dict(row), **json.loads(proposal[0])} if row and proposal else dict(row) if row else None
                if row:
                    website = con.execute("SELECT website FROM companies WHERE id=?", (review["company_id"],)).fetchone()[0]
                    review["contact"]["fields"] = contact_fields(review["contact"], website)
                try:
                    previous = json.loads(review["old_value"] or "{}")
                except ValueError:
                    previous = {}
                review["previous_contact"] = previous if isinstance(previous, dict) else {}
            if review["kind"] == "duplicate_candidate":
                left = brief(con.execute("SELECT * FROM companies WHERE id=?", (review["old_value"],)).fetchone())
                right = brief(con.execute("SELECT * FROM companies WHERE id=?", (review["new_value"],)).fetchone())
                key = website_key(left["website"]) if left else ""
                same = bool(key and right and key == website_key(right["website"]))
                review["comparison"] = {"left": left, "right": right, "same_website": same,
                                        "website_group": key if same else ""}
        return reviews


@pytest.fixture
def api(tmp_path):
    load_upgrade_fixture(tmp_path, "v2_9423c53")
    store = Store(tmp_path)
    with store.connect() as con:
        companies = [r[0] for r in con.execute("SELECT id FROM companies ORDER BY id")]
        # Many pending reviews sharing created_at values, plus a duplicate candidate and evidence.
        for i in range(45):
            con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,status,
                created_at,fingerprint) VALUES(?,?,'field_update','notes','',?,'u','e','pending',?,?)""",
                        (f"pg{i:02d}", companies[i % len(companies)], f"v{i}", f"2026-10-18T10:00:0{i % 4}+00:00",
                         f"fp-pg{i}"))
            con.execute("INSERT INTO review_evidence VALUES(?,?,?,?,?)",
                        (f"pg{i:02d}", f"ev-pg{i}", "u", f"evidence {i}", f"2026-10-18T10:00:0{i % 4}+00:00"))
        con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,status,
            created_at,fingerprint) VALUES('dupx',?,'duplicate_candidate','company',?,?,'u','e','pending',
            '2026-10-18T10:00:01+00:00','fp-dupx')""", (companies[0], companies[0], companies[1]))
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    client = TestClient(app)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    yield client, store


def by_created(rows):
    return [(key, sorted(json.dumps(r, sort_keys=True) for r in group))
            for key, group in groupby(rows, key=lambda r: r["created_at"])]


def test_reviews_array_matches_legacy(api):
    client, store = api
    response = client.get("/api/reviews")
    rows = response.json()
    assert isinstance(rows, list) and int(response.headers["X-Total-Count"]) == len(rows)
    assert by_created(rows) == by_created(legacy_reviews(store))
    kinds = {r["kind"] for r in rows}
    assert {"contact", "field_update", "duplicate_candidate"} <= kinds


def test_reviews_pages_cover_everything_once(api):
    client, store = api
    full = client.get("/api/reviews").json()
    first = client.get("/api/reviews?page=1").json()
    assert set(first) == {"items", "total", "page", "page_size"}
    assert first["page_size"] == 20 and first["total"] == len(full)
    seen = []
    page = 1
    while True:
        body = client.get(f"/api/reviews?page={page}&page_size=7").json()
        if not body["items"]:
            break
        seen += [r["id"] for r in body["items"]]
        page += 1
    assert seen == [r["id"] for r in full] and len(set(seen)) == len(seen)
    beyond = client.get("/api/reviews?page=999&page_size=20").json()
    assert beyond["items"] == [] and beyond["total"] == len(full)
    assert client.get("/api/reviews?page_size=201").status_code == 422
    assert client.get("/api/reviews?page=0").status_code == 422


def test_reviews_kind_filter(api):
    client, store = api
    body = client.get("/api/reviews?page=1&page_size=200&kind=duplicate_candidate").json()
    assert body["total"] == len(body["items"]) >= 1
    assert {r["kind"] for r in body["items"]} == {"duplicate_candidate"}
    contacts = client.get("/api/reviews?kind=contact").json()
    assert contacts and {r["kind"] for r in contacts} == {"contact"}


def test_companies_paging_matches_company_page(api):
    client, store = api
    rows = client.get("/api/companies")
    assert isinstance(rows.json(), list) and rows.headers["X-Total-Count"] == str(len(rows.json()))
    paged = client.get("/api/companies?page=1&page_size=2").json()
    assert paged == client.get("/api/company-page?page=1&page_size=2").json()
    assert paged["total"] == len(rows.json())
    assert client.get("/api/companies?page_size=201").status_code == 422
