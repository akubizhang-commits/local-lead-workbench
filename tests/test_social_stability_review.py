"""Offline stability regressions: temporary stores, pure parsers, fake providers."""
import json
import sqlite3
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import nullcontext

import pytest

from leadworkbench.db import Store
from leadworkbench import social_policy
from leadworkbench.social_projection import project_account
from leadworkbench.sources import (
    social_browser, social_combination, social_discovery, social_optional,
    social_platform_search, social_profile,
)


PROFILE = "https://www.instagram.com/fixture/"
PREVIEW_HTML = (
    '<title>Fixture Apparel</title><meta property="og:title" content="Fixture Apparel">'
    '<meta property="og:description" content="Public business bio hello@brand.example">'
)


def manual_profile(store, name="Fixture", url=PROFILE):
    company = store.manual_company(name, f"https://{name.lower()}.example")
    store.add_social(company["id"], url, "Offline fixture")
    return store.create_social_run([company["id"]], profile_only=True)


def set_gate(store, platform="instagram", state="waiting_login", not_before=0):
    with store.connect() as con:
        con.execute("INSERT OR REPLACE INTO social_platform_gates VALUES(?,?,?,?,?)",
                    (platform, state, not_before, "Fixture restriction", 2))


@pytest.mark.parametrize("state,deadline", [
    ("waiting_login", 0), ("waiting_configuration", 0),
    ("waiting_recovery", 2000), ("ready", 2000),
])
def test_manual_read_respects_global_platform_gate(tmp_path, monkeypatch, state, deadline):
    monkeypatch.setattr(social_policy.time, "time", lambda: 1000)
    store = Store(tmp_path)
    run = manual_profile(store)
    set_gate(store, state=state, not_before=deadline)
    assert store.claim_social_item() is None
    assert store.social_run(run["id"])["summary"]["pending"] == 1


def test_manual_failure_updates_gate_for_other_runs(tmp_path):
    store = Store(tmp_path)
    manual_profile(store)
    item = store.claim_social_item()
    assert store.finish_social_item(item["id"], {"status": "challenge", "message": "Verification required"})
    manual_profile(store, "Second")
    with store.connect() as con:
        assert social_policy.gate(con, "instagram")["state"] == "waiting_login"
    assert store.claim_social_item() is None


def test_cached_preview_cannot_reset_manual_gate_or_retry_history():
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    ("instagram", "waiting_login", 0, "Login required", 3))
        before = social_policy.gate(con, "instagram")
        assert not social_policy.observe(con, {"stage": "profile", "target": PROFILE},
                                         {"status": "preview_read", "cache_reused": True})
        assert social_policy.gate(con, "instagram") == before


@pytest.mark.parametrize("reason", [
    "dependency_missing", "invalid_settings", "unsupported_proxy", "invalid_response",
])
def test_unusable_provider_configuration_needs_manual_recovery(reason):
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        assert social_policy.observe(con, {"stage": "profile", "target": PROFILE},
                                     {"status": "blocked", "reason": reason})
        assert social_policy.gate(con, "instagram")["state"] == "waiting_configuration"


@pytest.mark.parametrize("later_reason", ["provider_error", "network_error", "invalid_response"])
def test_late_provider_failure_cannot_erase_access_restriction(later_reason):
    retained = {
        "status": "preview_read", "title": "Fixture", "description": "Public bio",
        "fields": [], "posts": [{"url": "https://www.instagram.com/p/ONE/", "text": "Observed"}],
        "partial": True, "reason": "access_denied", "message": "Authentication rejected",
    }
    result = social_optional._failure(retained, later_reason)
    assert result["reason"] == "access_denied"
    assert result["partial"] and result["status"] == "preview_read"
    assert result["posts"] == retained["posts"]
    assert retained["message"] == "Authentication rejected"


def test_login_page_heading_is_not_projected_as_a_public_profile():
    html = "<title>Log in • Facebook</title><main><h1>Log in to Facebook</h1><p>Log in to continue</p></main>"
    result = social_profile.parse_profile(html, "https://www.facebook.com/fixture/")
    assert result["status"] == "auth_required"
    assert not result.get("fields")
    assert not result.get("title")


