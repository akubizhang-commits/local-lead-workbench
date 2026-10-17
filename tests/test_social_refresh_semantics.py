"""Cross-operation and successive observation regressions, using a real store."""
import json
import uuid

import pytest

from leadworkbench.db import Store
from leadworkbench.social_connections import private_write


@pytest.fixture
def account(tmp_path):
    store = Store(tmp_path)
    cid = store.manual_company("Fixture Brand", "https://brand.example")["id"]
    entry = store.add_social(cid, "https://x.com/brand", "Fixture")
    return store, cid, entry["id"]


def observe(account, payload, day):
    store, cid, eid = account
    run_id = store.create_social_run([cid], profile_only=True)["id"]
    with store.connect() as con:
        con.execute(
            "INSERT INTO social_page_observations VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, eid, run_id, json.dumps(payload), f"2026-09-{day}T00:00:00+00:00"),
        )


def test_distinct_actions_do_not_reuse_profile_job(account):
    store, cid, _ = account
    profile = store.create_social_run([cid], profile_only=True)
    search = store.create_social_run([cid], platform_search=True)
    read = store.create_social_run([cid], read_only=True)
    assert len({r["id"] for r in (profile, search, read)}) == 3
    assert {i["stage"] for i in search["items"]} == {"platform_search"}
    assert store.create_social_run([cid], profile_only=True)["id"] == profile["id"]


def test_changed_execution_settings_get_a_new_job(account):
    store, cid, _ = account
    first = store.create_social_run([cid], read_only=True)
    private_write(store.directory / "social-connections.json", {"reader": "browser", "max_posts": 5})
    second = store.create_social_run([cid], read_only=True)
    assert first["id"] != second["id"]
    assert store.create_social_run([cid], read_only=True)["id"] == second["id"]


def test_company_order_does_not_change_job_identity(account):
    store, cid, _ = account
    other = store.manual_company("Other fixture", "https://other.example")["id"]
    first = store.create_social_run([cid, other], platform_search=True)
    assert store.create_social_run([other, cid, cid], platform_search=True)["id"] == first["id"]


def test_sparse_success_preserves_fields_and_their_actual_dates(account):
    observe(account, {
        "status": "preview_read", "title": "Brand", "description": "Wholesale",
        "source_url": "https://x.com/brand", "provider": "browser",
        "fields": [
            {"key": "email", "value": "hello@brand.example", "evidence": "Email us"},
            {"key": "website", "value": "https://brand.example"},
        ],
    }, "20")
    observe(account, {
        "status": "preview_read", "title": "", "description": "",
        "fields": [], "posts": [{"url": "https://x.com/brand/status/1", "text": "New post"}],
    }, "23")
    page = account[0].social_pages(account[1])[0]
    assert page["title"] == "Brand"
    assert page["description"] == "Wholesale"
    assert page["posts"][0]["text"] == "New post"
    email = next(f for f in page["fields"] if f["key"] == "email")
    assert email["value"] == "hello@brand.example"
    assert email["source_url"] == "https://x.com/brand"
    assert email["observed_at"].startswith("2026-09-20")
    assert email["retained"] is True
    assert page["match_signals"]
    assert page["content_sources"]["description"]["observed_at"].startswith("2026-09-20")


def test_changed_field_keeps_previous_evidence_separate(account):
    observe(account, {
        "status": "preview_read", "source_url": "https://x.com/brand",
        "fields": [{"key": "email", "value": "old@brand.example", "evidence": "Old bio"},
                   {"key": "followers", "value": "100"}],
    }, "20")
    observe(account, {
        "status": "preview_read", "source_url": "https://x.com/brand",
        "fields": [{"key": "email", "value": "new@brand.example", "evidence": "New bio"},
                   {"key": "email", "value": "sales@brand.example", "evidence": "New bio"},
                   {"key": "followers", "value": "110"}],
    }, "23")
    page = account[0].social_pages(account[1])[0]
    assert {f["value"] for f in page["fields"] if f["key"] == "email"} == {
        "new@brand.example", "sales@brand.example"
    }
    assert next(f for f in page["fields"] if f["key"] == "followers")["value"] == "110"
    old = next(f for f in page["field_history"] if f["value"] == "old@brand.example")
    assert old["evidence"] == "Old bio"
    assert old["observed_at"].startswith("2026-09-20")


def test_failed_read_does_not_promote_untrusted_fields_or_refresh_old_date(account):
    observe(account, {
        "status": "preview_read", "title": "Brand",
        "fields": [{"key": "email", "value": "hello@brand.example"}],
    }, "20")
    observe(account, {
        "status": "auth_required", "message": "Login required",
        "fields": [{"key": "email", "value": "login@platform.example"}],
    }, "23")
    page = account[0].social_pages(account[1])[0]
    assert page["status"] == "auth_required"
    assert page["content_observed_at"].startswith("2026-09-20")
    assert [f["value"] for f in page["fields"]] == ["hello@brand.example"]
    assert page["fields"][0]["observed_at"].startswith("2026-09-20")


