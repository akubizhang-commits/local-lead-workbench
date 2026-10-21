"""AWS trial D4: social login sessions and site search are off in server mode (design §4, §3.3)."""
import importlib.util
import secrets
import subprocess

import pytest
from fastapi.testclient import TestClient

from leadworkbench import auth, maps_guard
from leadworkbench.api import SOCIAL_LOGIN_UNSUPPORTED, create_app
from leadworkbench.config import load
from leadworkbench.db import Store
from leadworkbench.sources.social_browser import blocked_ip_literal

SERVER = {"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"}


@pytest.fixture
def server(tmp_path):
    app = create_app(tmp_path, config=load(SERVER))
    password = secrets.token_urlsafe(18)
    auth.create_user(app.state.store, "alice", password)
    client = TestClient(app, base_url="https://lw.test", raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.post(
        "/api/auth/login", json={"username": "alice", "password": password}).json()["token"]
    return app, client


@pytest.fixture
def local(tmp_path):
    app = create_app(tmp_path / "local", allowed_hosts={"testserver"})
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    return app, client


def test_open_login_window_is_refused_without_a_process(server, monkeypatch):
    _, client = server
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    response = client.post("/api/social/browser/instagram/open")
    assert response.status_code == 409
    assert response.json()["code"] == "social_login_unsupported"
    assert response.json()["detail"] == SOCIAL_LOGIN_UNSUPPORTED
    assert started == []
    closed = client.post("/api/social/browser/instagram/close")
    assert closed.status_code == 200 and closed.json()["status"] == "closed"


def test_connections_report_login_support(server, local):
    assert server[1].get("/api/social/connections").json()["login_supported"] is False
    assert local[1].get("/api/social/connections").json()["login_supported"] is True


def test_platform_search_runs_refused(server):
    app, client = server
    company = app.state.store.manual_company("Vocal Apparel", "https://example.org")["id"]
    response = client.post("/api/social/runs", json={"company_ids": [company], "platform_search": True})
    assert response.status_code == 409 and response.json()["code"] == "social_login_unsupported"
    assert client.post("/api/social/runs", json={"company_ids": [company]}).status_code == 200


def test_local_platform_search_still_allowed(local):
    app, client = local
    company = app.state.store.manual_company("Vocal Apparel", "https://example.org")["id"]
    assert client.post("/api/social/runs", json={"company_ids": [company], "platform_search": True}).status_code == 200


@pytest.mark.skipif(importlib.util.find_spec("instaloader") is not None, reason="instaloader installed")
def test_instaloader_missing_is_409_in_server_mode(server, local):
    response = server[1].put("/api/social/connections", json={"instagram_reader": "instaloader"})
    assert response.status_code == 409 and response.json()["code"] == "dependency_missing"
    assert "Instaloader" in response.json()["detail"]
    assert local[1].put("/api/social/connections", json={"instagram_reader": "instaloader"}).status_code == 200


@pytest.mark.asyncio
async def test_worker_skips_platform_search_in_server_mode(tmp_path):
    from leadworkbench.social_worker import process_social_item
    store = Store(tmp_path)
    company = store.manual_company("Vocal Apparel", "https://example.org")["id"]
    run = store.create_social_run([company], platform_search=True)
    called = []
    async def search(*args):
        called.append(args)
        return {"status": "succeeded", "links": []}
    for _ in run["items"]:
        assert await process_social_item(store, platform_search=search, server=True)
    assert called == []
    detail = store.social_run(run["id"])
    results = [item["result"] for item in detail["items"]]
    assert all(r["status"] == "skipped" and r["reason"] == "server_mode" for r in results), results
    assert results[0]["message"] == "服务器部署版不支持站内搜索（需要登录社媒账号），已跳过"


@pytest.mark.parametrize("url,blocked", [
    ("http://169.254.169.254/latest/meta-data/", True), ("http://10.0.0.1/", True), ("http://[::1]/", True),
    ("http://127.0.0.1:8000/api/session", True), ("https://192.168.1.5/x.png", True),
    ("https://8.8.8.8/", False), ("https://www.instagram.com/x/", False), ("https://cdn.example.com/a.js", False),
])
def test_blocked_ip_literal(url, blocked):
    assert blocked_ip_literal(url) is blocked


def test_maps_orphan_wording():
    assert "docker compose restart worker" in maps_guard.maps_orphan_problem(True)["message"]
    assert maps_guard.maps_orphan_problem(False) == maps_guard.MAPS_ORPHAN
