"""Offline transport regressions: real crawler, fake HTTP responses and clock."""
import asyncio
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock

import pytest

from leadworkbench.sources import website as w
from leadworkbench.website_link_policy import link_kind
from test_website import Response, Session


ROOT = "https://example.org/"


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]

    async def sleep(delay):
        assert delay >= 0
        now[0] += delay

    monkeypatch.setattr(w.time, "time", lambda: now[0])
    monkeypatch.setattr(w.asyncio, "sleep", sleep)
    return now


class TimedSession(Session):
    def __init__(self, routes, clock):
        super().__init__(routes)
        self.clock = clock
        self.sent_at = []

    def get(self, url, **kwargs):
        self.sent_at.append(self.clock[0])
        return super().get(url, **kwargs)


async def crawl(monkeypatch, clock, routes, **options):
    session = TimedSession(routes, clock)
    monkeypatch.setattr(w.aiohttp, "ClientSession", lambda **kwargs: session)
    result = await w.enrich_website(ROOT, **options)
    return result, session


@pytest.mark.asyncio
async def test_missing_robots_delay_still_spaces_every_request(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/contact">Contact</a>'),
        ROOT + "contact": Response("desk@example.org"),
    })
    assert result["summary"]["pages_checked"] == 2
    assert any(c["email"] == "desk@example.org" for c in result["contacts"])
    assert all(b - a >= 2 for a, b in zip(session.sent_at, session.sent_at[1:]))


@pytest.mark.asyncio
async def test_robots_redirect_hops_share_spacing_and_then_apply_crawl_delay(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=302, headers={"Location": "/robots-policy"}),
        ROOT + "robots-policy": Response("User-agent: *\nCrawl-delay: 8"),
        ROOT: Response("<title>Fixture</title>"),
    })
    assert result["status"] == "completed"
    assert session.sent_at[1] - session.sent_at[0] >= 2
    assert session.sent_at[2] - session.sent_at[1] >= 8


@pytest.mark.asyncio
async def test_minimum_spacing_survives_batch_checkpoint(monkeypatch, clock):
    routes = {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/contact">Contact</a>'),
        ROOT + "contact": Response("desk@example.org"),
    }
    first, first_session = await crawl(monkeypatch, clock, routes, max_pages=1)
    second, second_session = await crawl(
        monkeypatch, clock, routes, checkpoint=json.loads(json.dumps(first["checkpoint"])))
    assert second["summary"]["pages_checked"] == 2
    assert ROOT not in second_session.requested
    assert second_session.sent_at[0] - first_session.sent_at[-1] >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_rate_response_stops_batch_preserves_contacts_and_pending_queue(monkeypatch, clock, status):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('desk@example.org <a href="/contact-a">Contact</a>'
                       '<a href="/contact-b">Contact</a>'),
        ROOT + "contact-a": Response(status=status, headers={"Retry-After": "7200"}),
        ROOT + "contact-b": Response("later@example.org"),
    })
    assert session.requested == [ROOT + "robots.txt", ROOT, ROOT + "contact-a"]
    assert result["status"] == "partial" and result["has_more"] is False
    assert any(c["email"] == "desk@example.org" for c in result["contacts"])
    assert ROOT + "contact-b" in result["checkpoint"]["queue"]
    assert ROOT + "contact-a" in result["checkpoint"]["failures"]
    gate = result["website_access"]
    assert gate["host"] == "example.org" and gate["http_status"] == status
    assert gate["not_before"] >= session.sent_at[-1] + 7200
    assert gate["manual_required"] is False
    assert result["checkpoint"]["host_gates"]["example.org"] == gate


@pytest.mark.asyncio
async def test_retry_after_http_date_is_retained_without_waiting_in_lane(monkeypatch, clock):
    date = format_datetime(datetime.fromtimestamp(10000, timezone.utc), usegmt=True)
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response(status=503, headers={"Retry-After": date}),
    })
    assert result["website_access"]["not_before"] >= 10000
    assert clock[0] < 1010
    assert len(session.requested) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["garbage", "-1", "NaN", "9" * 1000],
                         ids=["invalid", "negative", "not-a-number", "oversized"])
