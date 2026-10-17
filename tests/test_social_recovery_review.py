"""Cross-review reproductions using real source loops, fake HTTP and temp DBs."""
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from leadworkbench import social_policy
from leadworkbench.db import Store
from leadworkbench.social_worker import process_social_item
from leadworkbench.sources import social_discovery as social, website
from leadworkbench.website_policy import WebsiteAccessPolicy
from test_searxng_gate import queued_search
from test_social_http_policy import BIO, HTML, PROFILE, ROOT, http, queue_website
from test_social_resume_api import api
from test_website import Response


CHALLENGE = ('<title>Just a moment...</title>'
             '<script src="/cdn-cgi/challenge-platform/verify.js"></script>' + HTML +
             '<a href="tel:+12125550123">Call</a>')


def host_gate(policy, host, deadline=0):
    policy.observe(dict(host=host, not_before=deadline, manual_required=True,
                        http_status=403, reason="HTTP 403"))


@pytest.mark.asyncio
async def test_website_challenge_stops_before_links_and_persists_cross_lane_gate(tmp_path, http):
    store = Store(tmp_path)
    company, run = queue_website(store)
    with store.connect() as con:
        con.execute("UPDATE companies SET phone=? WHERE id=?", ("+442079460123", company["id"]))
    http.routes.update({ROOT + "robots.txt": Response(status=404), ROOT: Response(CHALLENGE)})
    assert await process_social_item(store)
    item = store.social_run(run["id"])["items"][0]
    assert item["state"] == "blocked"
    assert item["result"]["reason"] == "challenge"
    assert not item["result"].get("links") and not item["result"].get("fields")
    assert store.social_detail(company["id"])["accounts"] == []
    assert store.company(company["id"])["phone"] == "+442079460123"
    policy = WebsiteAccessPolicy(store)
    assert policy.gates()["brand.example"]["manual_required"]
    before = list(http.sends)
    assert (await website.enrich_website(ROOT, access_policy=policy))["status"] == "blocked"
    assert http.sends == before


@pytest.mark.asyncio
@pytest.mark.parametrize("markup", [
    '<title>Captcha integration guide</title><article>Just a moment /cdn-cgi/challenge</article>',
    '<title>Just a moment</title><pre>&lt;script src="/cdn-cgi/challenge"&gt;</pre>',
    '<title>Contact us</title><script src="/cdn-cgi/challenge-platform/widget.js"></script>',
])
async def test_ordinary_website_content_does_not_trigger_challenge_or_import_phones(tmp_path, http, markup):
    store = Store(tmp_path)
    company, run = queue_website(store)
    phone = store.company(company["id"])["phone"]
    http.routes.update({ROOT + "robots.txt": Response(status=404),
                        ROOT: Response(markup + HTML + '<a href="tel:+12125550123">Call</a>')})
    assert await process_social_item(store)
    assert store.social_run(run["id"])["items"][0]["state"] == "succeeded"
    assert store.social_detail(company["id"])["accounts"][0]["url"] == PROFILE
    assert store.company(company["id"])["phone"] == phone
    assert not WebsiteAccessPolicy(store).gates()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("header_kind,wait", [("seconds", 7200), ("http_date", 10800), ("short", 60)])
async def test_searx_response_deadline_reaches_shared_gate_and_resume(tmp_path, http, status, header_kind, wait):
    store = Store(tmp_path)
    store.set_social_settings("http://127.0.0.1:18888")
    first, second = queued_search(store, "First"), queued_search(store, "Second")
    start = http.clock[0]
    header = format_datetime(datetime.fromtimestamp(start + wait, timezone.utc), usegmt=True) if header_kind == "http_date" else str(wait)
    http.routes["http://127.0.0.1:18888/search"] = Response(status=status, headers={"Retry-After": header})
    assert await process_social_item(store)
    outcome = store.social_run(first["id"])["items"][1]["result"]
    assert outcome["http_status"] == status
    assert outcome["not_before"] >= start + wait
    deadline = start + max(600, wait)
    with store.connect() as con:
        gate = social_policy.gate(con, "searxng")
        assert gate["not_before"] == deadline and gate["failures"] == 1
    http.clock[0] = deadline - 1
    assert not await process_social_item(store)
    assert len(http.sends) == 1
    with pytest.raises(ValueError, match="冷却"):
        store.resume_social_platform("searxng")
    assert store.social_run(second["id"])["summary"]["pending"] > 0
    http.clock[0] = deadline
    store.resume_social_platform("searxng")
    assert store.claim_social_item()["run_id"] == second["id"]


@pytest.mark.asyncio
async def test_unrepresentable_searx_retry_after_requires_manual_configuration_recovery(tmp_path, http):
    store = Store(tmp_path)
    store.set_social_settings("http://127.0.0.1:18888")
    queued_search(store, "Fixture")
    http.routes["http://127.0.0.1:18888/search"] = Response(status=503, headers={"Retry-After": "9" * 200})
    assert await process_social_item(store)
    http.clock[0] += 86400
    with store.connect() as con:
        assert social_policy.public_gate(con, "searxng")["manual_required"]
        assert not social_policy.allowed(con, "searxng")


