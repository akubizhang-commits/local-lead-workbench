"""P1-6: explicit error codes and request ids (design §5.8)."""
import json
import logging
import os
import sqlite3
import stat

import pytest
from fastapi.testclient import TestClient

from leadworkbench import errors
from leadworkbench.api import create_app


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    try:
        yield client, app
    finally:
        client.close()


MISSING = [
    ("PATCH", "/api/tasks/missing/social-settings", {}),
    ("POST", "/api/tasks/missing/social", {"social_platforms": ["facebook"]}),
    ("POST", "/api/tasks/missing/runs", None),
    ("GET", "/api/tasks/missing/activity", None),
    ("GET", "/api/runs/missing/items/missing", None),
    ("GET", "/api/runs/missing", None),
    ("POST", "/api/runs/missing/relocate", None),
    ("POST", "/api/runs/missing/pause", None),
    ("DELETE", "/api/exclusion-rules/missing", None),
    ("GET", "/api/companies/missing", None),
    ("PATCH", "/api/companies/missing", {"revision": 1, "notes": "x"}),
    ("POST", "/api/companies/missing/contacts", {"revision": 1, "phone": "+1 408 555 0101"}),
    ("POST", "/api/companies/missing/refresh", None),
    ("GET", "/api/social/runs/missing", None),
    ("POST", "/api/social/runs/missing/cancel", None),
    ("POST", "/api/social/runs/missing/retry", None),
    ("GET", "/api/companies/missing/social", None),
    ("POST", "/api/companies/missing/social/sync", None),
    ("POST", "/api/companies/missing/social/links", {"url": "https://www.instagram.com/fixturebrand"}),
    ("POST", "/api/companies/missing/social/missing/decision", {"status": "candidate", "revision": 1}),
    ("POST", "/api/companies/missing/social/outreach", {"entity_id": "e", "state": "draft", "note": "n"}),
    ("POST", "/api/reviews/missing/decision", {"decision": "accept"}),
    ("PATCH", "/api/schedules/missing", {"enabled": True}),
    ("PATCH", "/api/tasks/missing", {"keywords": ["x"]}),
    ("GET", "/api/export-jobs/missing", None),
    ("POST", "/api/export-jobs/missing/cancel", None),
    ("GET", "/api/export-jobs/missing/file", None),
]


def test_every_id_route_is_parametrized(api):
    _, app = api
    paths = {(m, r.path) for r in app.routes if "{id}" in getattr(r, "path", "") for m in r.methods}
    covered = {(m, p.replace("missing", "{id}", 1)) for m, p, _ in MISSING}
    normalized = {(m, p.replace("/items/missing", "/items/{item_id}").replace("/social/missing/", "/social/{entity_id}/")
                   .replace("/pause", "/{action}")) for m, p in covered}
    assert paths <= normalized


@pytest.mark.parametrize("method,path,body", MISSING)
def test_1_missing_id_is_not_found_with_request_id(api, method, path, body):
    client, _ = api
    response = client.request(method, path, json=body)
    assert response.status_code == 404, response.text
    payload = response.json()
    assert payload["code"] == "not_found"
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert payload["request_id"].startswith("req_")
    assert not payload["detail"].startswith("'")


def test_2_bare_key_error_is_internal_and_logged(api, monkeypatch, caplog):
    client, app = api
    monkeypatch.setattr(app.state.store, "tasks", lambda: {}["secret-key-x"])
    with caplog.at_level(logging.ERROR, logger="leadworkbench.api"):
        response = client.get("/api/tasks")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "internal"
    assert "secret-key-x" not in response.text
    assert payload["request_id"] in payload["detail"]
    records = [r for r in caplog.records if getattr(r, "event", "") == "unhandled"]
    assert records and records[0].request_id == payload["request_id"]
    assert records[0].exc_info
    # The same failure is in logs/api.log as one JSON line with the traceback (P1-7).
    lines = [json.loads(line) for line in (app.state.data_dir / "logs/api.log").read_text().splitlines()]
    errors_logged = [line for line in lines if line["event"] == "unhandled"]
    assert errors_logged and errors_logged[0]["level"] == "ERROR"
    assert errors_logged[0]["request_id"] == payload["request_id"]
    assert "Traceback" in errors_logged[0]["exc"] and errors_logged[0]["error_type"] == "KeyError"