@pytest.mark.parametrize("parser", [
    social_browser.parse_browser_document, social_profile.parse_profile,
])
def test_account_name_containing_gate_words_is_not_a_login_page(parser):
    result = parser(PREVIEW_HTML, "https://www.instagram.com/challengerbrand/")
    assert result["status"] == "preview_read"


def test_hidden_login_form_does_not_discard_visible_public_profile():
    html = PREVIEW_HTML + '<form hidden><input type="password"></form>'
    result = social_profile.parse_profile(html, PROFILE)
    assert result["status"] == "preview_read"
    assert any(field["value"] == "hello@brand.example" for field in result["fields"])


@pytest.mark.parametrize("parser", [
    social_discovery.parse_public_profile, social_profile.parse_profile,
])
def test_public_profile_does_not_publish_malformed_contact_fields(parser):
    html = (
        '<title>Fixture</title><meta property="og:description" content="'
        'Email hello@brand.example bad..name@brand.example.com sales@-bad.example.org logo@2x.png '
        'filler@godaddy.com Phone: 0000000000; Tel: +1 (212) 555-0199">'
    )
    result = parser(html, PROFILE)
    emails = [field["value"] for field in result["fields"] if field["key"] == "email"]
    phones = [field["value"] for field in result["fields"] if field["key"] == "phone"]
    assert emails == ["hello@brand.example"]
    assert len(phones) == 1
    assert "".join(c for c in phones[0] if c.isdigit()) == "12125550199"


def test_invalid_new_social_fields_do_not_replace_retained_valid_observations():
    observations = [
        ({"status": "preview_read", "source_url": PROFILE, "fields": [
            {"key": "email", "value": "bad..name@brand.example.com"},
            {"key": "phone", "value": "0000000000"},
            {"key": "website", "value": "javascript:alert(1)"},
        ]}, "2026-09-26"),
        ({"status": "preview_read", "source_url": PROFILE, "fields": [
            {"key": "email", "value": "hello@brand.example", "evidence": "Original bio"},
            {"key": "phone", "value": "+12125550199"},
        ]}, "2026-09-20"),
    ]
    before = json.dumps(observations)
    result = project_account(observations)
    assert {field["key"]: field["value"] for field in result["fields"]} == {
        "email": "hello@brand.example", "phone": "+12125550199",
    }
    assert all(field["retained"] and field["observed_at"] == "2026-09-20"
               for field in result["fields"])
    assert json.dumps(observations) == before


@pytest.mark.parametrize("parser", [
    social_discovery.parse_public_profile, social_profile.parse_profile,
])
def test_social_email_fields_share_outreach_policy(parser):
    allowed = ["sales@brand.example", "support@brand.example", "info@brand.example"]
    rejected = ["youremail@here.com", "privacy@brand.example", "abuse@brand.example",
                "jobs@brand.example", "no-reply@brand.example"]
    html = '<title>Fixture</title><meta property="og:description" content="' + " ".join(allowed + rejected) + '">'
    result = parser(html, PROFILE)
    assert {field["value"] for field in result["fields"]} == set(allowed)
    # Filtering fields must not rewrite the captured source text.
    assert "privacy@brand.example" in result["description"]


def test_historical_social_email_fields_share_outreach_policy_without_writes():
    payload = {"status": "preview_read", "fields": [
        {"key": "email", "value": value} for value in [
            "youremail@here.com", "privacy@brand.example", "abuse@brand.example",
            "jobs@brand.example", "noreply@brand.example", "sales@brand.example",
            "support@brand.example", "info@brand.example"]]}
    before = json.dumps(payload)
    result = project_account([(payload, "2026-09-20")])
    assert {field["value"] for field in result["fields"]} == {
        "sales@brand.example", "support@brand.example", "info@brand.example"}
    assert json.dumps(payload) == before


