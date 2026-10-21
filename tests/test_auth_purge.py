"""AWS trial D2: the worker purges expired sessions (design §2.4)."""
from datetime import timedelta
import secrets

from leadworkbench import auth, worker
from leadworkbench.config import load
from leadworkbench.db import Store
from leadworkbench.worker import worker_lock

CFG = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"})


def expired_session(store):
    password = secrets.token_urlsafe(18)
    auth.create_user(store, "alice", password)
    user = auth.verify_login(store, "alice", password)
    auth.create_session(store, user["id"], auth.utcnow() - timedelta(days=30), CFG)


def count(store):
    with store.connect() as con:
        return con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0]


def test_purge_skips_while_auth_lock_is_held(tmp_path):
    store = Store(tmp_path)
    expired_session(store)
    with worker_lock(tmp_path, "auth.lock"):
        assert worker.run_auth_purge(store) is None
    assert count(store) == 1
    assert worker.run_auth_purge(store) == 1
    assert count(store) == 0


def test_purge_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    store = Store(tmp_path)
    def broken(*args):
        raise OSError("disk")
    monkeypatch.setattr(auth, "purge_expired", broken)
    assert worker.run_auth_purge(store) is None
    assert any(getattr(r, "event", "") == "auth_purge_failed" for r in caplog.records)
