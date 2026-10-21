"""AWS trial D2: session-gated API boundary (design §2.4–2.7, §10.1 test_auth_api)."""
import asyncio
from datetime import timedelta
import json
import re
import secrets
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from leadworkbench import api as api_module
from leadworkbench import auth
from leadworkbench.api import AUTH_OPTIONAL, AUTH_PUBLIC, create_app
from leadworkbench.config import load

SERVER = {"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"}
BASE = "https://lw.test"


@pytest.fixture
def password():
    return secrets.token_urlsafe(18)


def make(tmp_path, env=None, base=BASE):
    app = create_app(tmp_path, config=load({**SERVER, **(env or {})}))
    return app, TestClient(app, base_url=base, raise_server_exceptions=False)


@pytest.fixture
def server(tmp_path, password):
    app, client = make(tmp_path)
    auth.create_user(app.state.store, "alice", password)
    yield app, client
    client.close()


def login(client, password, username="alice"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def cookie_header(response):
    return response.headers.get("set-cookie", "")


def logs(tmp_path):
    path = tmp_path / "logs" / "api.log"
    return path.read_text() if path.exists() else ""


def events(tmp_path, name):
    return [json.loads(line) for line in logs(tmp_path).splitlines() if f'"event": "{name}"' in line]


def concrete(path):
    return re.sub(r"\{[^}]+\}", "x", path)


def test_every_api_route_requires_login(server):
    app, client = server
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in AUTH_PUBLIC or path in AUTH_OPTIONAL:
            continue
        for method in route.methods - {"HEAD"}:
            response = client.request(method, concrete(path))
            assert response.status_code == 401, (method, path, response.status_code)
            assert response.json()["code"] == "unauthorized"
            assert response.json()["detail"] == "请先登录"
            checked += 1
    assert checked > 70  # includes the diagnostics bundle and export downloads
    assert client.get("/api/does-not-exist").status_code == 401


def test_session_endpoint_and_healthz(server):
    _, client = server
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/session").json() == {"authenticated": False, "mode": "server", "auth": True}


def test_login_cookie_attributes_and_session(server, password):
    _, client = server
    response = login(client, password, "ALICE")
    header = cookie_header(response)
    assert header.startswith("__Host-lw_session=")
    for part in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert part.lower() in header.lower()
    assert "domain" not in header.lower() and "max-age" not in header.lower()
    body = response.json()
    assert body["user"] == {"username": "alice"} and len(body["token"]) > 30
    session = client.get("/api/session").json()
    assert session["authenticated"] and session["token"] == body["token"]
    assert client.get("/api/overview").status_code == 200  # GET needs no CSRF token
    me = client.get("/api/auth/me").json()
    assert me["username"] == "alice" and me["last_login_at"]


def test_csrf_required_for_writes(server, password):
    _, client = server
    token = login(client, password).json()["token"]
    missing = client.post("/api/backups")
    assert missing.status_code == 403 and missing.json()["code"] == "csrf_failed"
    assert missing.json()["detail"] == "页面已过期，请刷新后重试"
    assert client.post("/api/backups", headers={"X-Local-Token": "wrong"}).status_code == 403
    assert client.post("/api/backups", headers={"X-Local-Token": token}).status_code == 200


def test_cross_origin_refused(server, password):
    _, client = server
    token = login(client, password).json()["token"]
    headers = {"X-Local-Token": token}
    assert client.post("/api/backups", headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/backups", headers={**headers, "Origin": BASE}).status_code == 200
    assert client.post("/api/backups", headers={**headers, "Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_login_requires_json(server, password):
    _, client = server
    response = client.post("/api/auth/login", data={"username": "alice", "password": password})
    assert response.status_code == 415 and response.json()["code"] == "unsupported_media_type"


def test_wrong_password_and_validation(server, password):
    _, client = server
    bad = client.post("/api/auth/login", json={"username": "alice", "password": password + "x"})
    assert bad.status_code == 401 and bad.json() == {**bad.json(), "code": "invalid_credentials",
                                                      "detail": "用户名或密码错误"}
    assert "set-cookie" not in bad.headers
    assert client.post("/api/auth/login", json={"username": "", "password": "x"}).status_code == 422
    assert client.post("/api/auth/login", json={"username": "a", "password": "x", "extra": 1}).status_code == 422


def test_overlong_username_is_422_and_not_counted(server, password, monkeypatch):
    app, client = server
    calls = []
    real = app.state.login_limiter.reserve
    monkeypatch.setattr(app.state.login_limiter, "reserve", lambda *a: calls.append(a) or real(*a))
    response = client.post("/api/auth/login", json={"username": "a" * 65, "password": password})
    assert response.status_code == 422 and response.json()["code"] == "validation"
    assert client.post("/api/auth/login", json={"username": "alice", "password": "p" * 129}).status_code == 422
    assert calls == []


def test_rate_limit_429(server, password, tmp_path):
    _, client = server
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    limited = client.post("/api/auth/login", json={"username": "alice", "password": password})
    assert limited.status_code == 429 and limited.json()["code"] == "login_rate_limited"
    assert int(limited.headers["Retry-After"]) > 800
    assert limited.json()["detail"] == "登录尝试过多，请 15 分钟后再试"
    failed = events(tmp_path, "auth_login_failed")
    assert failed and all(len(e["ip_hash8"]) == 8 and e["user_known"] is True for e in failed)
    locked = events(tmp_path, "auth_login_locked")
    assert [e["scope"] for e in locked] == ["account"] and locked[0]["ip_hash8"]
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
    assert unknown.status_code == 401
    assert events(tmp_path, "auth_login_failed")[-1]["user_known"] is False


def test_logout_branches(server, password):
    app, client = server
    token = login(client, password).json()["token"]
    old = client.cookies.get("__Host-lw_session")
    # Valid session + wrong CSRF: refused, still signed in.
    assert client.post("/api/auth/logout", headers={"X-Local-Token": "bad"}).status_code == 403
    assert client.get("/api/overview").status_code == 200
    # Valid session + correct CSRF: 204, cookie cleared, old cookie dead.
    done = client.post("/api/auth/logout", headers={"X-Local-Token": token})
    assert done.status_code == 204
    header = cookie_header(done)
    assert header.startswith("__Host-lw_session=")
    for part in ("Secure", "HttpOnly", "Path=/"):
        assert part.lower() in header.lower()
    assert "domain" not in header.lower()
    client.cookies.clear()
    stale = TestClient(app, base_url=BASE, cookies={"__Host-lw_session": old})
    assert stale.get("/api/overview").status_code == 401
    # Deleted session cookie without CSRF: 204.
    assert stale.post("/api/auth/logout").status_code == 204
    # No cookie at all: 204 and a clearing Set-Cookie.
    bare = client.post("/api/auth/logout")
    assert bare.status_code == 204 and cookie_header(bare).startswith("__Host-lw_session=")


def test_login_rotates_existing_session(server, password):
    app, client = server
    login(client, password)
    first = client.cookies.get("__Host-lw_session")
    login(client, password)
    second = client.cookies.get("__Host-lw_session")
    assert first != second
    with app.state.store.connect() as con:
        hashes = {r[0] for r in con.execute("SELECT id_hash FROM auth_sessions")}
    assert hashes == {auth.sha256_hex(second)}


def test_touch_failure_keeps_request_working(server, password, monkeypatch):
    app, client = server
    login(client, password)
    with app.state.store.connect() as con:
        con.execute("UPDATE auth_sessions SET last_seen_at=?",
                    (auth.iso(auth.utcnow() - timedelta(minutes=30)),))
    real = sqlite3.connect
    def broken(path, *args, **kwargs):
        if kwargs.get("timeout") == 0.2:
            raise sqlite3.OperationalError("database is locked")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(auth.sqlite3, "connect", broken)
    assert client.get("/api/overview").status_code == 200


@pytest.mark.parametrize("message,status,code", [("database is locked", 503, "db_busy"),
                                                 ("database or disk is full", 507, "disk_full"),
                                                 ("something odd", 503, "db_busy")])
def test_session_read_failure_is_not_401(server, password, monkeypatch, message, status, code):
    _, client = server
    login(client, password)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError(message)
    monkeypatch.setattr(auth, "resolve_session", fail)
    response = client.get("/api/overview")
    assert (response.status_code, response.json()["code"]) == (status, code)
    if code == "db_busy":
        assert response.headers["Retry-After"] == "2"


def test_readonly_detail_has_no_path(server, password, monkeypatch, tmp_path):
    _, client = server
    login(client, password)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr(auth, "resolve_session", fail)
    response = client.get("/api/overview")
    assert response.status_code == 503 and response.json()["code"] == "readonly_storage"
    assert str(tmp_path) not in response.text
    assert response.json()["detail"] == "服务器数据目录不可写，服务暂不可用，请联系管理员。"


def test_resolve_failures_are_logged_once_a_minute(server, password, monkeypatch, tmp_path):
    _, client = server
    login(client, password)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(auth, "resolve_session", fail)
    for _ in range(5):
        assert client.get("/api/overview").status_code == 503
    assert len(events(tmp_path, "auth_resolve_failed")) == 1


def test_expired_session_is_401_and_row_kept(server, password):
    app, client = server
    login(client, password)
    with app.state.store.connect() as con:
        con.execute("UPDATE auth_sessions SET expires_at=?", (auth.iso(auth.utcnow() - timedelta(seconds=1)),))
    assert client.get("/api/overview").status_code == 401
    with app.state.store.connect() as con:
        assert con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1


def test_no_cookie_never_enters_the_gate(server, monkeypatch):
    app, client = server
    entered = []
    real = app.state.gate.shared
    monkeypatch.setattr(app.state.gate, "shared", lambda: entered.append(1) or real())
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204
    assert entered == []


def test_probe_does_not_touch(server, password, monkeypatch):
    app, client = server
    token = login(client, password).json()["token"]
    with app.state.store.connect() as con:
        con.execute("UPDATE auth_sessions SET last_seen_at=?",
                    (auth.iso(auth.utcnow() - timedelta(minutes=30)),))
    touched = []
    monkeypatch.setattr(auth, "_touch", lambda *a: touched.append(a))
    response = client.post("/api/social/probe", headers={"X-Local-Token": token})
    assert response.status_code == 200
    assert touched == []
    assert client.get("/api/overview").status_code == 200
    assert len(touched) == 1


def test_host_messages(tmp_path):
    _, client = make(tmp_path, base="https://other.test")
    assert client.get("/healthz").json()["detail"] == "不允许的访问地址"
    local = TestClient(create_app(tmp_path / "local", allowed_hosts={"testserver"}), base_url="http://other.test")
    assert local.get("/api/session").json()["detail"] == "仅允许本机访问"


def test_insecure_http_cookie(tmp_path, password):
    app, client = make(tmp_path, {"LEAD_PUBLIC_ORIGIN": "http://lw.test", "LEAD_ALLOW_INSECURE_HTTP": "1"},
                       base="http://lw.test")
    auth.create_user(app.state.store, "alice", password)
    header = cookie_header(login(client, password))
    assert header.startswith("lw_session=") and "secure" not in header.lower()
    assert client.get("/api/overview").status_code == 200


def test_secrets_never_in_responses_or_logs(server, password, tmp_path):
    app, client = server
    client.post("/api/auth/login", json={"username": "alice", "password": password + "x"})
    response = login(client, password)
    token = response.json()["token"]
    cookie = client.cookies.get("__Host-lw_session")
    bodies = [client.post("/api/backups", headers={"X-Local-Token": "bad"}).text,
              client.get("/api/does-not-exist").text,
              client.post("/api/tasks", headers={"X-Local-Token": token}, json={"bad": 1}).text]
    client.post("/api/backups", headers={"X-Local-Token": token})
    text = logs(tmp_path) + "".join(bodies)
    for secret in (password, cookie, token):
        assert secret not in text
    requests = events(tmp_path, "http_request")
    assert any(e.get("user") for e in requests)
    assert all("alice" not in json.dumps(e) for e in requests)


def run_concurrently(app, bodies):
    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await asyncio.gather(*(client.post("/api/auth/login", json=b) for b in bodies))
    return asyncio.run(main())


def slow_verify(calls):
    def verify(store, username, password, now=None):
        calls.append(username)
        time.sleep(0.05)
        return None
    return verify


def test_concurrent_same_account_only_five_hash(server, monkeypatch):
    app, _ = server
    calls = []
    monkeypatch.setattr(auth, "verify_login", slow_verify(calls))
    responses = run_concurrently(app, [{"username": "alice", "password": f"wrong{i}"} for i in range(20)])
    assert len(calls) == 5
    assert sorted(r.status_code for r in responses) == [401] * 5 + [429] * 15


def test_concurrent_same_ip_twenty_hash(server, monkeypatch):
    app, _ = server
    calls = []
    monkeypatch.setattr(auth, "verify_login", slow_verify(calls))
    responses = run_concurrently(app, [{"username": f"user{i:02d}", "password": "wrong"} for i in range(25)])
    assert len(calls) == 20
    assert sorted(r.status_code for r in responses) == [401] * 20 + [429] * 5


def test_login_storage_error_is_503_and_not_counted(server, password, monkeypatch):
    app, client = server
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(auth, "verify_login", fail)
    for _ in range(7):
        response = client.post("/api/auth/login", json={"username": "alice", "password": password})
        assert response.status_code == 503 and response.json()["code"] == "db_busy"
    monkeypatch.undo()
    login(client, password)


def test_non_ascii_csrf_header_is_403(server, password, tmp_path):
    _, client = server
    login(client, password)
    response = client.post("/api/backups", headers={"X-Local-Token": "\xff\xff\xff".encode("latin-1")})
    assert response.status_code == 403 and response.json()["code"] == "csrf_failed"
    assert "ERROR" not in logs(tmp_path)


def test_lone_surrogate_username_is_401(server):
    _, client = server
    body = json.dumps({"username": "\ud800alice", "password": "wrong"})
    response = client.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code in (401, 422), response.text


@pytest.mark.parametrize("message,code", [("database is locked", "db_busy"),
                                          ("attempt to write a readonly database", "readonly_storage")])
def test_create_session_failure_is_503(server, password, monkeypatch, tmp_path, message, code):
    _, client = server
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError(message)
    monkeypatch.setattr(auth, "create_session", fail)
    response = client.post("/api/auth/login", json={"username": "alice", "password": password})
    assert response.status_code == 503 and response.json()["code"] == code
    assert str(tmp_path) not in response.text


def test_logout_readonly_detail_has_no_path(server, password, monkeypatch, tmp_path):
    _, client = server
    token = login(client, password).json()["token"]
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr(auth, "delete_session", fail)
    response = client.post("/api/auth/logout", headers={"X-Local-Token": token})
    assert response.status_code == 503 and response.json()["code"] == "readonly_storage"
    assert response.json()["detail"] == "服务器数据目录不可写，服务暂不可用，请联系管理员。"


def test_dummy_hash_is_ready_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_DUMMY", [])
    make(tmp_path)
    assert len(auth._DUMMY) == 1


def test_auth_routes_absent_in_local_mode(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    token = client.get("/api/session").json()["token"]
    response = client.post("/api/auth/login", headers={"X-Local-Token": token},
                           json={"username": "alice", "password": "x"})
    assert response.status_code == 404 and response.json()["code"] == "unknown_endpoint"
    assert api_module.AUTH_PUBLIC >= {"/healthz", "/api/session", "/api/auth/login"}