def test_malformed_social_field_container_does_not_crash_projection():
    result = project_account([
        ({"status": "preview_read", "fields": [None, "bad", {"key": [], "value": "bad"}],
          "posts": [None, {"url": "javascript:alert(1)", "text": "untrusted"}]}, "2026-09-26"),
        ({"status": "preview_read", "fields": [{"key": "email", "value": "hello@brand.example"}]},
         "2026-09-20"),
    ])
    assert [field["value"] for field in result["fields"]] == ["hello@brand.example"]
    assert result["posts"] == []
    assert result["content_observed_at"] == "2026-09-20"


@pytest.mark.parametrize("result", [
    {"status": "unreadable"}, {"status": "failed", "reason": "provider_error"},
])
def test_unrecognized_result_does_not_reset_recovery_history(result):
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    ("instagram", "waiting_recovery", 0, "Prior failures", 3))
        assert social_policy.observe(con, {"stage": "profile", "target": PROFILE}, result)
        value = social_policy.gate(con, "instagram")
        assert value["state"] == "waiting_configuration"
        assert value["failures"] == 3
        assert not social_policy.allowed(con, "instagram")


def test_recovery_is_bounded_and_successful_other_platform_cannot_reset_it(monkeypatch):
    monkeypatch.setattr(social_policy.time, "time", lambda: 1000)
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        for attempt, delay in enumerate((600, 1800, 7200, 7200), 1):
            assert social_policy.observe(con, {"stage": "read", "target": PROFILE},
                                         {"status": "failed", "reason": "network_error"})
            value = social_policy.gate(con, "instagram")
            assert value["not_before"] == 1000 + delay
            assert value["failures"] == attempt
        social_policy.observe(con, {"stage": "read", "target": "https://x.com/fixture"},
                              {"status": "preview_read"})
        assert social_policy.gate(con, "instagram")["state"] == "waiting_configuration"
        monkeypatch.setattr(social_policy.time, "time", lambda: 9000)
        assert not social_policy.allowed(con, "instagram")
        social_policy.resume(con, "instagram")
        assert social_policy.allowed(con, "instagram")


class ProfileBrowser:
    """Actual parser/reader loop with deterministic page states and no browser."""
    def __init__(self, later, status=200):
        self.later = later
        self.status = status
        self.url = PROFILE
        self.reads = 0
        self.navigated = []
        self.closed = self.stopped = False

    async def new_page(self):
        return self

    async def route(self, *args):
        pass

    async def goto(self, url, **kwargs):
        self.navigated.append(url)
        self.url = url
        return SimpleNamespace(status=self.status)

    async def wait_for_timeout(self, delay):
        pass

    async def content(self):
        self.reads += 1
        if self.reads == 1:
            return PREVIEW_HTML
        if isinstance(self.later, Exception):
            raise self.later
        return self.later

    async def close(self):
        self.closed = True

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
@pytest.mark.parametrize("later,reason", [
    ("<title>Log in • Instagram</title>", "login_required"),
    ("<title>Security check</title>", "challenge"),
    (TimeoutError("fixture"), "network_error"),
    ("<title>Instagram</title><main></main>", "profile_unrecognized"),
])
async def test_profile_only_late_failure_retains_actual_preview(tmp_path, monkeypatch, later, reason):
    fake = ProfileBrowser(later)
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    result = await social_browser._read_browser(PROFILE, tmp_path, {"profile_only": True})
    assert result["status"] == "preview_read"
    assert result["partial"] and result["reason"] == reason
    assert result["title"] == "Fixture Apparel"
    assert result["fields"][0]["value"] == "hello@brand.example"
    assert result["posts"] == []
    assert fake.navigated == [PROFILE]
    assert fake.closed and fake.stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
async def test_initial_http_error_cannot_become_profile_preview(tmp_path, monkeypatch, status):
    fake = ProfileBrowser(PREVIEW_HTML, status)
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    result = await social_browser._read_browser(PROFILE, tmp_path, {"profile_only": True})
    assert result["status"] == "failed"
    assert result["reason"] == "network_error"
    assert fake.reads == 0
    assert fake.closed and fake.stopped


