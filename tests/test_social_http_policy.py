"""Real fetch loops and send hooks; fake HTTP, virtual time and temporary DBs."""
import asyncio
from types import SimpleNamespace

import pytest

from leadworkbench.db import Store
from leadworkbench.social_worker import process_social_item
from leadworkbench.sources import social_combination, social_discovery as social, website
from leadworkbench.website_policy import WebsiteAccessPolicy
from test_website import Response


ROOT = "https://brand.example/"
PROFILE = "https://www.instagram.com/fixture/"
HTML = '<p>Follow our brand <a href="https://www.instagram.com/fixture/">Instagram</a></p><a href="/contact">Contact</a>'
BIO = '<title>Fixture</title><meta property="og:description" content="Public bio hello@brand.example">'


@pytest.fixture
def http(monkeypatch):
    clock = [1000.]
    sends = []
    routes = {}
    sessions = []
    hooks = SimpleNamespace(before_send=None)
    real_sleep = asyncio.sleep

    async def sleep(delay):
        clock[0] += delay
        await real_sleep(0)

    async def send(request, connection):
        sends.append((str(request.url), clock[0]))

    class WireResponse:
        def __init__(self, response, request_class, url):
            self.response, self.request_class, self.url = response, request_class, url

        async def __aenter__(self):
            request = object.__new__(self.request_class)
            request.url = self.url
            if hooks.before_send:
                await hooks.before_send(request)
            await request.send(None)
            return self.response

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, **options):
            self.request_class = options.get("request_class", website.aiohttp.ClientRequest)
            self.closed = False
            sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        def get(self, url, **options):
            assert url in routes, f"Unexpected HTTP request: {url}"
            return WireResponse(routes[url], self.request_class, url)

    monkeypatch.setattr(website.time, "time", lambda: clock[0])
    monkeypatch.setattr(website.asyncio, "sleep", sleep)
    monkeypatch.setattr(website.aiohttp.ClientRequest, "send", send)
    monkeypatch.setattr(website.aiohttp, "ClientSession", Session)
    return SimpleNamespace(clock=clock, sends=sends, routes=routes, sessions=sessions, hooks=hooks)


def queue_website(store):
    company = store.manual_company("Fixture", ROOT)
    run = store.create_social_run([company["id"]])
    return company, run


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_website_lane_gate_stops_social_worker_before_even_robots(tmp_path, http, status):
    store = Store(tmp_path)
    queue_website(store)
    policy = WebsiteAccessPolicy(store)
    http.routes.update({ROOT + "robots.txt": Response(status=404),
                        ROOT: Response(status=status, headers={"Retry-After": "7200"})})
    result = await website.enrich_website(ROOT, access_policy=policy)
    assert result["website_access"]["http_status"] == status
    before = list(http.sends)
    assert await process_social_item(store)
    assert http.sends == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_social_worker_gate_stops_website_lane(tmp_path, http, status):
    store = Store(tmp_path)
    queue_website(store)
    http.routes.update({ROOT + "robots.txt": Response(status=404),
                        ROOT: Response(status=status, headers={"Retry-After": "7200"})})
    assert await process_social_item(store)
    policy = WebsiteAccessPolicy(store)
    gate = policy.gates()["brand.example"]
    assert gate["http_status"] == status
    if status == 429:
        assert gate["not_before"] >= http.sends[-1][1] + 7200
    before = list(http.sends)
    result = await website.enrich_website(ROOT, access_policy=policy)
    assert result["status"] == "blocked" and http.sends == before


@pytest.mark.asyncio
async def test_social_website_keeps_evidence_and_only_fetches_one_page(tmp_path, http):
    store = Store(tmp_path)
    company, _ = queue_website(store)
    http.routes.update({ROOT + "robots.txt": Response(status=404), ROOT: Response(HTML)})
    assert await process_social_item(store)
    assert [url for url, _ in http.sends] == [ROOT + "robots.txt", ROOT]
    assert http.sends[1][1] - http.sends[0][1] >= 2
    accounts = store.social_detail(company["id"])["accounts"]
    assert accounts[0]["url"] == PROFILE
    assert "Follow our brand" in accounts[0]["evidence"][0]["evidence"]
    assert accounts[0]["evidence"][0]["source_url"] == ROOT


