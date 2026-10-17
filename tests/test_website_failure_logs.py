"""Offline regressions derived from sanitized runtime failure categories."""
from unittest.mock import patch

import pytest

from leadworkbench.sources import website as w
from leadworkbench.website_link_policy import link_kind
from test_website import Response, Session, website_clock

pytestmark = pytest.mark.usefixtures("website_clock")


@pytest.mark.parametrize("path", [
    "/uploads/photo.JPG?size=large", "/uploads/team.webp", "/bullet.gif",
    "/blogs/news.atom", "/news.rss", "/contact.svg",
])
def test_explicit_non_html_resources_are_assets(path):
    assert link_kind("https://example.org" + path, "Contact us") == "asset"


@pytest.mark.parametrize("path", ["/contact", "/team/photos", "/news", "/download?file=photo.jpg"])
def test_uncertain_pages_remain_eligible(path):
    assert link_kind("https://example.org" + path) == "page"


@pytest.mark.asyncio
async def test_asset_links_skip_without_requests_or_false_contact_forms():
    root = "https://example.org/"
    session = Session({
        root + "robots.txt": Response(status=404),
        root: Response('<a href="/contact.jpg">Contact us</a><a href="/news.atom">News</a>'
                       '<a href="/contact">Contact us</a>'),
        root + "contact": Response("desk@example.org"),
        root + "contact.jpg": Response(headers={"Content-Type": "image/jpeg"}),
        root + "news.atom": Response(headers={"Content-Type": "application/atom+xml"}),
    })
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website(root)
    assert result["status"] == "completed"
    assert result["summary"]["pages_skipped"] == 2
    assert result["summary"]["failed"] == 0
    assert not any((c.get("url") or "").endswith(".jpg") for c in result["contacts"])
    assert session.requested == [root + "robots.txt", root, root + "contact"]


@pytest.mark.asyncio
async def test_old_checkpoint_assets_are_skipped_before_fetching():
    root = "https://example.org/"
    checkpoint = {
        "version": 1, "start": root, "queue": {root + "photo.jpg": [1, 1, 1]},
        "fetched": [root], "summary": {"pages_checked": 1},
    }
    session = Session({
        root + "robots.txt": Response(status=404),
        root + "photo.jpg": Response(headers={"Content-Type": "image/jpeg"}),
    })
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website(root, checkpoint=checkpoint)
    assert result["status"] == "completed"
    assert result["summary"]["pages_skipped"] == 1
    assert session.requested == []


@pytest.mark.asyncio
async def test_redirect_to_asset_skips_before_target_request():
    root = "https://example.org/"
    session = Session({
        root + "robots.txt": Response(status=404),
        root: Response('<a href="/photo">Photo</a>'),
        root + "photo": Response(status=302, headers={"Location": "/photo.jpg"}),
        root + "photo.jpg": Response(headers={"Content-Type": "image/jpeg"}),
    })
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website(root)
    assert result["status"] == "completed"
    assert result["summary"]["pages_skipped"] == 1
    assert root + "photo.jpg" not in session.requested


@pytest.mark.asyncio
async def test_asset_start_does_not_claim_success_without_html():
    session = Session({
        "https://example.org/robots.txt": Response(status=404),
        "https://example.org/contact.jpg": Response(headers={"Content-Type": "image/jpeg"}),
    })
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website("https://example.org/contact.jpg")
    assert result["status"] == "blocked"
    assert result["pages_checked"] == 0
    assert result["warnings"]


@pytest.mark.asyncio
async def test_robots_timeout_preserves_exception_type():
    session = Session({"https://example.org/robots.txt": TimeoutError()})
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website("https://example.org/")
    assert result["status"] == "blocked"
    assert "robots unavailable: TimeoutError" in result["warnings"][0]
    assert "TimeoutError" in result["checkpoint"]["logs"][0]["reason"]


@pytest.mark.asyncio
async def test_page_timeout_has_a_nonempty_log_reason():
    session = Session({
        "https://example.org/robots.txt": Response(status=404),
        "https://example.org/": TimeoutError(),
    })
    with patch.object(w.aiohttp, "ClientSession", return_value=session):
        result = await w.enrich_website("https://example.org/")
    assert result["checkpoint"]["logs"][0]["reason"] == "TimeoutError"