@pytest.mark.asyncio
async def test_browser_launch_runtime_failure_is_not_misreported_as_session_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(side_effect=RuntimeError("Launch failed")))
    result = await social_browser._read_browser(PROFILE, tmp_path, {})
    assert result["reason"] == "network_error"


@pytest.mark.parametrize("handle", ["foo..bar", "brand@email.example.org", "a" * 31, ".brand", "brand."])
def test_invalid_account_handles_not_discovered_or_parsed_as_profiles(handle):
    url = f"https://www.instagram.com/{handle}/"
    html = f'<main><a href="{url}">Fixture Brand</a></main>'
    assert social_discovery.website_candidates(html, "https://brand.example") == []
    result = social_platform_search.parse_candidates(html, "instagram", "Fixture Brand", PROFILE)
    assert result["links"] == []
    assert social_profile.parse_profile(PREVIEW_HTML, url)["status"] == "blocked"


def test_search_login_page_cannot_seed_account_candidates():
    html = '<title>Log in • Instagram</title><a href="/fixture/">Fixture</a>'
    result = social_platform_search.parse_candidates(html, "instagram", "Fixture", PROFILE)
    assert result["status"] == "auth_required"
    assert result.get("links", []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,reason", [
    ("browser", "access_denied"), ("instaloader", "rate_limited"), ("instaloader", "robots_denied"),
])
async def test_selected_provider_restriction_never_triggers_fallback(tmp_path, monkeypatch, selected, reason):
    result = {"status": "preview_read", "partial": True, "reason": reason, "title": "Kept"}
    browser = AsyncMock(return_value=result)
    optional = AsyncMock(return_value=result)
    http = AsyncMock(side_effect=AssertionError("Restricted provider must not fall back"))
    monkeypatch.setattr(social_browser, "read_browser", browser)
    monkeypatch.setattr(social_optional, "read_instaloader", optional)
    monkeypatch.setattr(social_combination, "read_public", http)
    actual = await social_combination.read_combined(
        PROFILE, tmp_path, {"reader": "browser", "instagram_reader": selected})
    assert actual == result
    assert browser.await_count + optional.await_count == 1
    http.assert_not_awaited()


