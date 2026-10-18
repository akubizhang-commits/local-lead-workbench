"""P0-2: a contact change is a proposal; rejecting it keeps the previous contact (design §3.8)."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from leadworkbench.api import create_app
from leadworkbench.db import Conflict, Store, STALE_PROPOSAL
from leadworkbench.export import export_data

BOB = dict(name="Bob Buyer", role="Buyer", email="bob@alpha.example", kind="procurement", role_status="explicit",
           association_status="same_block", source_url="https://alpha.example/team", evidence="Bob Buyer | Buyer")
ROBERT = {**BOB, "name": "Robert Other", "role": "Sales Rep", "kind": "general",
          "source_url": "https://alpha.example/about", "evidence": "Robert Other | Sales Rep"}
CAROL = {**ROBERT, "name": "Carol Other", "evidence": "Carol Other | Sales Rep"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def company(store):
    return store.manual_company("Alpha", "https://alpha.example")["id"]


def collect(store, cid, *contacts, run_id=""):
    store.apply_enrichment(cid, dict(status="completed", contacts=[dict(c) for c in contacts]), run_id)


def contact_reviews(store):
    return [r for r in store.reviews() if r["kind"] == "contact"]


def exported_names(store):
    book = load_workbook(io.BytesIO(export_data(store, "xlsx")))
    return [row[1] for row in list(book["Contacts"].values)[1:]]


def bob_visible(store, cid):
    contacts = store.detail(cid)["contacts"]
    assert [(c["name"], c["role"]) for c in contacts if c.get("adoption_status") != "excluded"] == [("Bob Buyer", "Buyer")]
    assert store.company_page()["items"][0]["procurement_count"] == 1
    assert "Bob Buyer" in exported_names(store)


def test_qa_f1_replay_keeps_bob_while_pending_and_after_reject(tmp_path, store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    [review] = contact_reviews(store)
    assert review["contact_change"] == "proposal"
    assert review["previous_contact"]["name"] == "Bob Buyer" and review["contact"]["name"] == "Robert Other"
    bob_visible(store, cid)
    assert store.review_decision(review["id"], "reject") == {"ok": True, "auto_closed": 0, "kept_previous": True}
    bob_visible(store, cid)
    reopened = Store(tmp_path)
    bob_visible(reopened, cid)
    with reopened.connect() as con:
        assert not con.execute("SELECT 1 FROM contact_decisions WHERE adoption_status='excluded'").fetchone()
    collect(reopened, cid, ROBERT)
    collect(reopened, cid, BOB)
    assert contact_reviews(reopened) == []
    bob_visible(reopened, cid)


def test_accept_writes_the_proposal(store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    store.review_decision(contact_reviews(store)[0]["id"], "accept")
    [contact] = store.detail(cid)["contacts"]
    assert (contact["name"], contact["role"], contact["adoption_status"], contact["review_status"]) == \
        ("Robert Other", "Sales Rep", "accepted", "accept")


def test_reclassified_contact_makes_accept_stale_and_hides_review(store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    review = contact_reviews(store)[0]
    with store.connect() as con:
        con.execute("UPDATE contacts SET name='Accept cookies now' WHERE email='bob@alpha.example'")
    store.reclassify_contacts(cid)
    with pytest.raises(Conflict):
        store.review_decision(review["id"], "accept")
    assert contact_reviews(store) == []
    with store.connect() as con:
        assert con.execute("SELECT status FROM contact_change_proposals").fetchone()[0] == "superseded"
        evidence = con.execute("SELECT count(*) FROM review_evidence WHERE review_id=?", (review["id"],)).fetchone()[0]
    collect(store, cid, ROBERT)
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM review_evidence WHERE review_id=?", (review["id"],)).fetchone()[0] == evidence


def test_rebase_after_new_evidence_or_filled_role_still_accepts(store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    collect(store, cid, {**BOB, "evidence": "Bob Buyer on another page"})
    review = contact_reviews(store)[0]
    assert json.loads(review["old_value"])["evidence"] == "Bob Buyer on another page"
    store.review_decision(review["id"], "accept")
    assert store.detail(cid)["contacts"][0]["name"] == "Robert Other"

    other = store.manual_company("Beta", "https://beta.example")["id"]
    nameless_role = {**BOB, "role": "", "email": "bob@beta.example", "source_url": "https://beta.example/team"}
    collect(store, other, nameless_role)
    collect(store, other, {**ROBERT, "email": "bob@beta.example"})
    collect(store, other, {**BOB, "email": "bob@beta.example", "source_url": "https://beta.example/team"})
    review = contact_reviews(store)[0]
    store.review_decision(review["id"], "accept")
    assert store.detail(other)["contacts"][0]["role"] == "Sales Rep"


def test_identity_change_makes_accept_stale_and_commits_supersede(store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    review = contact_reviews(store)[0]
    with store.connect() as con:
        con.execute("UPDATE contacts SET role='Head Buyer' WHERE email='bob@alpha.example'")
    with pytest.raises(Conflict, match=STALE_PROPOSAL):
        store.review_decision(review["id"], "accept")
    with store.connect() as con:
        assert con.execute("SELECT status FROM reviews WHERE id=?", (review["id"],)).fetchone()[0] == "superseded"
        assert con.execute("SELECT status FROM contact_change_proposals").fetchone()[0] == "superseded"


def test_rejected_value_again_does_not_count_as_market_change(store):
    market = store.create_market(dict(country="US", state="California", city="San Jose", keywords=["roofing"],
                                      company_types=[], auto_enrich=False))
    run = store.start_task(market["id"])
    from leadworkbench.sources.maps import _record
    listing = _record(dict(title="Alpha", link="https://www.google.com/maps/search/?api=1&query_place_id=P1",
                           place_id="P1", web_site="https://alpha.example"), "2026-10-01T00:00:00+00:00")
    cid = store.ingest_market(listing, run["id"], "roofing", market["id"])["id"]
    collect(store, cid, BOB, run_id=run["id"])
    collect(store, cid, ROBERT, run_id=run["id"])
    store.review_decision(contact_reviews(store)[0]["id"], "reject")
    with store.connect() as con:
        con.execute("UPDATE run_companies SET changed=0")
    collect(store, cid, ROBERT, run_id=run["id"])
    with store.connect() as con:
        assert con.execute("SELECT changed FROM run_companies WHERE company_id=?", (cid,)).fetchone()[0] == 0


def test_review_fingerprints_never_collide(store):
    cid = company(store)
    collect(store, cid, BOB)
    collect(store, cid, ROBERT)
    collect(store, cid, CAROL)  # same page, different value
    [review] = contact_reviews(store)
    assert review["contact"]["name"] == "Carol Other"
    assert review["previous_contact"]["name"] == "Bob Buyer"
    collect(store, cid, ROBERT)  # Robert was superseded, not rejected: a new pending review
    [review] = contact_reviews(store)
    assert review["contact"]["name"] == "Robert Other"
    collect(store, cid, ROBERT)  # same proposal, same page, same snippet
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM review_evidence WHERE review_id=?", (review["id"],)).fetchone()[0] == 1


def test_excluded_contact_conflict_creates_no_proposal(store):
    cid = company(store)
    collect(store, cid, BOB)
    with store.connect() as con:
        con.execute("INSERT INTO contact_decisions SELECT id,'excluded','2026' FROM contacts")
    collect(store, cid, ROBERT)
    assert contact_reviews(store) == []


def legacy_conflict(store, cid):
    """The pre-P0 shape: the row was overwritten with Robert and set to pending."""
    collect(store, cid, BOB)
    with store.connect() as con:
        bob = dict(con.execute("SELECT * FROM contacts").fetchone())
        con.execute("INSERT INTO contact_versions VALUES('legacy-v',?,?,?)", (bob["id"], json.dumps(bob), bob["observed_at"]))
        con.execute("""UPDATE contacts SET name='Robert Other',role='Sales Rep',kind='general',evidence='Robert',
            source_url='https://alpha.example/about',review_status='pending' WHERE id=?""", (bob["id"],))
        old = json.dumps({k: bob[k] for k in ("name", "role", "kind", "role_status", "association_status", "evidence")})
        con.execute("INSERT INTO reviews VALUES('legacy-r',?,'contact','contact',?,?,?,?,'pending','2026','fp-legacy')",
                    (cid, old, bob["id"], "https://alpha.example/about", "Robert"))
    return bob


def test_legacy_conflict_reject_restores_bob(tmp_path, store):
    cid = company(store)
    legacy_conflict(store, cid)
    [review] = contact_reviews(store)
    assert review["contact_change"] == "legacy"
    collect(store, cid, CAROL)  # still pending: no new review on top of the legacy one
    assert len(contact_reviews(store)) == 1
    assert store.review_decision("legacy-r", "reject")["kept_previous"] is True
    bob_visible(store, cid)
    with store.connect() as con:
        assert tuple(con.execute("SELECT origin,status FROM contact_change_proposals").fetchone()) == ("legacy", "rejected")
    bob_visible(Store(tmp_path), cid)


def test_legacy_conflict_accept_keeps_new_value(store):
    cid = company(store)
    legacy_conflict(store, cid)
    store.review_decision("legacy-r", "accept")
    [contact] = store.detail(cid)["contacts"]
    assert (contact["name"], contact["adoption_status"]) == ("Robert Other", "accepted")


def test_api_reject_and_stale_accept(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        store = app.state.store
        cid = company(store)
        collect(store, cid, BOB)
        collect(store, cid, ROBERT)
        review = contact_reviews(store)[0]
        response = client.post(f"/api/reviews/{review['id']}/decision", json={"decision": "reject"})
        assert response.status_code == 200 and response.json()["kept_previous"] is True
        collect(store, cid, CAROL)
        review = contact_reviews(store)[0]
        with store.connect() as con:
            con.execute("UPDATE contacts SET role='Head Buyer'")
        response = client.post(f"/api/reviews/{review['id']}/decision", json={"decision": "accept"})
        assert response.status_code == 409 and response.json()["detail"] == STALE_PROPOSAL
