"""Form entries: only contact-type pages count, at most one per site (synthetic sites, no network)."""
import json
from unittest.mock import patch

import pytest

from leadworkbench.sources import website as w
from leadworkbench.website_link_policy import contact_page
from test_website import Response, Session, website_clock  # noqa: F401

pytestmark = pytest.mark.usefixtures("website_clock")
ROOT = "https://example.org/"


def form(action, heading="Contact us"):
    return (f'<form action="{action}"><h2>{heading}</h2><input type="email" name="email">'
            f'<textarea name="message"></textarea><button>Send</button></form>')


def page(title, body=""):
    return Response(f"<html><head><title>{title}</title></head><body>{body}</body></html>")


async def crawl(routes, checkpoint=None, max_pages=w.WEBSITE_BATCH_SIZE):
    with patch.object(w.aiohttp, "ClientSession", return_value=Session(routes)):
        return await w.enrich_website(ROOT, checkpoint=checkpoint, max_pages=max_pages)


def forms(result):
    return [c for c in result["contacts"] if c["kind"] in {"contact_form", "supplier_form"}]


@pytest.mark.parametrize("url,title,expected", [
    (ROOT + "contact-us", "", True),
    (ROOT + "request-a-quote", "", True),
    (ROOT + "become-a-dealer", "", True),
    (ROOT + "supplier-registration", "", True),
    (ROOT + "page", "Contact Us | Acme", True),
    (ROOT + "page", "Request a quote - Acme", True),
    (ROOT + "联系我们", "", True),
    (ROOT + "blog/how-to-contact-a-supplier", "Contact your supplier", False),
    (ROOT + "privacy-policy", "Privacy Policy | Acme", False),
    (ROOT + "products/metal-sheet", "Metal sheet", False),
    (ROOT + "careers", "Careers", False),
    (ROOT + "about", "About ABC Metal Suppliers", False),
    (ROOT, "ABC Metal Suppliers", False),
])
def test_contact_page_classification(url, title, expected):
    assert contact_page(url, title) is expected


@pytest.mark.asyncio
async def test_blog_and_privacy_forms_are_not_entries_when_a_contact_page_has_one():
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", '<a href="/blog/news">Blog</a><a href="/privacy">Privacy</a>'
                                 '<a href="/contact">Contact</a>'),
              ROOT + "blog/news": page("News | Acme", form("/blog/news", "Leave us a message")),
              ROOT + "privacy": page("Privacy Policy", form("/privacy", "Leave us a message")),
              ROOT + "contact": page("Contact | Acme", form("/contact/send"))}
    result = await crawl(routes)
    assert [(c["url"], c["source_url"]) for c in forms(result)] == [(ROOT + "contact/send", ROOT + "contact")]


@pytest.mark.asyncio
async def test_several_contact_page_forms_count_once():
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", '<a href="/contact">Contact</a><a href="/request-a-quote">Quote</a>'),
              ROOT + "contact": page("Contact", form("/contact/a") + form("/contact/b", "Get in touch")),
              ROOT + "request-a-quote": page("Quote", form("/quote/send", "Send us a message"))}
    result = await crawl(routes)
    assert [c["url"] for c in forms(result)] == [ROOT + "contact/a"]
    assert result["summary"]["contacts_found"] == len(result["contacts"])


@pytest.mark.asyncio
async def test_only_non_contact_forms_keep_the_earliest_one():
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", '<a href="/blog/a">Blog A</a><a href="/blog/b">Blog B</a>'),
              ROOT + "blog/a": page("Blog A", form("/blog/a", "Leave us a message")),
              ROOT + "blog/b": page("Blog B", form("/blog/b", "Leave us a message"))}
    result = await crawl(routes)
    assert [c["url"] for c in forms(result)] == [ROOT + "blog/a"]