async def test_invalid_retry_after_still_defers_without_unbounded_sleep(monkeypatch, clock, value):
    result, _ = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response(status=429, headers={"Retry-After": value}),
    })
    assert result["website_access"]["not_before"] >= clock[0] + 60
    assert clock[0] < 1010
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_future_checkpoint_gate_prevents_even_robots_request(monkeypatch, clock):
    gate = dict(host="example.org", not_before=8200, reason="HTTP 429",
                http_status=429, manual_required=False)
    checkpoint = {
        "version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
        "host_gates": {"example.org": gate},
    }
    result, session = await crawl(monkeypatch, clock, {}, checkpoint=checkpoint)
    assert session.requested == []
    assert result["has_more"] is False and result["status"] == "blocked"
    assert result["checkpoint"]["queue"] == {ROOT: (0, 0, 0)}
    assert result["website_access"] == gate
    assert clock[0] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_robots_rate_failure_preserves_structured_gate_and_fails_closed(monkeypatch, clock, status):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=status, headers={"Retry-After": "7200"}),
    })
    assert session.requested == [ROOT + "robots.txt"]
    assert result["status"] == "blocked"
    assert result["website_access"]["http_status"] == status
    assert result["website_access"]["not_before"] >= 8200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_stops_pending_requests_without_fallback(monkeypatch, clock, status):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/contact-a">Contact</a><a href="/contact-b">Contact</a>'),
        ROOT + "contact-a": Response(status=status),
        ROOT + "contact-b": Response("later@example.org"),
    })
    assert ROOT + "contact-b" not in session.requested
    assert result["has_more"] is False
    assert result["website_access"]["manual_required"] is True


@pytest.mark.asyncio
async def test_page_not_found_remains_local_and_next_contact_page_is_read(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/contact-old">Contact</a><a href="/contact-new">Contact</a>'),
        ROOT + "contact-old": Response(status=404),
        ROOT + "contact-new": Response("desk@example.org"),
    })
    assert ROOT + "contact-new" in session.requested
    assert any(c["email"] == "desk@example.org" for c in result["contacts"])
    assert not result.get("website_access")


@pytest.mark.asyncio
async def test_https_redirect_never_falls_back_to_plain_http():
    session = Session({
        ROOT: Response(status=302, headers={"Location": "http://example.org/contact"}),
        "http://example.org/contact": Response("desk@example.org"),
    })
    with pytest.raises(w.UnsafeURL):
        await w.fetch_page(session, ROOT)
    assert session.requested == [ROOT]


def test_schemeless_url_preserves_tenant_query_in_https_normalization():
    assert w.normalize_url("example.org?tenant=branch-a&id=2&id=3") == (
        "https://example.org/?tenant=branch-a&id=2&id=3")


@pytest.mark.asyncio
async def test_invalid_changed_start_with_checkpoint_is_blocked_without_fetch(monkeypatch):
    monkeypatch.setattr(w.aiohttp, "ClientSession", lambda **kwargs: pytest.fail("No fetch"))
    result = await w.enrich_website("http://127.0.0.1/", checkpoint={
        "version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
    })
    assert result["status"] == "blocked"
    assert not result["contacts"]


@pytest.mark.parametrize("suffix", [".js", ".css", ".woff2", ".zip", ".mp4"])
def test_explicit_assets_are_not_contact_pages_even_with_contact_label(suffix):
    assert link_kind(ROOT + "contact" + suffix, "Contact us") == "asset"


def test_product_sku_label_does_not_override_contact_page_priority():
    parsed = w.parse_page(
        '<a href="/style-77844-dress">Contact supplier</a>'
        '<a href="/supplier-directory">Suppliers</a>', ROOT)
    assert parsed["links"] == [(ROOT + "supplier-directory", True)]
    assert any(item["url"] == ROOT + "style-77844-dress" for item in parsed["skipped_links"])


