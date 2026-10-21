"""AWS trial D2: server mode fails closed in degraded mode (design §2.6 step 2, §10.1)."""
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

from leadworkbench import auth
from leadworkbench.api import create_app
from leadworkbench.config import load

CFG = {"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"}


@pytest.fixture
def degraded(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "storage_unavailable")
    app = create_app(tmp_path, config=load(CFG))
    return TestClient(app, base_url="https://lw.test", raise_server_exceptions=False)


def test_diagnostics_bundle_refused_without_path(degraded, tmp_path):
    response = degraded.get("/api/diagnostics/bundle")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "storage_unavailable"
    assert str(tmp_path) not in response.text
    assert response.json()["detail"] == "服务器数据库初始化失败，服务暂不可用，请联系管理员。"


def test_session_reports_unavailable(degraded):
    response = degraded.get("/api/session")
    assert response.status_code == 200
    assert response.headers["X-Lead-Unavailable"] == "storage_unavailable"
    body = response.json()
    assert body["authenticated"] is False and body["unavailable"]["code"] == "storage_unavailable"


def test_login_and_cookie_requests_are_503(degraded):
    assert degraded.post("/api/auth/login", json={"username": "alice", "password": "x"}).status_code == 503
    degraded.cookies.set("__Host-lw_session", "anything", domain="lw.test")
    assert degraded.get("/api/overview").status_code == 503
    assert degraded.get("/healthz").status_code == 503


def test_session_read_error_is_503_not_401(tmp_path, monkeypatch):
    app = create_app(tmp_path, config=load(CFG))
    password = secrets.token_urlsafe(18)
    auth.create_user(app.state.store, "alice", password)
    client = TestClient(app, base_url="https://lw.test", raise_server_exceptions=False)
    assert client.post("/api/auth/login", json={"username": "alice", "password": password}).status_code == 200
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(auth, "resolve_session", fail)
    response = client.get("/api/session")
    assert response.status_code == 503 and response.json()["code"] == "db_busy"
