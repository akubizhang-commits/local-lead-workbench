"""Synthetic fixtures only: these tests never access the network or a database."""
import asyncio
import os
import socket
import time
import unittest
from unittest.mock import AsyncMock, patch

import pytest

from leadworkbench.sources import website as w


@pytest.fixture
def website_clock(monkeypatch):
    """Advance request spacing without sleeping or weakening production limits."""
    clock = [time.time()]
    real_sleep = asyncio.sleep

    async def advance(delay):
        clock[0] += delay
        await real_sleep(0)

    monkeypatch.setattr(w.time, "time", lambda: clock[0])
    monkeypatch.setattr(w.asyncio, "sleep", advance)
    return clock


pytestmark = pytest.mark.usefixtures("website_clock")


HTML = """
<html><head><title>Fixture Clothing</title></head><body>
<h1>Fixture Clothing</h1><p>We are a clothing wholesaler and distributor.</p>
<main><section class="team">
 <div class="person"><h3>Alice Example</h3><p>Purchasing Director</p>
   <p><a href="mailto:Alice+buy@example.org">Email Alice</a></p></div>
 <div class="person"><h3>Bob Example</h3><p>Sales Manager</p>
   <a href="mailto:bob@example.org">bob@example.org</a></div>
 <div class="person"><h3>Carol Example</h3><p>Procurement Manager</p></div>
</section></main>
<footer><a href="mailto:info@example.org">info@example.org</a>
 <a href="tel:+1-212-555-0100">+1-212-555-0100</a></footer>
<a href="/suppliers">Supplier application</a>
<a href="https://www.linkedin.com/company/fixture">LinkedIn</a>
<a href="https://linkedin.com.evil.example/company/fixture">Fake social</a>
<a href="https://example.net/?next=instagram.com">Fake social 2</a>
<script>var secret = "hidden@example.org";</script>
</body></html>
"""

# Sanitized structural reproduction of the live sales-reps card: two people
# share one outer team-card, but each has an adjacent heading/role/email section.
SHARED_BUYER_CARD = """
<div class="team-card">
<h2>FIRST EXAMPLE</h2>
<p class="role">Buyer<br/></p>
<p><strong>Email:</strong> <a href="mailto:FIRST@example.org">first@example.org</a><br/><br/></p>
<h2>SECOND EXAMPLE</h2>
<p class="role">Buyer<br/></p>
<p class="role"><a href="mailto:SECOND@example.org">Email: second@example.org</a></p>
</div>
<footer><a href="mailto:reception@example.org">Reception</a></footer>
"""


