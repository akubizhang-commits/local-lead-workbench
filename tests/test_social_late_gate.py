"""Late browser failures keep observed data and report the platform gate reason."""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest

from leadworkbench import social_policy
from leadworkbench.sources import social_browser


PROFILE_URL = "https://www.instagram.com/fixture/"
KEPT_URL = "https://www.instagram.com/p/KEPT/"
GOOD_URL = "https://www.instagram.com/p/GOOD/"
LATE_URL = "https://www.instagram.com/p/LATE/"
NEVER_URL = "https://www.instagram.com/p/NEVER/"
PROFILE_HTML = '<a href="/p/GOOD/">First</a><a href="/p/LATE/">Later</a><a href="/p/NEVER/">Stop before this</a>'
MORE_HTML = PROFILE_HTML + "<p>Another loaded post</p>"
FIELD = {"key": "email", "value": "hello@fixture.example", "evidence": "Fixture bio"}
KEPT_POST = {"url": KEPT_URL, "text": "Initial observed post", "published_at": ""}
GOOD_POST = {"url": GOOD_URL, "text": "Additional observed post", "published_at": ""}


class FakeBrowser:
    """Page/context/driver boundary; no Playwright import or external requests."""

    def __init__(self, phase, outcome):
        self.phase = phase
        self.outcome = outcome
        self.url = PROFILE_URL
        self.html = PROFILE_HTML
        self.mouse = SimpleNamespace(wheel=self.wheel)
        self.scrolls = 0
        self.navigated = []
        self.closed = False
        self.stopped = False

    async def new_page(self):
        return self

    async def route(self, *args):
        pass

    async def goto(self, url, **kwargs):
        self.navigated.append(url)
        self.url = url
        if url == LATE_URL:
            if isinstance(self.outcome, Exception):
                raise self.outcome
            if isinstance(self.outcome, int):
                return SimpleNamespace(status=self.outcome)
            self.html = "late gate"
        elif url == GOOD_URL:
            self.html = "good post"
        elif url == NEVER_URL:
            raise AssertionError("browser continued navigating after a platform gate")
        return SimpleNamespace(status=200)

    async def wait_for_timeout(self, delay):
        pass

    async def content(self):
        return self.html

    async def wheel(self, *args):
        self.scrolls += 1
        if self.phase == "scroll":
            if self.scrolls == 1:
                self.html = MORE_HTML
            elif isinstance(self.outcome, Exception):
                raise self.outcome
            else:
                self.html = "late gate"

    async def close(self):
        self.closed = True

    async def stop(self):
        self.stopped = True

    def parse(self, html, url, max_posts=3):
        if html == "late gate" or self.phase == "initial":
            return {
                "status": self.outcome, "provider": "browser", "source_url": url,
                "message": "Fixture platform response", "posts": [],
                "description": "Another observed post" if self.outcome == "preview_read" else "",
            }
        if html == "good post":
            return {"status": "preview_read", "description": GOOD_POST["text"], "posts": []}
        assert html in {PROFILE_HTML, MORE_HTML}
        return {
            "status": "preview_read", "provider": "browser", "source_url": PROFILE_URL,
            "title": "Fixture profile", "description": "Previously observed bio",
            "fields": [deepcopy(FIELD)], "message": "Profile collected",
            "posts": [deepcopy(GOOD_POST if html == MORE_HTML else KEPT_POST)],
        }


@pytest.fixture
def browser(monkeypatch):
    def install(phase, outcome):
        fake = FakeBrowser(phase, outcome)

        async def context(*args, **kwargs):
            return fake, fake

        monkeypatch.setattr(social_browser, "browser_context", context)
        monkeypatch.setattr(social_browser, "parse_browser_document", fake.parse)
        return fake

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,outcome,reason,retry,gate_state", [
    ("scroll", "auth_required", "login_required", True, "waiting_login"),
    ("scroll", "challenge", "challenge", True, "waiting_login"),
    ("post", "auth_required", "login_required", True, "waiting_login"),
    ("post", "challenge", "challenge", True, "waiting_login"),
    ("post", 401, "login_required", True, "waiting_login"),
    ("post", 403, "login_required", True, "waiting_login"),
    ("post", 429, "rate_limited", True, "waiting_recovery"),
    ("post", 500, "network_error", True, "waiting_recovery"),
    ("post", 503, "network_error", True, "waiting_recovery"),
    ("post", 404, "http_error", False, "ready"),
    ("post", TimeoutError("fixture timeout"), "network_error", True, "waiting_recovery"),
    ("post", OSError("fixture connection failure"), "network_error", True, "waiting_recovery"),
    ("scroll", TimeoutError("fixture scroll timeout"), "network_error", True, "waiting_recovery"),
])
async def test_late_failure_retains_profile_and_posts_and_reports_gate(
        tmp_path, monkeypatch, browser, phase, outcome, reason, retry, gate_state):
    fake = browser(phase, outcome)
    result = await social_browser._read_browser(PROFILE_URL, tmp_path, {"max_posts": 3})

    assert result["status"] == "preview_read"
    assert result["partial"] is True
    assert result.get("reason") == reason
    assert result["title"] == "Fixture profile"
    assert result["description"] == "Previously observed bio"
    assert result["fields"] == [FIELD]
    assert result["source_url"] == PROFILE_URL
    assert result["posts"] == [KEPT_POST, GOOD_POST]
    assert fake.navigated == (
        [PROFILE_URL] if phase == "scroll" else [PROFILE_URL, GOOD_URL, LATE_URL])
    assert fake.closed and fake.stopped
    with social_browser.profile_lock(tmp_path, "instagram"):
        pass

    # Exercise the real policy against the partial source result, including
    # retryable HTTP/server failures versus a terminal missing post.
    monkeypatch.setattr(social_policy.time, "time", lambda: 1000)
    with sqlite3.connect(":memory:") as con:
        con.row_factory = sqlite3.Row
        con.executescript(social_policy.DDL)
        assert social_policy.observe(con, {"stage": "read", "target": PROFILE_URL}, result) is retry
        gate = social_policy.gate(con, "instagram")
        assert gate["state"] == gate_state
        if retry:
            assert not social_policy.allowed(con, "instagram")
            assert gate["reason"]
        if gate_state == "waiting_recovery":
            assert gate["not_before"] == 1600


@pytest.mark.asyncio
async def test_successful_browser_read_does_not_invent_gate_reason(tmp_path, browser):
    fake = browser("post", "preview_read")
    result = await social_browser._read_browser(PROFILE_URL, tmp_path, {"max_posts": 3})
    assert result["status"] == "preview_read"
    assert not result.get("partial")
    assert not result.get("reason")
    assert result["fields"] == [FIELD]
    assert result["posts"] == [
        KEPT_POST, GOOD_POST,
        {"url": LATE_URL, "text": "Another observed post", "published_at": ""},
    ]
    assert fake.navigated == [PROFILE_URL, GOOD_URL, LATE_URL]
    assert fake.closed and fake.stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["auth_required", "challenge"])
async def test_initial_gate_remains_a_gate_without_fabricated_preview(tmp_path, browser, status):
    fake = browser("initial", status)
    result = await social_browser._read_browser(PROFILE_URL, tmp_path, {"max_posts": 3})
    assert result["status"] == status
    assert not result.get("partial")
    assert result["posts"] == []
    assert fake.navigated == [PROFILE_URL]
    assert fake.scrolls == 0
    assert fake.closed and fake.stopped
