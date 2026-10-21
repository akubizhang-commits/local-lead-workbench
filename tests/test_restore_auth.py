"""AWS trial D2: restoring a backup keeps the current accounts (design §2.1.1, §10.1)."""
from contextlib import closing
import secrets
import shutil
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from leadworkbench import auth, db
from leadworkbench.api import create_app
from leadworkbench.config import load
from leadworkbench.worker import worker_lock
from db_hash import content_hash
from legacy_db import load_upgrade_fixture

CFG = {"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"}
BASE = "https://lw.test"


@pytest.fixture
def password():
    return secrets.token_urlsafe(18)


@pytest.fixture
def server(tmp_path, password):
    app = create_app(tmp_path / "data", config=load(CFG))
    auth.create_user(app.state.store, "alice", password)
    client = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    token = client.post("/api/auth/login", json={"username": "alice", "password": password}).json()["token"]
    client.headers["X-Local-Token"] = token
    yield app, client
    client.close()


def legacy_backup(tmp_path, store):
    path = load_upgrade_fixture(tmp_path / "legacy", "v1")
    (store.directory / "backups").mkdir(exist_ok=True)
    shutil.copyfile(path, store.directory / "backups" / "legacy-v1.sqlite3")
    return "legacy-v1.sqlite3"


def rows(store, table):
    with closing(sqlite3.connect(store.path)) as con:
        return sorted(con.execute(f"SELECT * FROM {table}").fetchall())


def test_restore_old_backup_keeps_accounts_and_sessions(server, tmp_path):
    app, client = server
    store = app.state.store
    users, sessions = rows(store, "auth_users"), rows(store, "auth_sessions")
    name = legacy_backup(tmp_path, store)
    response = client.post("/api/backups/restore", json={"name": name})
    assert response.status_code == 200, response.text
    assert rows(store, "auth_users") == users and rows(store, "auth_sessions") == sessions
    with closing(sqlite3.connect(store.path)) as con:
        assert con.execute("SELECT 1 FROM schema_migrations WHERE id='aws_20261021_auth'").fetchone()
    assert client.get("/api/overview").status_code == 200
    assert list((store.directory / "backups").glob("auto-pre-restore-*.sqlite3"))


def test_backup_sessions_do_not_come_back(server, password):
    app, client = server
    store = app.state.store
    backup = client.post("/api/backups").json()["name"]  # contains the current session
    old_cookie = client.cookies.get("__Host-lw_session")
    client.post("/api/auth/logout")
    client.cookies.clear()
    fresh = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    token = fresh.post("/api/auth/login", json={"username": "alice", "password": password}).json()["token"]
    assert fresh.post("/api/backups/restore", json={"name": backup},
                      headers={"X-Local-Token": token}).status_code == 200
    stale = TestClient(app, base_url=BASE, cookies={"__Host-lw_session": old_cookie})
    assert stale.get("/api/overview").status_code == 401
    assert fresh.get("/api/overview").status_code == 200
    assert len(rows(store, "auth_sessions")) == 1


def test_requests_during_restore_never_401_or_500(server, tmp_path, password):
    app, client = server
    name = legacy_backup(tmp_path, app.state.store)
    statuses, stop = [], threading.Event()
    reader = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    reader.cookies.update(client.cookies)
    def poll():
        while not stop.is_set():
            statuses.append(reader.get("/api/overview").status_code)
    thread = threading.Thread(target=poll)
    thread.start()
    try:
        result = client.post("/api/backups/restore", json={"name": name})
        assert result.status_code == 200, result.text
        while statuses.count(200) < 3 and thread.is_alive():
            stop.wait(0.05)
    finally:
        stop.set()
        thread.join(30)
    assert statuses and not {401, 500} & set(statuses)
    assert 200 in statuses


def test_unreadable_accounts_abort_restore(server, tmp_path, monkeypatch):
    app, client = server
    store = app.state.store
    name = legacy_backup(tmp_path, store)
    def broken(con):
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(auth, "snapshot", broken)
    before = content_hash(store.path)
    response = client.post("/api/backups/restore", json={"name": name})
    assert response.status_code == 409
    assert response.json()["code"] == "restore_auth_unreadable"
    assert response.json()["detail"] == db.RESTORE_AUTH_UNREADABLE
    assert "命令行恢复" in response.json()["detail"]
    assert content_hash(store.path) == before
    assert list((store.directory / "backups").glob("auto-pre-restore-*.sqlite3"))


def test_auth_lock_busy_maps_to_restore_busy(server, tmp_path, monkeypatch):
    app, client = server
    store = app.state.store
    name = legacy_backup(tmp_path, store)
    monkeypatch.setattr(db, "RESTORE_AUTH_LOCK_SECONDS", 0.2)
    with worker_lock(store.directory, "auth.lock"):
        response = client.post("/api/backups/restore", json={"name": name})
    assert response.status_code == 409 and response.json()["code"] == "restore_busy"


def test_local_mode_restore_also_carries_empty_auth_tables(tmp_path):
    store = db.Store(tmp_path / "data")
    name = legacy_backup(tmp_path, store)
    store.restore(name)
    assert rows(store, "auth_users") == [] and rows(store, "auth_sessions") == []