@pytest.mark.parametrize("platform,hosts", [
    ("instagram", ["instagram.com"]),
    ("facebook", ["facebook.com", "m.facebook.com"]),
    ("x", ["twitter.com", "x.com"]),
    ("youtube", ["youtube.com", "m.youtube.com", "youtu.be"]),
    ("pinterest", ["pinterest.com", "au.pinterest.com"]),
    ("tiktok", ["tiktok.com"]),
    ("bluesky", ["bsky.app"]),
])
def test_resume_clears_only_selected_platform_host_gates_and_keeps_spacing(api, http, platform, hosts):
    client, store = api
    policy = WebsiteAccessPolicy(store)
    unrelated = ["brand.example", "instagram.com.evil.example", "other.example"]
    for host in hosts + unrelated:
        policy.record_request("https://" + host, http.clock[0], 8)
        host_gate(policy, host)
    with store.connect() as con:
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    (platform, "waiting_login", 0, "HTTP 403", 0))
    assert client.post(f"/api/social/platforms/{platform}/resume").status_code == 200
    assert set(policy.gates()) == set(unrelated)
    assert all(policy.timing("https://" + host) == (http.clock[0], 8) for host in hosts)


def test_platform_resume_host_deadline_rejection_is_atomic_and_other_platforms_survive(api, http):
    client, store = api
    policy = WebsiteAccessPolicy(store)
    host_gate(policy, "facebook.com")
    host_gate(policy, "m.facebook.com", http.clock[0] + 7200)
    host_gate(policy, "instagram.com", http.clock[0] + 14400)
    before = policy.gates()
    with store.connect() as con:
        con.execute("INSERT INTO social_platform_gates VALUES('facebook','waiting_login',0,'HTTP 403',0)")
    assert client.post("/api/social/platforms/facebook/resume").status_code == 409
    assert policy.gates() == before
    with store.connect() as con:
        assert social_policy.gate(con, "facebook")["state"] == "waiting_login"
    http.clock[0] += 7200
    assert client.post("/api/social/platforms/facebook/resume").status_code == 200
    assert policy.gates() == {"instagram.com": before["instagram.com"]}


@pytest.mark.asyncio
async def test_http_profile_403_can_really_read_after_explicit_platform_resume(api, http):
    client, store = api
    company = store.manual_company("Fixture", ROOT)
    store.add_social(company["id"], PROFILE, "Public account")
    run = store.create_social_run([company["id"]], read_only=True)
    http.routes.update({"https://www.instagram.com/robots.txt": Response(status=404),
                        PROFILE: Response(status=403)})
    assert await process_social_item(store)
    assert store.social_run(run["id"])["status"] == "partial"
    assert client.post("/api/social/platforms/instagram/resume").status_code == 200
    retry = store.retry_social_run(run["id"])
    http.routes[PROFILE] = Response(BIO)
    assert await process_social_item(store)
    assert store.social_run(retry["id"])["items"][0]["state"] == "succeeded"
    assert store.social_pages(company["id"])[0]["description"] == "Public bio hello@brand.example"


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["challenge", "403", "429"])
async def test_manual_website_retry_recovers_only_observed_redirect_host_after_deadline(api, http, restriction):
    client, store = api
    company, run = queue_website(store)
    destination = "https://www.brand.example/landing"
    response = Response(CHALLENGE) if restriction == "challenge" else Response(
        status=int(restriction), headers={"Retry-After": "7200"})
    http.routes.update({ROOT + "robots.txt": Response(status=404),
                        ROOT: Response(status=302, headers={"Location": destination}),
                        "https://www.brand.example/robots.txt": Response(status=404), destination: response})
    assert await process_social_item(store)
    # Complete the search branch without contacting even a local search service.
    assert await process_social_item(store)
    before = store.social_run(run["id"])
    assert before["status"] == "partial"
    assert before["items"][0]["result"]["website_access"]["host"] == "brand.example"
    policy = WebsiteAccessPolicy(store)
    host_gate(policy, "unrelated.example")
    host_gate(policy, "instagram.com")
    path = f"/api/social/runs/{run['id']}/retry"
    if restriction == "429":
        deadline = policy.gates()["brand.example"]["not_before"]
        assert client.post(path).status_code == 400
        assert len(store.social_runs()) == 1
        assert policy.gates()["brand.example"]["not_before"] == deadline
        http.clock[0] = deadline
    response = client.post(path)
    assert response.status_code == 200, response.text
    retry = response.json()
    assert retry["id"] != run["id"]
    assert set(policy.gates()) == {"unrelated.example", "instagram.com"}
    http.routes[destination] = Response('<p>No social links here</p>')
    assert await process_social_item(store)
    assert store.social_run(retry["id"])["items"][0]["state"] == "succeeded"
    assert store.social_run(run["id"])["items"] == before["items"]
    assert store.social_detail(company["id"])["accounts"] == []


def test_invalid_manual_retry_does_not_clear_host_gates(api):
    client, store = api
    company, run = queue_website(store)
    policy = WebsiteAccessPolicy(store)
    host_gate(policy, "brand.example")
    before = policy.gates()
    assert client.post(f"/api/social/runs/{run['id']}/retry").status_code == 400
    item = store.claim_social_item()
    store.finish_social_item(item["id"], dict(status="blocked", reason="access_denied",
                                            website_access=before["brand.example"]))
    store.cancel_social_run(run["id"])
    with store.connect() as con:
        con.execute("UPDATE companies SET status='do_not_contact' WHERE id=?", (company["id"],))
    assert client.post(f"/api/social/runs/{run['id']}/retry").status_code == 400
    assert policy.gates() == before and len(store.social_runs()) == 1