@pytest.mark.parametrize("message,status,code", [
    ("database or disk is full", 507, "disk_full"),
    ("attempt to write a readonly database", 503, "readonly_storage"),
    ("database is locked", 503, "db_busy"),
])
def test_3_storage_errors_are_classified(api, monkeypatch, message, status, code):
    client, app = api
    def failing():
        raise sqlite3.OperationalError(message)
    monkeypatch.setattr(app.state.store, "tasks", failing)
    response = client.get("/api/tasks")
    assert response.status_code == status
    assert response.json()["code"] == code
    if code == "db_busy":
        assert response.headers["Retry-After"] == "2"


def test_4_request_id_reaches_thread_pool_logs(api, monkeypatch, caplog):
    client, app = api
    seen = []
    def logging_tasks():
        logging.getLogger("leadworkbench.db").warning("inside store")
        seen.append(errors.REQUEST_ID.get())
        return []
    monkeypatch.setattr(app.state.store, "tasks", logging_tasks)
    response = client.get("/api/tasks")
    assert response.status_code == 200
    assert seen == [response.headers["X-Request-ID"]]


def test_5_readonly_directory_degrades(tmp_path, capsys):
    directory = tmp_path / "ro"
    directory.mkdir()
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        app = create_app(directory, allowed_hosts={"testserver"})
        with TestClient(app) as client:
            session = client.get("/api/session")
            assert session.status_code == 200
            assert session.headers["X-Lead-Unavailable"] == "readonly_storage"
            client.headers["X-Local-Token"] = session.json()["token"]
            overview = client.get("/api/overview")
            assert overview.status_code == 503
            assert overview.json()["code"] == "readonly_storage"
            assert overview.json()["request_id"] == overview.headers["X-Request-ID"]
        assert "数据目录不可写" in capsys.readouterr().err
    finally:
        directory.chmod(stat.S_IRWXU)


def test_6_validation_keeps_array_detail(api):
    client, _ = api
    response = client.post("/api/tasks", json={"state": ""})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["code"] == "validation"


@pytest.mark.parametrize("message,status", [("internal check failed", 500), ("请选择地区", 400)])
def test_7_untyped_value_errors(api, monkeypatch, message, status):
    client, app = api
    def failing():
        raise ValueError(message)
    monkeypatch.setattr(app.state.store, "tasks", failing)
    response = client.get("/api/tasks")
    assert response.status_code == status
    assert response.json()["code"] == ("internal" if status == 500 else "invalid")
    assert (message in response.text) == (status == 400)


def test_boundary_errors_carry_codes(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        missing = client.get("/api/tasks")
        assert missing.status_code == 401 and missing.json()["code"] == "unauthorized"
        assert missing.json()["request_id"] == missing.headers["X-Request-ID"]
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        unknown = client.get("/api/no-such-endpoint")
        assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_endpoint"
        assert client.get("/api/session").headers["X-Request-ID"].startswith("req_")


def test_storage_unavailable_fault_keeps_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "storage_unavailable")
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    assert app.state.store is None
    with TestClient(app) as client:  # lifespan shutdown must not raise in degraded mode
        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.headers["X-Lead-Unavailable"] == "storage_unavailable"
        client.headers["X-Local-Token"] = session.json()["token"]
        response = client.get("/api/settings")
        assert response.status_code == 503 and response.json()["code"] == "storage_unavailable"


def test_internal_fault(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TEST_FAULT", "internal")
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    response = client.get("/api/settings")
    assert response.status_code == 500 and response.json()["code"] == "internal"


def test_ioerr_with_low_space_is_disk_full(tmp_path, monkeypatch):
    import shutil
    exc = sqlite3.OperationalError("disk I/O error")
    exc.sqlite_errorcode = 10 | (3 << 8)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10, 10, 1000))
    assert errors.classify_storage_error(exc, tmp_path).code == "disk_full"
    monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10**10, 0, 10**9))
    assert errors.classify_storage_error(exc, tmp_path) is None


def test_not_found_message_is_unquoted():
    exc = errors.NotFound("企业不存在或已合并")
    assert str(exc) == "企业不存在或已合并"
    assert isinstance(exc, KeyError)