@pytest.mark.asyncio
async def test_expired_gate_can_finish_without_retaining_its_wait_warning(monkeypatch, clock):
    checkpoint = {
        "version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
        "host_gates": {"example.org": dict(host="example.org", not_before=8200,
            reason="HTTP 429", http_status=429, manual_required=False)},
    }
    waiting, _ = await crawl(monkeypatch, clock, {}, checkpoint=checkpoint)
    clock[0] = 9000
    finished, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    }, checkpoint=waiting["checkpoint"])
    assert finished["status"] == "completed"
    assert finished["warnings"] == []
    assert session.requested == [ROOT + "robots.txt", ROOT]


@pytest.mark.asyncio
async def test_internal_transport_retry_is_spaced_and_request_budget_still_applies(monkeypatch, clock):
    options = {}
    session = TimedSession({
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    }, clock)

    def factory(**kwargs):
        options.update(kwargs)
        return session

    monkeypatch.setattr(w.aiohttp, "ClientSession", factory)
    monkeypatch.setattr(w, "MAX_REQUESTS", 2)
    await w.enrich_website(ROOT)
    request = object.__new__(options["request_class"])
    sent_at = []

    async def wire_send(connection):
        sent_at.append(clock[0])

    monkeypatch.setattr(w.aiohttp.ClientRequest, "send", AsyncMock(side_effect=wire_send))
    await request.send(None)
    await request.send(None)
    with pytest.raises(w.RequestLimit):
        await request.send(None)
    assert len(sent_at) == 2
    assert sent_at[1] - sent_at[0] >= 2


@pytest.mark.asyncio
async def test_timeout_stops_host_without_retrying_or_losing_other_queue_entries(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/contact-a">Contact</a><a href="/contact-b">Contact</a>'),
        ROOT + "contact-a": TimeoutError(),
    })
    assert session.requested == [ROOT + "robots.txt", ROOT, ROOT + "contact-a"]
    assert result["website_access"]["reason"] == "TimeoutError"
    assert result["website_access"]["not_before"] > clock[0]
    assert ROOT + "contact-b" in result["checkpoint"]["queue"]
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_tls_failure_requires_manual_attention_without_http_retry(monkeypatch, clock):
    import ssl
    from types import SimpleNamespace
    key = SimpleNamespace(host="example.org", port=443, ssl=True, is_ssl=True)
    failure = w.aiohttp.ClientConnectorCertificateError(
        key, ssl.SSLCertVerificationError(1, "certificate verify failed"))
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: failure,
    })
    assert result["status"] == "blocked"
    assert result["website_access"]["manual_required"] is True
    assert session.requested == [ROOT + "robots.txt", ROOT]