@pytest.mark.asyncio
async def test_usable_cache_during_gate_does_not_request_or_reset_gate(tmp_path, monkeypatch):
    from leadworkbench.social_worker import process_social_item
    store = Store(tmp_path)
    ids = []
    for name in ("A", "B"):
        company = store.manual_company(name, "https://brand.example")
        ids.append(company["id"])
        store.add_social(company["id"], PROFILE, "Shared fixture")
    store.create_social_run(ids, profile_only=True)
    read = AsyncMock(return_value={"status": "preview_read", "title": "Kept", "fields": []})
    assert await process_social_item(store, read=read)
    set_gate(store)
    with store.connect() as con:
        before = social_policy.gate(con, "instagram")
    assert await process_social_item(store, read=read)
    assert read.await_count == 1
    with store.connect() as con:
        assert social_policy.gate(con, "instagram") == before
        assert con.execute("SELECT count(*) FROM social_page_observations").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_worker_cancellation_releases_claim_only_after_reader_cleanup(tmp_path):
    from leadworkbench.social_worker import process_social_item
    store = Store(tmp_path)
    run = manual_profile(store)
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def read(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    job = asyncio.create_task(process_social_item(store, read=read))
    await asyncio.wait_for(entered.wait(), 1)
    assert store.social_run(run["id"])["summary"]["running"] == 1
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cleaned.is_set()
    assert store.social_run(run["id"])["summary"]["pending"] == 1
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM social_page_observations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_total_browser_budget_preserves_preview_and_cleans_up(tmp_path, monkeypatch):
    budget = asyncio.timeout(None)
    fake = ProfileBrowser("")
    async def content():
        fake.reads += 1
        if fake.reads == 1:
            return PREVIEW_HTML
        budget.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()
    fake.content = content
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    monkeypatch.setattr(social_browser.asyncio, "timeout", lambda seconds: budget)
    result = await social_browser.read_browser(PROFILE, tmp_path, {"profile_only": True})
    assert result["title"] == "Fixture Apparel"
    assert result["status"] == "preview_read" and result["partial"]
    assert result["reason"] == "network_error"
    assert fake.closed and fake.stopped
    with social_browser.profile_lock(tmp_path, "instagram"):
        pass


@pytest.mark.asyncio
async def test_context_close_failure_still_stops_driver_and_retains_preview(tmp_path, monkeypatch):
    fake = ProfileBrowser(PREVIEW_HTML)
    fake.close = AsyncMock(side_effect=OSError("Fixture close failure"))
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    result = await social_browser._read_browser(PROFILE, tmp_path, {"profile_only": True})
    assert fake.stopped
    assert result["title"] == "Fixture Apparel" and result["partial"]
    assert result["reason"] == "network_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,code", [
    ("access_denied", 1), ("rate_limited", 0), ("network_error", 0),
])
async def test_child_failure_snapshot_cannot_erase_previously_streamed_data(tmp_path, monkeypatch, reason, code):
    preview = social_optional._empty("instaloader", PROFILE)
    preview.update(status="preview_read", title="Observed", description="Actual bio",
                   posts=[{"url": "https://www.instagram.com/p/ONE/", "text": "Observed post"}])
    failure = social_optional._failure(social_optional._empty("instaloader", PROFILE), reason)
    stream = asyncio.StreamReader()
    for row in (preview, failure):
        stream.feed_data(json.dumps(row).encode() + b"\n")
    stream.feed_eof()
    child = SimpleNamespace(stdout=stream, returncode=code, wait=AsyncMock(return_value=code))
    monkeypatch.setattr(social_optional.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(social_optional.asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    result = await social_optional.read_instaloader(PROFILE, tmp_path, {})
    assert result["status"] == "preview_read"
    assert result["reason"] == reason and result["partial"]
    assert result["title"] == preview["title"] and result["posts"] == preview["posts"]
    assert child.wait.await_count >= 1


def test_child_outer_failure_preserves_last_emitted_snapshot(monkeypatch, capsys):
    snapshot = social_optional._empty("instaloader", PROFILE)
    snapshot.update(status="preview_read", title="Observed", description="Bio")
    def read(url, count, emit):
        emit(snapshot)
        raise social_optional.ProviderError("rate_limited")
    monkeypatch.setattr(social_optional, "_instagram_run", read)
    monkeypatch.setattr(social_optional, "_bounded_instagram_transport", lambda proxy: nullcontext())
    monkeypatch.setattr(social_optional.sys, "argv", ["fixture", "--instagram-child", PROFILE, "3", ""])
    assert social_optional._main() == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == 2
    assert records[-1]["title"] == "Observed"
    assert records[-1]["status"] == "preview_read" and records[-1]["partial"]
    assert records[-1]["reason"] == "rate_limited"


@pytest.mark.asyncio
async def test_malformed_optional_settings_stop_without_fallback(tmp_path, monkeypatch):
    (tmp_path / "social-secrets.json").write_text("{broken", encoding="utf-8")
    http = AsyncMock(side_effect=AssertionError("Invalid configured provider must not fall back"))
    monkeypatch.setattr(social_combination, "read_public", http)
    result = await social_combination.read_combined("https://www.youtube.com/@fixture", tmp_path, {})
    assert result["status"] == "blocked" and result["reason"] == "not_configured"
    http.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_server_error_has_recovery_reason(monkeypatch):
    monkeypatch.setattr(social_discovery, "public_html",
                        AsyncMock(side_effect=social_discovery.HTTPError(503)))
    result = await social_discovery.read_public(PROFILE, "read")
    assert result["reason"] == "network_error"


@pytest.mark.asyncio
async def test_blocked_platform_does_not_stop_other_platform(tmp_path):
    from leadworkbench.social_worker import process_social_item
    store = Store(tmp_path)
    manual_profile(store)
    company = store.manual_company("Other", "https://other.example")
    store.add_social(company["id"], "https://x.com/other", "Fixture")
    store.create_social_run([company["id"]], read_only=True)
    set_gate(store)
    read = AsyncMock(return_value={"status": "preview_read", "title": "Other"})
    assert await process_social_item(store, read=read)
    read.assert_awaited_once_with("https://x.com/other", "read")
    with store.connect() as con:
        assert social_policy.gate(con, "instagram")["state"] == "waiting_login"


def test_manual_run_exposes_waiting_platform_for_recovery_ui(tmp_path):
    store = Store(tmp_path)
    run = manual_profile(store)
    set_gate(store)
    result = store.social_run(run["id"])
    assert result["platform_gates"]["instagram"]["state"] == "waiting_login"
    assert result["platform_gates"]["instagram"]["manual_required"] is True
    assert result["summary"]["pending"] == 1


def test_visible_login_heading_without_password_or_title_is_not_a_profile():
    html = "<main><h1>Log in</h1><button>Continue with Facebook</button></main>"
    assert social_profile.parse_profile(html, "https://www.facebook.com/fixture/")["status"] == "auth_required"


@pytest.mark.parametrize("limit,expected", [(0, 0), (100, 5)])
def test_browser_post_parser_obeys_bounded_post_budget(limit, expected):
    html = PREVIEW_HTML + "".join(
        f'<article><p>Post {i}</p><a href="/p/POST{i}/">Permalink</a></article>' for i in range(10))
    assert len(social_browser.parse_browser_document(html, PROFILE, limit)["posts"]) == expected


@pytest.mark.asyncio
async def test_zero_post_read_does_not_enter_feed_or_post_navigation(tmp_path, monkeypatch):
    fake = ProfileBrowser(PREVIEW_HTML)
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    result = await social_browser._read_browser(PROFILE, tmp_path, {"max_posts": 0})
    assert result["status"] == "preview_read" and result["profile_only"]
    assert not result.get("partial")
    assert result["posts"] == []
    assert fake.navigated == [PROFILE]


@pytest.mark.asyncio
async def test_cache_never_substitutes_a_partial_read_for_other_company(tmp_path, monkeypatch):
    from leadworkbench.social_worker import process_social_item
    store = Store(tmp_path)
    ids = []
    for name in ("A", "B"):
        company = store.manual_company(name, "https://brand.example")
        ids.append(company["id"])
        store.add_social(company["id"], PROFILE, "Shared fixture")
    store.create_social_run(ids, read_only=True)
    clock = [1000]
    monkeypatch.setattr(social_policy.time, "time", lambda: clock[0])
    read = AsyncMock(side_effect=[
        {"status": "preview_read", "partial": True, "title": "Only bio", "posts": []},
        {"status": "preview_read", "title": "Refreshed", "posts": []},
    ])
    assert await process_social_item(store, read=read)
    assert not await process_social_item(store, read=read)
    clock[0] = 2000
    assert await process_social_item(store, read=read)
    assert read.await_count == 2
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM social_page_observations").fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_only", [True, False])
async def test_late_non_profile_surface_stops_without_losing_preview(tmp_path, monkeypatch, profile_only):
    fake = ProfileBrowser("")
    fake.mouse = SimpleNamespace(wheel=AsyncMock())
    async def content():
        fake.reads += 1
        if fake.reads > 1:
            fake.url = "https://www.instagram.com/accounts/recovery/"
        return PREVIEW_HTML
    fake.content = content
    monkeypatch.setattr(social_browser, "browser_context", AsyncMock(return_value=(fake, fake)))
    result = await social_browser._read_browser(PROFILE, tmp_path, {"profile_only": profile_only})
    assert result["title"] == "Fixture Apparel" and result["partial"]
    assert result["reason"] == "unsupported_url"
    assert fake.reads == 2 and fake.navigated == [PROFILE]
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        assert social_policy.observe(con, {"stage": "profile", "target": PROFILE}, result)
        assert social_policy.gate(con, "instagram")["state"] == "waiting_configuration"