@pytest.mark.asyncio
async def test_fallback_waits_for_the_last_batch_and_a_later_contact_form_wins():
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", form("/", "Leave us a message") + '<a href="/team-x">Team</a>'),
              ROOT + "team-x": page("Team", '<a href="/contact">Contact</a>'),
              ROOT + "contact": page("Contact", form("/contact/send"))}
    first = await crawl(routes, max_pages=1)
    assert first["has_more"] and not forms(first)
    second = await crawl(routes, checkpoint=json.loads(json.dumps(first["checkpoint"])))
    assert not second["has_more"]
    assert [c["url"] for c in forms(second)] == [ROOT + "contact/send"]


@pytest.mark.asyncio
async def test_fallback_survives_batches_when_no_contact_page_has_a_form():
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", form("/", "Leave us a message") + '<a href="/team-x">Team</a>'),
              ROOT + "team-x": page("Team", form("/team-x", "Leave us a message"))}
    first = await crawl(routes, max_pages=1)
    assert first["has_more"] and not forms(first)
    second = await crawl(routes, checkpoint=json.loads(json.dumps(first["checkpoint"])))
    assert [(c["url"], c["source_url"]) for c in forms(second)] == [(ROOT, ROOT)]


def resumed(checkpoint, clock):
    # Mirrors Store.control("resume") once the 503 cooldown has passed: host
    # gates are released and failed URLs go back into the queue.
    checkpoint = json.loads(json.dumps(checkpoint))
    clock[0] = max([clock[0], *(g.get("not_before", 0) for g in checkpoint.get("host_gates", {}).values())]) + 1
    checkpoint["host_gates"] = {}
    failures = checkpoint.get("failures", {})
    checkpoint.setdefault("queue", {}).update(failures)
    checkpoint["attempted"] = [u for u in checkpoint.get("attempted", []) if u not in failures]
    checkpoint["failures"] = {}
    return checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("contact_body,expected", [
    (form("/contact/send"), [ROOT + "contact/send"]),
    ("<p>Call us</p>", [ROOT]),
])
async def test_partial_stop_keeps_fallback_pending_until_a_resume_finishes(contact_body, expected, website_clock):
    home = page("Acme", form("/", "Leave us a message") + '<a href="/contact">Contact</a>')
    first = await crawl({ROOT + "robots.txt": Response(status=404), ROOT: home,
                         ROOT + "contact": Response(status=503)})
    assert not first["has_more"] and first["status"] == "partial"
    assert not forms(first)
    second = await crawl({ROOT + "robots.txt": Response(status=404), ROOT: home,
                          ROOT + "contact": page("Contact | Acme", contact_body)},
                         checkpoint=resumed(first["checkpoint"], website_clock))
    assert [c["url"] for c in forms(second)] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("dead", [Response(status=404), Response(status=410)])
async def test_permanent_dead_link_does_not_hold_back_the_fallback(dead, website_clock):
    routes = {ROOT + "robots.txt": Response(status=404),
              ROOT: page("Acme", form("/", "Leave us a message") + '<a href="/team-x">Team</a>'),
              ROOT + "team-x": dead}
    first = await crawl(routes)
    assert first["status"] == "partial"
    assert [(c["url"], c["source_url"]) for c in forms(first)] == [(ROOT, ROOT)]
    # A resume that hits the same dead link again keeps exactly that one entry.
    second = await crawl(routes, checkpoint=resumed(first["checkpoint"], website_clock))
    assert forms(second) == [] and second["checkpoint"]["form_entry"]["kept"]
    assert second["summary"]["contacts_found"] == first["summary"]["contacts_found"]


@pytest.mark.asyncio
async def test_robots_disallowed_page_does_not_hold_back_the_fallback():
    routes = {ROOT + "robots.txt": Response("User-agent: *\nDisallow: /team-x\n"),
              ROOT: page("Acme", form("/", "Leave us a message") + '<a href="/team-x">Team</a>')}
    result = await crawl(routes)
    assert result["summary"]["failed"] == 1
    assert [c["url"] for c in forms(result)] == [ROOT]