def test_empty_field_values_do_not_erase_previous_values(account):
    observe(account, {"status": "preview_read", "fields": [
        {"key": "email", "value": "hello@brand.example"}]}, "20")
    observe(account, {"status": "preview_read", "fields": [
        {"key": "email", "value": " "}]}, "23")
    page = account[0].social_pages(account[1])[0]
    assert [f["value"] for f in page["fields"]] == ["hello@brand.example"]


def test_failed_first_read_has_no_account_fields(account):
    observe(account, {
        "status": "auth_required", "title": "Login",
        "fields": [{"key": "email", "value": "help@platform.example"}],
    }, "23")
    page = account[0].social_pages(account[1])[0]
    assert page["fields"] == []
    assert page["field_history"] == []
    assert not page.get("title")
    assert not page.get("content_observed_at")


def test_projection_does_not_rewrite_observation_evidence(account):
    observe(account, {
        "status": "preview_read", "title": "Brand",
        "fields": [{"key": "email", "value": "hello@brand.example"}],
    }, "20")
    store, cid, _ = account
    with store.connect() as con:
        before = [tuple(row) for row in con.execute("SELECT * FROM social_page_observations")]
    first = store.social_pages(cid)
    assert first == store.social_pages(cid)
    with store.connect() as con:
        assert before == [tuple(row) for row in con.execute("SELECT * FROM social_page_observations")]


def test_api_keeps_actions_distinct_and_exposes_retained_field_provenance(account):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app

    observe(account, {
        "status": "preview_read", "source_url": "https://x.com/brand",
        "fields": [{"key": "email", "value": "hello@brand.example"}],
    }, "20")
    observe(account, {"status": "preview_read", "fields": []}, "23")
    store, cid, _ = account
    with TestClient(create_app(store.directory, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        profile = client.post("/api/social/runs", json={"company_ids": [cid], "profile_only": True})
        search = client.post("/api/social/runs", json={"company_ids": [cid], "platform_search": True})
        assert profile.status_code == search.status_code == 200
        assert profile.json()["id"] != search.json()["id"]
        detail = client.get(f"/api/companies/{cid}/social")
        assert detail.status_code == 200
        field = detail.json()["pages"][0]["fields"][0]
        assert field["retained"] is True
        assert field["observed_at"].startswith("2026-09-20")


@pytest.mark.asyncio
async def test_shared_account_cache_does_not_create_a_new_observation(account, monkeypatch):
    from leadworkbench.social_worker import process_social_item

    store, cid, eid = account
    other = store.manual_company("Other location", "https://other.example")["id"]
    store.add_social(other, "https://x.com/brand", "Shared account fixture")
    run = store.create_social_run([cid, other], read_only=True)
    calls = []

    async def read(url, stage):
        calls.append(url)
        return {"status": "preview_read", "title": "Brand", "fields": [
            {"key": "email", "value": "hello@brand.example"}]}

    monkeypatch.setattr("leadworkbench.social_discovery_store.timestamp",
                        lambda: "2026-09-23T10:00:00+00:00")
    assert await process_social_item(store, read=read)
    monkeypatch.setattr("leadworkbench.social_discovery_store.timestamp",
                        lambda: "2026-09-23T10:10:00+00:00")
    assert await process_social_item(store, read=read)
    assert len(calls) == 1
    assert store.social_run(run["id"])["summary"]["succeeded"] == 2
    with store.connect() as con:
        assert con.execute(
            "SELECT count(*) FROM social_page_observations WHERE entity_id=?", (eid,),
        ).fetchone()[0] == 1
    for company in (cid, other):
        assert store.social_pages(company)[0]["fields"][0]["observed_at"] == "2026-09-23T10:00:00+00:00"


def test_legacy_cached_snapshots_do_not_change_actual_observation_dates(account):
    payload = {"status": "preview_read", "title": "Brand", "fields": [
        {"key": "email", "value": "hello@brand.example"}]}
    observe(account, payload, "20")
    observe(account, {**payload, "cache_reused": True}, "23")
    page = account[0].social_pages(account[1])[0]
    assert page["fields"][0]["observed_at"].startswith("2026-09-20")
    assert page["content_observed_at"].startswith("2026-09-20")
    assert page["observed_at"].startswith("2026-09-20")


def test_cache_without_original_source_is_not_presented_as_new_evidence(account):
    observe(account, {
        "status": "preview_read", "cache_reused": True, "title": "Brand",
        "fields": [{"key": "email", "value": "hello@brand.example"}],
    }, "23")
    page = account[0].social_pages(account[1])[0]
    assert page["status"] == "unreadable"
    assert not page["fields"]
    assert not page.get("content_observed_at")
