"""AWS trial: the default local mode behaves as before (design §0.2, §10.1)."""
from fastapi.testclient import TestClient

from leadworkbench.api import create_app


def test_session_returns_the_start_token_without_login(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    body = client.get("/api/session").json()
    assert body["mode"] == "local" and body["auth"] is False and len(body["token"]) > 40
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"X-Local-Token": body["token"]}).status_code == 200


def test_auth_endpoints_do_not_exist(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    token = client.get("/api/session").json()["token"]
    for path in ("/api/auth/login", "/api/auth/logout"):
        response = client.post(path, headers={"X-Local-Token": token}, json={})
        assert response.status_code == 404 and response.json()["code"] == "unknown_endpoint"


def test_degraded_mode_still_serves_the_diagnostics_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "storage_unavailable")
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    token = client.get("/api/session").json()["token"]
    response = client.get("/api/diagnostics/bundle", headers={"X-Local-Token": token})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