@pytest.mark.asyncio
async def test_parallel_website_and_social_lanes_share_actual_send_spacing(tmp_path, http):
    store = Store(tmp_path)
    queue_website(store)
    http.routes.update({ROOT + "robots.txt": Response("User-agent: *\nCrawl-delay: 4"),
                        ROOT: Response(HTML), ROOT + "contact": Response("Contact")})
    website_result, social_result = await asyncio.gather(
        website.enrich_website(ROOT, access_policy=WebsiteAccessPolicy(store)),
        process_social_item(store),
    )
    assert website_result["pages_checked"] >= 1 and social_result
    assert len(http.sends) >= 4
    assert all(b[1] - a[1] >= 2 for a, b in zip(http.sends, http.sends[1:]))
    assert WebsiteAccessPolicy(store).timing(ROOT)[1] >= 4
    assert all(session.closed for session in http.sessions)


@pytest.mark.asyncio
async def test_platform_http_spaces_robots_redirects_page_and_internal_retry(tmp_path, http):
    policy = WebsiteAccessPolicy(Store(tmp_path))
    origin = "https://www.instagram.com/"
    http.routes.update({
        origin + "robots.txt": Response(status=302, headers={"Location": "/robots-policy"}),
        origin + "robots-policy": Response("User-agent: *\nCrawl-delay: 8"),
        PROFILE: Response(BIO),
    })
    result = await social_combination.read_combined(PROFILE, tmp_path, {"reader": "http"}, access_policy=policy)
    assert result["status"] == "preview_read"
    assert http.sends[1][1] - http.sends[0][1] >= 2
    assert http.sends[2][1] - http.sends[1][1] >= 8
    # aiohttp may resend the same ClientRequest after a disconnected keepalive.
    request = object.__new__(http.sessions[-1].request_class)
    request.url = PROFILE
    await request.send(None)
    assert http.sends[-1][1] - http.sends[-2][1] >= 8
    assert policy.timing(PROFILE) == (http.sends[-1][1], 8)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_social_robots_failure_is_shared_and_not_followed_by_page(tmp_path, http, status):
    store = Store(tmp_path)
    queue_website(store)
    http.routes[ROOT + "robots.txt"] = Response(status=status, headers={"Retry-After": "7200"})
    assert await process_social_item(store)
    assert [url for url, _ in http.sends] == [ROOT + "robots.txt"]
    gate = WebsiteAccessPolicy(store).gates()["brand.example"]
    assert gate["http_status"] == status


@pytest.mark.asyncio
async def test_social_redirect_cannot_bypass_gate_observed_after_previous_response(tmp_path, http):
    policy = WebsiteAccessPolicy(Store(tmp_path))
    http.routes.update({ROOT + "robots.txt": Response(status=404),
                        ROOT: Response(status=302, headers={"Location": "/landing"}),
                        ROOT + "landing": Response(HTML)})
    original = website.aiohttp.ClientRequest.send
    async def send(request, connection):
        await original(request, connection)
        if str(request.url) == ROOT:
            policy.observe(dict(host="brand.example", manual_required=True, not_before=0,
                                http_status=403, reason="Concurrent HTTP 403"))
    # Patch the base hook to simulate the other source observing a restriction.
    website.aiohttp.ClientRequest.send = send
    try:
        result = await social.read_public(ROOT, "website", access_policy=policy)
    finally:
        website.aiohttp.ClientRequest.send = original
    assert result["status"] == "blocked"
    assert [url for url, _ in http.sends] == [ROOT + "robots.txt", ROOT]


@pytest.mark.asyncio
async def test_delayed_website_connection_rechecks_spacing_after_social_lane_sent(tmp_path, http):
    store = Store(tmp_path)
    queue_website(store)
    http.routes.update({ROOT + "robots.txt": Response(status=404), ROOT: Response(HTML)})
    connected, release = asyncio.Event(), asyncio.Event()
    async def before_send(request):
        if type(request).__name__ == "BudgetedRequest" and not connected.is_set():
            connected.set()
            await release.wait()
    http.hooks.before_send = before_send
    job = asyncio.create_task(website.enrich_website(ROOT, max_pages=1, access_policy=WebsiteAccessPolicy(store)))
    try:
        await asyncio.wait_for(connected.wait(), 1)
        assert await process_social_item(store)
        release.set()
        await job
    finally:
        release.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    assert all(b[1] - a[1] >= 2 for a, b in zip(http.sends, http.sends[1:])), http.sends


@pytest.mark.asyncio
async def test_invalid_robots_transport_blocks_other_source_before_retry(tmp_path, http):
    policy = WebsiteAccessPolicy(Store(tmp_path))
    http.routes[ROOT + "robots.txt"] = Response("opaque", headers={"Content-Encoding": "gzip"})
    result = await social.read_public(ROOT, "website", access_policy=policy)
    assert result["status"] != "succeeded"
    assert policy.gates()["brand.example"]["manual_required"]
    before = list(http.sends)
    result = await website.enrich_website(ROOT, access_policy=policy)
    assert result["status"] == "blocked" and http.sends == before
