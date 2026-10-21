"""AWS trial D1: /healthz and the container health checks (design §1.4)."""
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from leadworkbench import healthcheck
from leadworkbench.api import create_app


def test_healthz_is_json_before_the_frontend_fallback(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


def test_healthz_still_checks_host(tmp_path):
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}), base_url="http://evil.test")
    assert client.get("/healthz").status_code == 403


def test_healthz_degraded_has_only_status(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "storage_unavailable")
    client = TestClient(create_app(tmp_path, allowed_hosts={"testserver"}))
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def heartbeat(tmp_path, age):
    seen = datetime.now(timezone.utc) - timedelta(seconds=age)
    (tmp_path / "worker-heartbeat.json").write_text(json.dumps({"pid": 1, "last_seen": seen.isoformat()}))


def test_worker_healthcheck(tmp_path):
    env = {"LEAD_DATA_DIR": str(tmp_path)}
    assert healthcheck.check_worker(env) == 1  # no heartbeat yet
    heartbeat(tmp_path, 5)
    assert healthcheck.check_worker(env) == 0
    heartbeat(tmp_path, 120)
    assert healthcheck.check_worker(env) == 1
    (tmp_path / "worker-heartbeat.json").write_text("{broken")
    assert healthcheck.check_worker(env) == 1


def test_api_healthcheck_fails_without_a_server():
    assert healthcheck.check_api({"LEAD_PORT": "18855"}) == 1


def test_usage_exit_code():
    assert healthcheck.main(["nope"]) == 2