class ParsingTests(unittest.TestCase):
    def test_shared_team_card_preserves_each_explicit_buyer_section(self):
        parsed = w.parse_page(SHARED_BUYER_CARD, "https://example.org/team")
        for local, name, other in [
            ("first", "FIRST EXAMPLE", "SECOND EXAMPLE"),
            ("second", "SECOND EXAMPLE", "FIRST EXAMPLE"),
        ]:
            channels = [c for c in parsed["contacts"] if c["email"]
                        and c["email"].lower() == local + "@example.org"]
            self.assertTrue(channels)
            for contact in channels:
                self.assertEqual(contact["name"], name)
                self.assertEqual(contact["role"], "Buyer")
                self.assertEqual(contact["kind"], "procurement")
                self.assertEqual(contact["role_status"], "explicit")
                self.assertEqual(contact["association_status"], "same_block")
                self.assertIn(name, contact["evidence"])
                self.assertIn("Buyer", contact["evidence"])
                self.assertIn(local + "@example.org", contact["evidence"].lower())
                self.assertNotIn(other, contact["evidence"])
                self.assertEqual(contact["source_url"], "https://example.org/team")
                self.assertTrue(contact["observed_at"])
        footer = next(c for c in parsed["contacts"] if c["email"] == "reception@example.org")
        self.assertIsNone(footer["name"])
        self.assertIsNone(footer["role"])
        self.assertEqual(footer["kind"], "general")

    def test_shared_card_requires_explicit_role_for_each_heading(self):
        for html in [
            SHARED_BUYER_CARD.replace('<div class="team-card">', "<div>"),
            SHARED_BUYER_CARD.replace('<p class="role">Buyer<br/></p>', "", 1),
        ]:
            with self.subTest(html=html):
                contacts = w.parse_page(html, "https://example.org/team")["contacts"]
                self.assertTrue(all(c["name"] is None for c in contacts))

    def test_single_token_names_are_not_expanded_in_explicit_shared_card(self):
        html = SHARED_BUYER_CARD.replace("FIRST EXAMPLE", "First").replace("SECOND EXAMPLE", "Second")
        contacts = w.parse_page(html, "https://example.org/team")["contacts"]
        names = {c["name"] for c in contacts if c["kind"] == "procurement"}
        self.assertEqual(names, {"First", "Second"})

    def test_contact_call_to_action_headings_are_not_people(self):
        html = """<div class="contact-card"><h3>Get in Touch</h3>
        <p>Email our Sales Manager</p><a href="mailto:desk@example.org">Email</a></div>
        <div class="contact-card"><h3>Send a Message</h3>
        <a href="mailto:desk@example.org">Send Message</a></div>"""
        contacts = w.parse_page(html, "https://example.org/contact")["contacts"]
        self.assertTrue(contacts)
        self.assertTrue(all(c["name"] is None for c in contacts))

    def test_content_heading_is_not_company_identity_and_site_metadata_has_evidence(self):
        parsed = w.parse_page("<h1>BRIEF HISTORY</h1>", "https://example.org/history")
        self.assertFalse(any(o["field"] == "name" for o in parsed["observations"]))
        parsed = w.parse_page(
            '<meta property="og:site_name" content="Fixture Clothing"><h1>CONTACT OUR TEAM</h1>',
            "https://example.org/team")
        name = next(o for o in parsed["observations"] if o["field"] == "name")
        self.assertEqual(name["value"], "Fixture Clothing")
        self.assertIn("Fixture Clothing", name["evidence"])

    def test_unmarked_buyer_card_without_email_is_retained(self):
        html = """<div class="profile"><h3>Dana Example</h3><h4>Buyer</h4>
        <a href="tel:+12125550100">Call</a></div>"""
        contacts = w.parse_page(html, "https://example.org/team")["contacts"]
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Dana Example")
        self.assertEqual(contacts[0]["kind"], "procurement")
        self.assertEqual(contacts[0]["role_status"], "explicit")
        self.assertIsNone(contacts[0]["email"])

    def test_evidence_retains_fields_after_long_card_intro_and_long_business_text(self):
        html = ('<div class="person"><p>' + 'Background. ' * 115 +
                '</p><h3>Alice Example</h3><p>Purchasing Director</p>'
                '<p>alice@example.org</p></div><p>' + 'Details. ' * 80 + 'We are a wholesaler.</p>')
        parsed = w.parse_page(html, "https://example.org/")
        alice = next(c for c in parsed["contacts"] if c["email"] == "alice@example.org")
        for value in ("Alice Example", "Purchasing Director", "alice@example.org"):
            self.assertIn(value, alice["evidence"])
        business = next(o for o in parsed["observations"] if o["field"] == "business_types")
        self.assertIn("wholesaler", business["evidence"])
        self.assertIsInstance(business["value"], list)

    def test_footer_procurement_text_does_not_relabel_general_mailbox(self):
        contacts = w.parse_page(
            "<footer>Procurement enquiries: purchasing@example.org. General: info@example.org</footer>",
            "https://example.org/")["contacts"]
        info = next(c for c in contacts if c["email"] == "info@example.org")
        self.assertEqual(info["kind"], "general")
        self.assertEqual(info["role_status"], "unknown")
        self.assertIsNone(info["name"])

    def test_nested_hidden_styles_do_not_crash_or_extract_contacts(self):
        parsed = w.parse_page(
            '<div style="display:none"><p style="display:none">hidden@example.org</p></div>',
            "https://example.org/")
        self.assertEqual(parsed["contacts"], [])

    def test_department_mailbox_uses_its_heading_without_borrowing_person(self):
        html = """<section><h2>Procurement Department</h2><p>vendor.desk@example.org</p></section>
        <footer>general.desk@example.org</footer>"""
        contacts = w.parse_page(html, "https://example.org/contact")["contacts"]
        department = next(c for c in contacts if c["email"] == "vendor.desk@example.org")
        self.assertEqual(department["kind"], "procurement")
        self.assertEqual(department["role_status"], "department")
        self.assertIsNone(department["name"])
        self.assertEqual(department["role"], "Procurement Department")
        footer = next(c for c in contacts if c["email"] == "general.desk@example.org")
        self.assertEqual(footer["kind"], "general")

    def test_navigation_heading_is_not_a_company_name(self):
        parsed = w.parse_page("<h1>Contact Us</h1><title>Contact Us</title>", "https://example.org/contact")
        self.assertFalse(any(o["field"] == "name" for o in parsed["observations"]))

    def test_oversized_dom_and_deep_dom_are_bounded_with_warning(self):
        for html in ["<span>x</span>" * 12001, "<div>" * 100 + "hello" + "</div>" * 100]:
            parsed = w.parse_page(html, "https://example.org/")
            self.assertTrue(any("limit" in s.lower() for s in parsed["warnings"]))
            self.assertEqual(parsed["contacts"], [])

    def test_historical_staff_is_not_reported_as_current_role(self):
        html = """<article class="person"><h3>Alice Example</h3>
        <p>Former Purchasing Director</p><a href="mailto:alice@example.org">Email</a></article>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        self.assertEqual(contacts[0]["role_status"], "historical")

    def test_published_forms_are_recorded_without_submission(self):
        html = """<form action="/apply"><h2>Supplier registration</h2>
        <input name="company"><textarea name="message"></textarea><button>Apply</button></form>
        <form action="http://localhost/submit"><h2>Contact us</h2></form>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["kind"], "supplier_form")
        self.assertEqual(contacts[0]["url"], "https://example.org/apply")
        self.assertIsNone(contacts[0]["name"])
        self.assertIn("/apply", contacts[0]["evidence"])

    def test_personal_phone_does_not_become_company_identity_phone(self):
        html = """<div class="person"><h3>Alice Example</h3><p>Buyer</p>
        <a href="tel:+12125550100">Call me</a></div>"""
        parsed = w.parse_page(html, "https://example.org/")
        self.assertFalse(any(o["field"] == "phone" for o in parsed["observations"]))

    def test_mailbox_outside_single_card_is_not_assigned_to_that_person(self):
        html = """<div class="page"><div class="person"><h3>Alice Example</h3>
        <p>Buyer</p></div><p>reception@example.org</p></div>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        reception = next(c for c in contacts if c["email"])
        self.assertIsNone(reception["name"])
        self.assertEqual(reception["association_status"], "company_only")

    def test_non_person_contact_heading_does_not_create_person(self):
        html = """<div><h2>Contact Us</h2><p>For purchasing questions</p>
        <a href="mailto:desk@example.org">Email</a></div>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        self.assertIsNone(contacts[0]["name"])

    def test_people_are_scoped_to_cards_and_footer_is_company_only(self):
        parsed = w.parse_page(HTML, "https://example.org/")
        contacts = parsed["contacts"]
        alice = next(c for c in contacts if c["email"] == "Alice+buy@example.org")
        self.assertEqual(alice["name"], "Alice Example")
        self.assertEqual(alice["role_status"], "explicit")
        self.assertEqual(alice["association_status"], "same_block")
        self.assertEqual(alice["kind"], "procurement")
        bob = next(c for c in contacts if c["email"] == "bob@example.org")
        self.assertEqual(bob["name"], "Bob Example")
        self.assertEqual(bob["kind"], "general")
        footer = next(c for c in contacts if c["email"] == "info@example.org")
        self.assertIsNone(footer["name"])
        self.assertEqual(footer["association_status"], "company_only")
        self.assertEqual(footer["kind"], "general")
        carol = next(c for c in contacts if c["name"] == "Carol Example")
        self.assertIsNone(carol["email"])
        self.assertEqual(carol["kind"], "procurement")
        self.assertNotIn("hidden@example.org", str(contacts))
        self.assertTrue(any(c["kind"] == "supplier_form" for c in contacts))

    def test_multiple_people_without_cards_do_not_get_email_associations(self):
        html = """<section><h2>Alice Example</h2><p>Buyer</p>
        <h2>Bob Example</h2><p>Procurement</p><p>shared@example.org</p></section>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        self.assertEqual(len(contacts), 1)
        self.assertIsNone(contacts[0]["name"])
        self.assertEqual(contacts[0]["association_status"], "company_only")

    def test_nested_channels_use_person_card_but_general_mailbox_is_not_personal(self):
        html = """<article class="person"><h3>Alice Example</h3>
        <div class="details"><p>Buyer</p><p><span>alice@example.org</span></p></div>
        <a href="mailto:info@example.org">Company email</a></article>"""
        contacts = w.parse_page(html, "https://example.org/")["contacts"]
        alice = next(c for c in contacts if c["email"] == "alice@example.org")
        info = next(c for c in contacts if c["email"] == "info@example.org")
        self.assertEqual(alice["name"], "Alice Example")
        self.assertIsNone(info["name"])

    def test_evidence_identity_business_types_and_social_allowlist(self):
        parsed = w.parse_page(HTML, "https://example.org/")
        fields = {o["field"]: o["value"] for o in parsed["observations"]}
        self.assertEqual(fields["name"], "Fixture Clothing")
        self.assertIn("wholesaler", fields["business_types"])
        self.assertIn("distributor", fields["business_types"])
        self.assertEqual(fields["phone"], "+1-212-555-0100")
        self.assertEqual(parsed["social_links"], ["https://www.linkedin.com/company/fixture"])
        for item in parsed["observations"] + parsed["contacts"]:
            self.assertEqual(item["source_url"], "https://example.org/")
            self.assertTrue(item["evidence"])
            self.assertTrue(item["observed_at"].endswith("+00:00"))

    def test_no_email_guesses_hidden_elements_and_dynamic_warning(self):
        html = """<div id="root"></div><script src="/app.js"></script>
        <div hidden>hidden@example.org</div><p style="display:none">secret@example.org</p>"""
        parsed = w.parse_page(html, "https://example.org/")
        self.assertEqual(parsed["contacts"], [])
        self.assertTrue(any("dynamic" in s.lower() for s in parsed["warnings"]))


class URLTests(unittest.TestCase):
    def test_rejects_private_reserved_credentials_and_unsafe_schemes(self):
        for url in [
            "http://127.0.0.1", "http://10.1.1.1", "http://169.254.169.254",
            "http://100.64.0.1", "http://[::1]", "http://[fd00::1]",
            "http://[fe80::1]", "http://[::ffff:127.0.0.1]", "http://[2002:7f00:1::]",
            "http://[64:ff9b::7f00:1]", "http://224.0.0.1", "http://0.0.0.0",
            "http://192.0.2.1", "http://localhost", "http://foo.localhost.",
            "http://foo.local", "file:///etc/passwd", "ftp://example.org/",
            "https://user:pass@example.org", "https://example.org:8443",
            "http://example.org\\@127.0.0.1", "https://example.org/\nsecret",
            "mailto:buyer@example.org", "tel:+12125550100", "javascript:alert(1)",
            "data:text/html,hello", "javascript://example.org/", "file://example.org/",
            "https:/example.org", "http:example.org", "about:blank",
        ]:
            with self.subTest(url=url), self.assertRaises(w.UnsafeURL):
                w.normalize_url(url)

    def test_normalizes_without_dropping_content_parameters(self):
        self.assertEqual(
            w.normalize_url("HTTPS://Example.ORG:443/contact?lang=en&utm_source=x#team"),
            "https://example.org/contact?lang=en",
        )
        self.assertEqual(w.normalize_url("example.org"), "https://example.org/")
        self.assertEqual(w.normalize_url("https://[2606:4700:4700::1111]/"),
                         "https://[2606:4700:4700::1111]/")
        for path in ("/public/../private", "/public/%2e%2e/private"):
            self.assertEqual(w.normalize_url("https://example.org" + path),
                             "https://example.org/private")


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_connector_dials_validated_ip_and_ignores_environment_proxy(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        dialed = []

        async def no_socket(connector, *args, **kwargs):
            dialed.extend(info[4][0] for info in kwargs["addr_infos"])
            raise OSError("Synthetic socket boundary: network disabled")

        # Exercise actual ClientSession/TCPConnector resolution, stopping before
        # opening any socket. An environment proxy must never change the target.
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9999",
                                     "http_proxy": "http://127.0.0.1:9999"}), \
             patch.object(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answers)), \
             patch.object(w.aiohttp.TCPConnector, "_wrap_create_connection", no_socket):
            result = await w.enrich_website("http://example.org/")
        self.assertEqual(dialed, ["93.184.216.34"])
        self.assertEqual(result["status"], "blocked")

    async def test_filters_mixed_answers_and_passes_numeric_public_ip_to_connector(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        with patch.object(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answers)):
            resolver = w.PublicResolver()
            records = await resolver.resolve("example.org", 443)
            self.assertEqual({r["host"] for r in records},
                             {"93.184.216.34", "2606:4700:4700::1111"})
            self.assertTrue(all(r["hostname"] == "example.org" for r in records))
            await resolver.close()

    async def test_dns_rebinding_next_resolution_cannot_return_private_address(self):
        good = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        bad = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with patch.object(asyncio.get_running_loop(), "getaddrinfo",
                          AsyncMock(side_effect=[good, bad])):
            resolver = w.PublicResolver()
            self.assertTrue(await resolver.resolve("example.org", 443))
            with self.assertRaises(w.UnsafeURL):
                await resolver.resolve("example.org", 443)


class Response:
    def __init__(self, text="", status=200, headers=None, chunks=None):
        self.status = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
        self.charset = "utf-8"
        self.content = self
        self.chunks = chunks if chunks is not None else [text.encode()]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class Session:
    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if kwargs.get("allow_redirects") is not False:
            raise AssertionError("Redirects must be validated manually")
        if kwargs.get("proxy") is not None:
            raise AssertionError("No proxy")
        response = self.routes[url]
        if isinstance(response, Exception):
            raise response
        return response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_compression_bomb_rejected_before_reading_body(self):
        session = Session({"https://example.org/": Response(headers={
            "Content-Encoding": "gzip"}, chunks=[])})
        with self.assertRaises(w.FetchError):
            await w.fetch_page(session, "https://example.org/")

    async def test_redirect_to_private_address_is_never_requested(self):
        session = Session({"https://example.org/": Response(status=302, headers={
            "Location": "http://127.0.0.1/admin"})})
        with self.assertRaises(w.UnsafeURL):
            await w.fetch_page(session, "https://example.org/")
        self.assertEqual(session.requested, ["https://example.org/"])

    async def test_cross_site_redirect_requires_review(self):
        session = Session({"https://example.org/": Response(status=302, headers={
            "Location": "https://other.org/"})})
        with self.assertRaises(w.UnsafeURL):
            await w.fetch_page(session, "https://example.org/")
        self.assertEqual(len(session.requested), 1)

    async def test_response_byte_limit_applies_to_stream_not_only_content_length(self):
        session = Session({"https://example.org/": Response(chunks=[b"x" * 8, b"y" * 8])})
        with self.assertRaises(w.FetchError):
            await w.fetch_page(session, "https://example.org/", max_bytes=10)

    async def test_redirect_loop_is_bounded(self):
        session = Session({"https://example.org/": Response(status=302, headers={"Location": "/"})})
        with self.assertRaises(w.FetchError):
            await w.fetch_page(session, "https://example.org/")
        self.assertLessEqual(len(session.requested), 6)

    async def test_unsupported_document_is_not_parsed_as_html(self):
        session = Session({"https://example.org/": Response("%PDF", headers={
            "Content-Type": "application/pdf"})})
        with self.assertRaises(w.FetchError):
            await w.fetch_page(session, "https://example.org/")


class CrawlTests(unittest.IsolatedAsyncioTestCase):
    async def test_excluded_routes_do_not_use_page_budget_or_cause_partial(self):
        excluded = [
            "/collections/all", "/products/shirt", "/cart", "/checkout",
            "/account/login", "/customer_authentication/login",
            "/policies/privacy-policy", "/pages/shipping-policy",
            "/en/products/shirt", "/LOGIN", "/pages/terms-of-service",
        ]
        links = "".join(f'<a href="{path}">Supplier</a>' for path in excluded)
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response(
                links + '<a href="/cart#again">Cart</a><a href="/contact">Contact</a>'
                '<a href="/pages/wholesale-vendor-info">Vendors</a>'),
            "https://example.org/contact": Response('<a href="/cart?utm_source=footer">Cart</a>'),
            "https://example.org/pages/wholesale-vendor-info": Response("<p>Supplier information</p>"),
        }, max_pages=3)
        self.assertEqual(result["pages_checked"], 3)
        self.assertEqual(result["pages_skipped"], 11)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(requested), 4)  # robots plus three relevant pages
        self.assertIn("https://example.org/pages/wholesale-vendor-info", requested)

    async def test_second_level_buyer_link_precedes_ordinary_queued_page(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response(
                '<a href="/news">News</a><a href="/about">About</a>'),
            "https://example.org/about": Response('<a href="/pages/buyer-directory">Directory</a>'),
            "https://example.org/pages/buyer-directory": Response("<p>Buyer directory</p>"),
        }, max_pages=3)
        self.assertEqual(requested[-2:], [
            "https://example.org/about", "https://example.org/pages/buyer-directory"])
        self.assertEqual(result["pages_skipped"], 0)
        self.assertEqual(result["status"], "partial")  # ordinary news remains

    async def test_rediscovered_priority_link_promotes_existing_queue_entry(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response(
                '<a href="/news">News</a><a href="/directory">Directory</a><a href="/about">About</a>'),
            "https://example.org/about": Response('<a href="/directory">Supplier contacts</a>'),
            "https://example.org/directory": Response("<p>Contact directory</p>"),
        }, max_pages=3)
        self.assertEqual(requested[-1], "https://example.org/directory")
        self.assertEqual(requested.count("https://example.org/directory"), 1)
        self.assertEqual(result["pages_checked"], 3)

    async def test_excluded_start_is_blocked_without_network(self):
        with patch.object(w.aiohttp, "ClientSession", side_effect=AssertionError("Must not fetch")):
            result = await w.enrich_website("https://example.org/products/shirt")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pages_checked"], 0)
        self.assertEqual(result["pages_skipped"], 1)
        self.assertTrue(any("excluded" in warning.lower() for warning in result["warnings"]))

    async def test_redirect_to_login_is_intentionally_skipped(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response('<a href="/contact">Contact</a>'),
            "https://example.org/contact": Response(
                status=302, headers={"Location": "/customer_authentication/login"}),
        })
        self.assertEqual(result["pages_checked"], 1)
        self.assertEqual(result["pages_skipped"], 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(requested), 3)

    async def test_transport_retries_share_the_wire_request_limit(self):
        options = {}
        session = Session({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response("<h1>Fixture</h1>"),
        })

        def factory(**kwargs):
            options.update(kwargs)
            return session

        with patch.object(w, "MAX_REQUESTS", 3), \
             patch.object(w.aiohttp, "ClientSession", side_effect=factory):
            await w.enrich_website("https://example.org/")
            request_type = options["request_class"]
            request = object.__new__(request_type)
            # Stop at the send boundary: repeated sends model aiohttp's
            # automatic disconnected-connection retry, with no socket or DB.
            with patch.object(w.aiohttp.ClientRequest, "send", AsyncMock(return_value=None)):
                for _ in range(3):
                    await request.send(None)
                with self.assertRaises(w.RequestLimit):
                    await request.send(None)

    async def test_request_limit_counts_robots_and_redirects(self):
        with patch.object(w, "MAX_REQUESTS", 3):
            result, requested = await self.crawl({
                "https://example.org/robots.txt": Response(status=404),
                "https://example.org/": Response(status=302, headers={"Location": "/one"}),
                "https://example.org/one": Response(status=302, headers={"Location": "/two"}),
            })
        self.assertEqual(len(requested), 3)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("request limit" in s.lower() for s in result["warnings"]))

    async def test_failed_robots_origin_is_not_requested_for_every_page(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response(
                '<h1>Fixture</h1><a href="http://example.org/a">Contact</a>'
                '<a href="http://example.org/b">Team</a>'),
            "http://example.org/robots.txt": Response(status=503),
        })
        self.assertEqual(requested.count("http://example.org/robots.txt"), 1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(all(isinstance(link, str) for link in result["social_links"]))

    async def test_total_budget_includes_slow_html_parser(self):
        original = w.parse_page

        def slow_parser(*args):
            time.sleep(0.15)
            return original(*args)

        with patch.object(w, "TOTAL_TIMEOUT", 0.02), patch.object(w, "parse_page", slow_parser):
            result, _ = await self.crawl({
                "https://example.org/robots.txt": Response(status=404),
                "https://example.org/": Response("<h1>Fixture</h1>"),
            })
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pages_checked"], 0)

    async def test_robots_denies_entire_site(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response("User-agent: *\nDisallow: /"),
        })
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(requested, ["https://example.org/robots.txt"])

    async def test_blocking_http_status_is_reported_as_blocked(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                result, _ = await self.crawl({
                    "https://example.org/robots.txt": Response(status=404),
                    "https://example.org/": Response(status=status),
                })
                self.assertEqual(result["status"], "blocked")

    async def test_failing_page_preserves_other_observations(self):
        result, _ = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response('<h1>Fixture</h1><a href="/contact">Contact</a>'),
            "https://example.org/contact": Response(status=500),
        })
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["observations"][0]["value"], "Fixture")

    async def crawl(self, routes, **kwargs):
        session = Session(routes)
        with patch.object(w.aiohttp, "ClientSession", return_value=session):
            result = await w.enrich_website("https://example.org/", **kwargs)
        return result, session.requested

    async def test_robots_disallow_prevents_request_and_keeps_existing_results(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response("User-agent: *\nDisallow: /private"),
            "https://example.org/": Response('<title>Fixture</title><a href="/private">Team</a>'),
        })
        self.assertEqual(result["pages_checked"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("https://example.org/private", requested)
        self.assertTrue(any("robots" in s.lower() for s in result["warnings"]))

    async def test_robots_failure_is_closed_and_missing_robots_is_allowed(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=503),
        })
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pages_checked"], 0)
        result, _ = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response("<title>Fixture</title>"),
        })
        self.assertEqual(result["status"], "completed")

    async def test_max_pages_same_site_dedup_and_contact_priority(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response(status=404),
            "https://example.org/": Response('''<title>Fixture</title>
              <a href="/products">Products</a><a href="/contact#one">Contact</a>
              <a href="/contact#two">Contact</a><a href="https://other.org">Other</a>'''),
            "https://example.org/contact": Response(HTML),
        }, max_pages=2)
        self.assertEqual(result["pages_checked"], 2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(requested[-1], "https://example.org/contact")
        self.assertTrue(result["contacts"])
        self.assertNotIn("https://other.org/", requested)

    async def test_depth_bound(self):
        routes = {"https://example.org/robots.txt": Response(status=404)}
        for i, path in enumerate(["/", "/a", "/b"]):
            routes["https://example.org" + path] = Response(
                f'<title>Fixture</title><a href="/{chr(97+i)}">More</a>')
        result, requested = await self.crawl(routes)
        self.assertEqual(result["pages_checked"], 3)
        self.assertNotIn("https://example.org/c", requested)
        self.assertEqual(result["status"], "partial")

    async def test_redirect_target_must_also_obey_robots(self):
        result, requested = await self.crawl({
            "https://example.org/robots.txt": Response("User-agent: *\nDisallow: /private"),
            "https://example.org/": Response(status=302, headers={"Location": "/private"}),
        })
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("https://example.org/private", requested)

    async def test_invalid_start_is_blocked_and_page_budget_is_validated(self):
        result = await w.enrich_website("http://localhost/")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])
        for value in [0, -1, True, 21, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                await w.enrich_website("https://example.org/", max_pages=value)

    async def test_total_timeout_and_cancellation(self):
        async def slow(*args, **kwargs):
            await asyncio.Event().wait()
        with patch.object(w, "TOTAL_TIMEOUT", 0.01), patch.object(w, "fetch_page", slow):
            result = await w.enrich_website("https://example.org/")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("time" in s.lower() for s in result["warnings"]))
        with patch.object(w, "fetch_page", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await w.enrich_website("https://example.org/")


if __name__ == "__main__":
    unittest.main()


class ContactQualityParsingTests(unittest.TestCase):
    def parse(self, html):
        return w.parse_page(html, 'https://example.org/team')['contacts']

    def test_service_and_marketing_cards_are_not_people(self):
        for heading, role in [
            ('Quality Control', 'Plan smaller production runs with realistic sourcing options'),
            ('Why Buyers Use Fashion Atlas Instead Of Searching Alone', 'Buyer protection'),
            ('Alice Example', 'Buyer'),
        ]:
            with self.subTest(heading=heading):
                html = f'<div class="service-card"><h3>{heading}</h3><p>{role}</p></div>' * 3
                self.assertEqual(self.parse(html), [])

    def test_unmarked_marketing_is_not_a_person(self):
        self.assertEqual(self.parse('<div><h3>Quality Control</h3><p>Plan smaller production '
                                    'runs with realistic sourcing options</p></div>'), [])

    def test_roles_and_channels_cannot_cross_service_cards(self):
        html = '''<div><div class="profile"><h3>Alice Example</h3></div>
        <div class="service-card"><p>Buyer</p><p>desk@example.org</p></div></div>'''
        contacts = self.parse(html)
        self.assertEqual(len(contacts), 1)
        self.assertIsNone(contacts[0]['name'])
        self.assertEqual(contacts[0]['kind'], 'general')
        self.assertNotIn('Alice Example', contacts[0]['evidence'])

    def test_article_and_navigation_do_not_imply_employment(self):
        for wrapper in ['article', 'nav']:
            html = f'<{wrapper}><div class="person"><h3>Alice Example</h3><p>Buyer</p></div></{wrapper}>'
            self.assertEqual(self.parse(html), [])

    def test_supplier_links_require_application_or_contact_intent(self):
        contacts = self.parse('''<a href="/compare">Compare suppliers</a>
        <a href="/why">Why buyers choose our suppliers</a>
        <a href="/register">Supplier registration</a>
        <a href="/contact">Contact our supplier team</a>''')
        self.assertEqual({c['url'] for c in contacts},
                         {'https://example.org/register', 'https://example.org/contact'})
        self.assertTrue(all(c['kind'] == 'supplier_form' for c in contacts))

    def test_generic_department_is_company_only(self):
        contacts = self.parse('<section><h2>Customer Service Department</h2>'
                              '<p>desk@example.org</p></section>')
        self.assertEqual(len(contacts), 1)
        self.assertIsNone(contacts[0]['name'])
        self.assertEqual(contacts[0]['role'], 'Customer Service Department')
        self.assertEqual(contacts[0]['role_status'], 'department')
        self.assertEqual(contacts[0]['kind'], 'general')

    def test_jsonld_person_requires_target_worksfor_and_preserves_evidence(self):
        import json
        for organization in [{'@id': 'https://example.org/#org'}, {'url': 'https://www.example.org/'},
                             {'name': 'Fixture Clothing'}]:
            person = {'@context': 'https://schema.org', '@type': 'Person', 'name': 'Alice Example',
                      'jobTitle': 'Purchasing Director', 'worksFor': organization}
            contacts = self.parse('<meta property="og:site_name" content="Fixture Clothing">'
                                  '<script type="application/ld+json">' + json.dumps(person) + '</script>')
            self.assertEqual(len(contacts), 1)
            self.assertEqual(contacts[0]['name'], 'Alice Example')
            self.assertIsNone(contacts[0]['email'])
            self.assertEqual(contacts[0]['kind'], 'procurement')
            for value in ['Alice Example', 'Purchasing Director', 'worksFor']:
                self.assertIn(value, contacts[0]['evidence'])

    def test_jsonld_graph_resolves_organization_but_rejects_authors(self):
        import json
        graph = [{'@id': '#org', '@type': 'Organization', 'name': 'Fixture Clothing',
                  'url': 'https://example.org/'},
                 {'@id': '#alice', '@type': 'Person', 'name': 'Alice Example',
                  'jobTitle': 'Buyer', 'worksFor': {'@id': '#org'}, 'email': 'alice@example.org'},
                 {'@type': 'Article', 'author': {'@id': '#bob'}},
                 {'@id': '#bob', '@type': 'Person', 'name': 'Bob Example', 'jobTitle': 'Buyer',
                  'worksFor': {'@id': '#org'}}]
        contacts = self.parse('<script type="application/ld+json">' + json.dumps({'@graph': graph}) + '</script>')
        self.assertEqual([c['name'] for c in contacts], ['Alice Example'])
        self.assertEqual(contacts[0]['email'], 'alice@example.org')

    def test_unaffiliated_or_malformed_structured_people_are_ignored(self):
        import json
        for worksfor in [None, {'name': 'Other Company'}, {'url': 'https://other.org/'}]:
            person = {'@type': 'Person', 'name': 'Alice Example', 'jobTitle': 'Buyer', 'worksFor': worksfor}
            self.assertEqual(self.parse('<script type="application/ld+json">' + json.dumps(person) + '</script>'), [])
        self.assertEqual(self.parse('<script type="application/ld+json">{bad json</script>'), [])

    def test_microdata_person_requires_affiliation_even_inside_person_card(self):
        for company, expected in [('https://example.org/', 1), ('https://other.org/', 0)]:
            html = f'''<div class="person" itemscope itemtype="https://schema.org/Person">
            <meta itemprop="name" content="Alice Example"><meta itemprop="jobTitle" content="Buyer">
            <div itemprop="worksFor" itemscope itemtype="https://schema.org/Organization">
            <link itemprop="url" href="{company}"></div></div>'''
            contacts = self.parse(html)
            self.assertEqual(len(contacts), expected)
            if expected:
                self.assertEqual(contacts[0]['name'], 'Alice Example')

    def test_visible_microdata_with_wrong_affiliation_does_not_fall_back(self):
        html = '''<div class="person" itemscope itemtype="https://schema.org/Person">
        <h3 itemprop="name">Alice Example</h3><p itemprop="jobTitle">Buyer</p>
        <a itemprop="worksFor" href="https://other.org/">Other Company</a></div>'''
        self.assertEqual(self.parse(html), [])


class StructuredScopeTests(unittest.TestCase):
    def parse(self, data):
        import json
        return w.parse_page('<script type="application/ld+json">' + json.dumps(data) + '</script>',
                            'https://example.org/team')['contacts']

    def person(self, **extra):
        return {'@type': 'Person', 'name': 'Alice Example', 'jobTitle': 'Buyer',
                'worksFor': {'url': 'https://example.org/'}, **extra}

    def test_author_string_reference_and_nested_author_are_excluded(self):
        for author in ['#alice', {'@id': '#alice'}, self.person()]:
            graph = {'@graph': [{'@type': 'Article', 'author': author},
                                self.person(**{'@id': '#alice'})]}
            if isinstance(author, dict) and '@type' in author:
                graph['@graph'].pop()
            self.assertEqual(self.parse(graph), [])

    def test_declared_foreign_organization_url_does_not_match_name_alone(self):
        import json
        html = '<meta property="og:site_name" content="Fixture">' + (
            '<script type="application/ld+json">' + json.dumps(self.person(
                worksFor={'name': 'Fixture', 'url': 'https://other.org/'})) + '</script>')
        self.assertEqual(w.parse_page(html, 'https://example.org/team')['contacts'], [])

    def test_shared_structured_mailbox_is_company_only(self):
        rows = self.parse(self.person(email='info@example.org'))
        mailbox = next(c for c in rows if c['email'])
        self.assertIsNone(mailbox['name'])
        self.assertEqual(mailbox['kind'], 'general')
        self.assertTrue(any(c['name'] == 'Alice Example' and c['email'] is None for c in rows))

    def test_structured_depth_bytes_and_count_are_bounded(self):
        nested = self.person()
        for _ in range(40):
            nested = {'next': nested}
        self.assertEqual(self.parse(nested), [])
        self.assertEqual(self.parse(self.person(description='x' * 150_000)), [])
        rows = self.parse({'@graph': [self.person(name='Alice Example', email=f'a{i}@example.org')
                                     for i in range(100)]})
        self.assertLessEqual(len(rows), 64)
        self.assertGreater(len(rows), 0)

    def test_department_evidence_does_not_cross_sibling_blocks(self):
        html = '''<div><section><h2>Procurement Department</h2></section>
        <div><p>desk@example.org</p></div></div>'''
        rows = w.parse_page(html, 'https://example.org/contact')['contacts']
        self.assertEqual(rows[0]['kind'], 'general')
        self.assertIsNone(rows[0]['role'])
        self.assertNotIn('Procurement Department', rows[0]['evidence'])


class SupplierIntentTests(unittest.TestCase):
    def test_supplier_marketing_mentions_of_forms_are_not_actions(self):
        html = '''<a href="/compare">Compare supplier application tools</a>
        <a href="/article">Why suppliers contact buyers</a>
        <a href="/services">Supplier sourcing services: contact us for a quote</a>
        <a href="/apply">Apply to become a supplier</a>'''
        rows = w.parse_page(html, 'https://example.org/')['contacts']
        self.assertEqual([c['url'] for c in rows if c['kind'] == 'supplier_form'],
                         ['https://example.org/apply'])


class PublishedEmailConflictTests(unittest.TestCase):
    def test_visible_email_and_mailto_disagreement_preserves_both_and_warns(self):
        html = '<a href="mailto:info@example.net">info@example.com</a>'
        result = w.parse_page(html, 'https://example.com/contact')
        self.assertEqual({c['email'] for c in result['contacts']},
                         {'info@example.net', 'info@example.com'})
        self.assertTrue(all(c['name'] is None for c in result['contacts']))
        self.assertTrue(any('Published email conflict' in warning and
                            'info@example.net' in warning and 'info@example.com' in warning
                            for warning in result['warnings']))

    def test_case_differences_and_mailto_body_are_not_recipient_conflicts(self):
        html = '<a href="mailto:ALICE@example.com?body=other@example.net">alice@example.com</a>'
        result = w.parse_page(html, 'https://example.com/contact')
        self.assertEqual(len(result['contacts']), 1)
        self.assertFalse(any('conflict' in warning for warning in result['warnings']))


class SalesRepresentativeTests(unittest.TestCase):
    def test_sales_rep_title_is_preserved_with_adjacent_language_and_extension(self):
        html = '''<div class="team-card"><h2>JONATHAN MINA</h2>
        <p class="role">Sales Rep</p><p>Language: English &amp; Spanish</p>
        <p>Extension: 109</p><a href="mailto:jonathan@example.org">Email</a></div>'''
        rows = w.parse_page(html, 'https://example.org/team')['contacts']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'JONATHAN MINA')
        self.assertEqual(rows[0]['role'], 'Sales Rep')
        self.assertEqual(rows[0]['kind'], 'general')
        self.assertEqual(rows[0]['role_status'], 'explicit')
        self.assertEqual(rows[0]['association_status'], 'same_block')
        self.assertEqual(rows[0]['email'], 'jonathan@example.org')