@pytest.mark.asyncio
async def test_manual_gate_never_expires_into_an_automatic_request(monkeypatch, clock):
    checkpoint = {
        "version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
        "host_gates": {"example.org": dict(host="example.org", not_before=0,
            reason="HTTP 403", http_status=403, manual_required=True)},
    }
    clock[0] = 100000
    result, session = await crawl(monkeypatch, clock, {}, checkpoint=checkpoint)
    assert session.requested == []
    assert result["status"] == "blocked"
    assert result["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [Response(status=500), TimeoutError()])
async def test_unavailable_robots_fails_closed_and_releases_lane(monkeypatch, clock, failure):
    result, session = await crawl(monkeypatch, clock, {ROOT + "robots.txt": failure})
    assert session.requested == [ROOT + "robots.txt"]
    assert result["status"] == "blocked" and result["pages_checked"] == 0
    assert result["website_access"]["reason"].startswith("robots unavailable:")
    assert result["website_access"]["not_before"] > clock[0]
    assert clock[0] == 1000


@pytest.mark.asyncio
async def test_cancellation_during_spacing_never_issues_next_request(monkeypatch, clock):
    started = asyncio.Event()

    async def wait(delay):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(w.asyncio, "sleep", wait)
    session = TimedSession({ROOT + "robots.txt": Response(status=404)}, clock)
    monkeypatch.setattr(w.aiohttp, "ClientSession", lambda **kwargs: session)
    job = asyncio.create_task(w.enrich_website(ROOT))
    await asyncio.wait_for(started.wait(), timeout=1)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert session.requested == [ROOT + "robots.txt"]


@pytest.mark.asyncio
async def test_word_document_link_is_retained_without_request_or_contact_form(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/vendor-application.docx">Supplier application</a>'),
    })
    assert session.requested == [ROOT + "robots.txt", ROOT]
    assert result["status"] == "completed"
    assert result["summary"]["files_unparsed"] == 1
    assert result["summary"]["failed"] == 0
    assert result["checkpoint"]["files_unparsed"][0]["url"] == ROOT + "vendor-application.docx"
    assert not result["contacts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
])
async def test_extensionless_word_document_remains_manual_without_body_read(monkeypatch, clock, content_type):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('<a href="/download">Download application</a>'),
        ROOT + "download": Response(headers={"Content-Type": content_type}, chunks=[]),
    })
    assert result["status"] == "completed"
    assert result["summary"]["files_unparsed"] == 1
    assert result["summary"]["failed"] == 0
    assert session.requested[-1] == ROOT + "download"


@pytest.mark.asyncio
async def test_oversized_dynamic_entry_does_not_relax_limit_or_guess_fallback_urls(monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response(headers={"Content-Length": "1000001"}, chunks=[]),
    })
    assert result["pages_checked"] == 0
    assert "byte limit" in result["warnings"][0]
    assert result["has_more"] is False
    assert session.requested == [ROOT + "robots.txt", ROOT]


def parse_forms_at_source(monkeypatch, html, source_url=ROOT):
    # Isolate source extraction from the independently maintained legacy filter.
    # These regressions must still protect the DB if legacy evidence rules change.
    monkeypatch.setattr(w, "invalid_contact_reason", lambda item: None)
    return w.parse_page(html, source_url)


def test_placeholder_contact_anchors_neither_create_channels_nor_crawl_current_page(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch,
        '<a href="#">Contact Us</a><a href=" # ">Supplier application</a>'
        '<a href="">Contact us</a>', ROOT + "catalog/item-123")
    assert parsed["contacts"] == []
    assert parsed["links"] == []


def test_real_contact_fragment_remains_a_published_channel(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch,
        '<a href="#contact">Contact Us</a><section id="contact">Enquiries</section>',
        ROOT + "company")
    assert parsed["contacts"][0]["url"] == ROOT + "company#contact"
    assert "#contact" in parsed["contacts"][0]["evidence"]


def test_outer_page_form_does_not_borrow_contact_navigation_or_product_review(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <form id="aspnetForm" action="/catalog/item-123">
          <header><a href="#">Contact Us</a></header>
          <nav><a href="/products">Products</a></nav>
          <main><h1>Seasonal products</h1><input type="search" name="q">
            <textarea name="message" placeholder="Product review"></textarea></main>
        </form>''', ROOT + "catalog/item-123")
    assert not parsed["contacts"]


@pytest.mark.parametrize("purpose", ["newsletter-sign-up", "search", "login", "cart"])
def test_non_contact_form_purpose_wins_over_incidental_contact_text(monkeypatch, purpose):
    parsed = parse_forms_at_source(monkeypatch, f'''
        <form id="{purpose}" action="/{purpose}">
          <h2>Contact us</h2><label>Contact number</label><input name="phone">
          <textarea name="message"></textarea><button>Continue</button>
        </form>''')
    assert not parsed["contacts"]


@pytest.mark.parametrize("control", [
    '<input name="phone">', '<textarea name="address"></textarea>',
    '<input type="hidden" name="message">', '<textarea name="message" disabled></textarea>',
])
def test_contact_form_requires_a_visible_writable_message_field(monkeypatch, control):
    parsed = parse_forms_at_source(monkeypatch,
        f'<form action="/submit"><h2>Contact us</h2>{control}<button>Submit</button></form>')
    assert not parsed["contacts"]


def test_clear_local_contact_form_with_message_is_preserved(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <form action="/send"><h2>Get in touch</h2>
          <input type="email" name="email"><label for="body">Your message</label>
          <textarea id="body" name="body"></textarea><button>Send message</button>
        </form>''')
    forms = [row for row in parsed["contacts"] if row["kind"] == "contact_form"]
    assert len(forms) == 1
    assert forms[0]["url"] == ROOT + "send"
    assert "Get in touch" in forms[0]["evidence"] and "/send" in forms[0]["evidence"]


def test_supplier_application_with_message_retains_supplier_intent(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <form action="/apply"><h2>Supplier registration</h2><input name="company">
          <textarea name="requirements"></textarea><button>Apply</button>
        </form>''')
    assert len(parsed["contacts"]) == 1
    assert parsed["contacts"][0]["kind"] == "supplier_form"
    assert parsed["contacts"][0]["url"] == ROOT + "apply"


def test_contact_intent_does_not_leak_between_forms(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <form action="/newsletter-sign-up"><label>Contact number</label><input name="phone"></form>
        <form action="/contact"><h2>Contact us</h2><textarea name="message"></textarea></form>''')
    assert [row["url"] for row in parsed["contacts"]] == [ROOT + "contact"]


def test_message_field_alone_is_not_contact_intent_from_a_nav_link(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <form action="/review"><a href="/contact">Contact us</a>
          <h2>Leave a product review</h2><textarea name="message"></textarea></form>''')
    # The genuine published /contact link is retained, not the review endpoint.
    assert [row["url"] for row in parsed["contacts"]] == [ROOT + "contact"]


def test_local_contact_form_inside_footer_does_not_lose_its_own_intent(monkeypatch):
    parsed = parse_forms_at_source(monkeypatch, '''
        <footer><p>Browse products</p><form action="/send"><h2>Get in touch</h2>
          <textarea name="message"></textarea><button>Send message</button>
        </form></footer>''')
    assert [row["url"] for row in parsed["contacts"]] == [ROOT + "send"]


def test_real_contact_forms_and_fragments_pass_shared_channel_validation():
    parsed = w.parse_page('''
        <a href="#contact">Contact us</a><form id="contact" action="/contact">
          <h2>Contact us</h2><textarea name="message"></textarea><button>Send</button>
        </form>''', ROOT)
    assert {row["url"] for row in parsed["contacts"]} == {ROOT + "#contact", ROOT + "contact"}


def emulate_wire(monkeypatch, clock, routes):
    """Exercise the supported ClientRequest send hook without opening a socket."""
    options = {}
    sent_at = []
    session = TimedSession(routes, clock)

    def factory(**kwargs):
        options.update(kwargs)
        return session

    async def enter(response):
        await object.__new__(options["request_class"]).send(None)
        return response

    async def send(connection):
        sent_at.append(clock[0])

    monkeypatch.setattr(w.aiohttp, "ClientSession", factory)
    monkeypatch.setattr(Response, "__aenter__", enter)
    monkeypatch.setattr(w.aiohttp.ClientRequest, "send", AsyncMock(side_effect=send))
    return options, sent_at


@pytest.mark.asyncio
async def test_shared_pacer_covers_robots_redirects_pages_and_wire_retry(monkeypatch, clock, access_policy):
    options, sent_at = emulate_wire(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=302, headers={"Location": "/robots-policy"}),
        ROOT + "robots-policy": Response("User-agent: *\nCrawl-delay: 8"),
        ROOT: Response("<title>Fixture</title>"),
    })
    result = await w.enrich_website(ROOT, access_policy=access_policy)
    assert result["status"] == "completed"
    # The repeated send models aiohttp's internal retry of the last request.
    await object.__new__(options["request_class"]).send(None)
    assert len(sent_at) == 4
    assert sent_at[1] - sent_at[0] >= 2
    assert sent_at[2] - sent_at[1] >= 8
    assert sent_at[-1] - sent_at[-2] >= 8
    assert access_policy.timing(ROOT) == (sent_at[-1], 8)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
async def test_wire_send_reuses_only_its_own_reservation_without_double_spacing(
        monkeypatch, clock, access_policy, shared):
    _, sent_at = emulate_wire(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    })
    result = await w.enrich_website(ROOT, access_policy=access_policy if shared else None)
    assert result["status"] == "completed"
    assert sent_at == [1000, 1002]


@pytest.mark.asyncio
async def test_clock_rollback_during_connection_blocks_before_actual_wire_send(
        monkeypatch, clock, access_policy):
    options, sent_at = emulate_wire(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    })

    async def enter(response):
        clock[0] = 100
        await object.__new__(options["request_class"]).send(None)
        return response

    monkeypatch.setattr(Response, "__aenter__", enter)
    result = await w.enrich_website(ROOT, access_policy=access_policy)
    assert sent_at == []
    assert result["status"] == "blocked" and result["has_more"] is False
    assert "clock" in result["website_access"]["reason"].casefold()
    assert access_policy.timing(ROOT) == (1000, 2)
    assert access_policy.gates()["example.org"] == result["website_access"]


@pytest.mark.asyncio
async def test_shared_gate_refusal_preserves_queue_without_sending_robots(monkeypatch, clock, access_policy):
    _, sent_at = emulate_wire(monkeypatch, clock, {ROOT + "robots.txt": Response(status=404)})
    gate = dict(host="example.org", not_before=8200, reason="HTTP 429",
                http_status=429, manual_required=False)

    access_policy.observe(gate)
    result = await w.enrich_website(ROOT, access_policy=access_policy)
    assert sent_at == []
    assert result["website_access"] == gate
    assert result["status"] == "blocked" and result["has_more"] is False
    assert result["checkpoint"]["queue"] == {ROOT: (0, 0, 0)}
    assert result["checkpoint"]["attempted"] == []
    assert result["checkpoint"]["logs"][-1]["reason"] == "host_gate"


@pytest.mark.asyncio
async def test_shared_gate_between_pages_retains_already_parsed_evidence(monkeypatch, clock, access_policy):
    _, sent_at = emulate_wire(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('desk@example.org <a href="/contact">Contact</a>'),
        ROOT + "contact": Response("later@example.org"),
    })
    gate = dict(host="example.org", not_before=0, reason="HTTP 403",
                http_status=403, manual_required=True)

    async def send(connection):
        sent_at.append(clock[0])
        if len(sent_at) == 2:
            access_policy.observe(gate)

    monkeypatch.setattr(w.aiohttp.ClientRequest, "send", AsyncMock(side_effect=send))
    result = await w.enrich_website(ROOT, access_policy=access_policy)
    assert len(sent_at) == 2
    assert result["has_more"] is False and result["status"] == "partial"
    assert any(row["email"] == "desk@example.org" for row in result["contacts"])
    assert ROOT + "contact" in result["checkpoint"]["queue"]
    assert ROOT + "contact" not in result["checkpoint"]["attempted"]


@pytest.fixture
def access_policy(tmp_path):
    from leadworkbench.db import Store
    from leadworkbench.website_policy import WebsiteAccessPolicy
    return WebsiteAccessPolicy(Store(tmp_path))


@pytest.mark.asyncio
async def test_source_commits_host_failure_before_returning_to_ingestion(monkeypatch, clock, access_policy):
    result, _ = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response(status=403),
    }, access_policy=access_policy)
    assert access_policy.gates()["example.org"] == result["website_access"]
    # A different work item with no checkpoint cannot retry the same host.
    again, session = await crawl(monkeypatch, clock, {}, access_policy=access_policy)
    assert session.requested == []
    assert again["website_access"]["manual_required"] is True


@pytest.mark.asyncio
async def test_shared_timing_spaces_fresh_work_items_and_www_alias(monkeypatch, clock, access_policy):
    _, first = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    }, access_policy=access_policy)
    alias = "https://www.example.org/"
    second = TimedSession({
        alias + "robots.txt": Response(status=404), alias: Response("<title>Fixture</title>"),
    }, clock)
    monkeypatch.setattr(w.aiohttp, "ClientSession", lambda **kwargs: second)
    result = await w.enrich_website(alias, access_policy=access_policy)
    assert result["status"] == "completed"
    assert second.sent_at[0] - first.sent_at[-1] >= 2
    assert access_policy.timing(ROOT) == access_policy.timing(alias)


@pytest.mark.asyncio
async def test_cleared_shared_gate_overrides_stale_checkpoint_without_importing_other_hosts(
        monkeypatch, clock, access_policy):
    access_policy.observe(dict(host="unrelated.org", not_before=9000, reason="HTTP 429",
        http_status=429, manual_required=False))
    checkpoint = {
        "version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
        "warnings": ["Website host waiting: HTTP 403"],
        "host_gates": {"example.org": dict(host="example.org", not_before=0, reason="HTTP 403",
            http_status=403, manual_required=True)},
    }
    result, _ = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    }, checkpoint=checkpoint, access_policy=access_policy)
    assert result["status"] == "completed"
    assert result["warnings"] == []
    assert result["checkpoint"]["host_gates"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["checkpoint", "shared"])
async def test_future_request_timestamp_blocks_without_sleep_or_timing_reset(
        monkeypatch, clock, access_policy, storage):
    checkpoint = {"version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]}}
    options = {"checkpoint": checkpoint}
    if storage == "shared":
        access_policy.record_request(ROOT, 4600, 2)
        options["access_policy"] = access_policy
    else:
        checkpoint["last_request"] = {"https://example.org": 4600}

    async def unexpected_sleep(delay):
        pytest.fail(f"A clock rollback must not occupy the lane with sleep({delay})")

    monkeypatch.setattr(w.asyncio, "sleep", unexpected_sleep)
    result, session = await crawl(monkeypatch, clock, {}, **options)
    assert session.requested == [] and clock[0] == 1000
    assert result["status"] == "blocked" and result["has_more"] is False
    assert result["checkpoint"]["queue"] == {ROOT: (0, 0, 0)}
    gate = result["website_access"]
    assert gate["manual_required"] is True
    assert "clock" in gate["reason"].casefold()
    if storage == "shared":
        assert access_policy.timing(ROOT) == (4600, 2)
        assert access_policy.gates()["example.org"] == gate
    else:
        assert result["checkpoint"]["last_request"]["https://example.org"] == 4600


@pytest.mark.asyncio
async def test_clock_rollback_during_spacing_stops_after_first_wait(monkeypatch, clock, access_policy):
    access_policy.record_request(ROOT, 1000, 2)
    waits = []

    async def rollback(delay):
        waits.append(delay)
        if len(waits) > 1:
            pytest.fail("Clock rollback must not cause repeated pacing waits")
        clock[0] = 100

    monkeypatch.setattr(w.asyncio, "sleep", rollback)
    result, session = await crawl(monkeypatch, clock, {}, access_policy=access_policy)
    assert waits == [2]
    assert session.requested == [] and result["has_more"] is False
    assert result["website_access"]["manual_required"] is True
    assert access_policy.timing(ROOT) == (1000, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["checkpoint", "shared"])
async def test_clock_guard_never_replaces_or_clears_retry_after_gate(
        monkeypatch, clock, access_policy, storage):
    gate = dict(host="example.org", not_before=8200, reason="HTTP 429",
                http_status=429, manual_required=False)
    checkpoint = {"version": 1, "start": ROOT, "queue": {ROOT: [0, 0, 0]},
                  "last_request": {"https://example.org": 4600}}
    options = {"checkpoint": checkpoint}
    if storage == "shared":
        access_policy.record_request(ROOT, 4600, 2)
        access_policy.observe(gate)
        options["access_policy"] = access_policy
    else:
        checkpoint["host_gates"] = {"example.org": gate}
    result, session = await crawl(monkeypatch, clock, {}, **options)
    assert session.requested == [] and clock[0] == 1000
    assert result["website_access"] == gate
    assert result["checkpoint"]["host_gates"]["example.org"] == gate
    if storage == "shared":
        assert access_policy.gates()["example.org"] == gate
        assert access_policy.timing(ROOT) == (4600, 2)


@pytest.mark.asyncio
async def test_small_clock_difference_only_adds_a_bounded_normal_interval(
        monkeypatch, clock, access_policy):
    access_policy.record_request(ROOT, 1000.25, 2)
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response("<title>Fixture</title>"),
    }, access_policy=access_policy)
    assert result["status"] == "completed"
    assert 1002.25 <= session.sent_at[0] <= 1003.25
    assert clock[0] < 1006
    assert not result.get("website_access")


CHALLENGE_HTML = '''
    <html><head><title>Just a moment...</title>
      <script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>
    </head><body>bait@example.org<a href="/challenge-next">Continue</a></body></html>
'''


@pytest.mark.asyncio
async def test_http_200_challenge_is_manual_host_block_without_company_observations(
        monkeypatch, clock, access_policy):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404), ROOT: Response(CHALLENGE_HTML),
    }, access_policy=access_policy)
    assert session.requested == [ROOT + "robots.txt", ROOT]
    assert result["status"] == "blocked" and result["has_more"] is False
    assert result["pages_checked"] == 0
    assert result["observations"] == [] and result["contacts"] == []
    gate = result["website_access"]
    assert gate["manual_required"] is True and gate["http_status"] == 200
    assert "challenge" in gate["reason"].casefold()
    assert access_policy.gates()["example.org"] == gate


@pytest.mark.asyncio
async def test_late_challenge_retains_good_contacts_and_stops_remaining_host_queue(
        monkeypatch, clock):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response('desk@example.org <a href="/contact">Contact</a>'
                       '<a href="/contact-next">Contact</a>'),
        ROOT + "contact": Response(CHALLENGE_HTML),
    })
    assert session.requested == [ROOT + "robots.txt", ROOT, ROOT + "contact"]
    assert result["status"] == "partial" and result["has_more"] is False
    assert {row["email"] for row in result["contacts"] if row["email"]} == {"desk@example.org"}
    assert result["summary"]["pages_checked"] == 1
    assert ROOT + "contact-next" in result["checkpoint"]["queue"]
    assert ROOT + "contact" in result["checkpoint"]["failures"]
    assert result["checkpoint"]["logs"][-1]["status"] == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [
    '<title>Captcha integration guide</title><p>Just a moment is a challenge page title; '
    'its code can mention /cdn-cgi/challenge.</p>',
    '<title>Just a moment</title><p>This ordinary article discusses captcha and forms.</p>',
    '<title>Just a moment</title><pre>&lt;script src="/cdn-cgi/challenge-platform/"&gt;'
    '&lt;/script&gt;</pre>',
    '<title>Contact us</title><script src="/cdn-cgi/challenge-platform/widget"></script>',
], ids=["article", "title-only", "escaped-code", "widget-with-normal-title"])
async def test_captcha_mentions_or_one_challenge_signal_do_not_block_normal_page(
        monkeypatch, clock, html):
    result, session = await crawl(monkeypatch, clock, {
        ROOT + "robots.txt": Response(status=404),
        ROOT: Response(html + "<p>" + "Company contact information. " * 6 + "desk@example.org</p>"),
    })
    assert result["status"] == "completed"
    assert result["pages_checked"] == 1 and not result.get("website_access")
    assert any(row["email"] == "desk@example.org" for row in result["contacts"])
    assert session.requested == [ROOT + "robots.txt", ROOT]
