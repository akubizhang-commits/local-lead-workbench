"""AWS trial D2: accounts and sessions (design §2.1–2.4, §10.1 test_auth)."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
import secrets
import sqlite3

import pytest

from leadworkbench import auth
from leadworkbench.config import load
from leadworkbench.db import Store
from leadworkbench.worker import LockTimeout, worker_lock

CFG = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"})
T0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def password():
    return secrets.token_urlsafe(18)


def test_hash_format_and_verify(password):
    stored = auth.hash_password(password)
    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "32768", "8", "1")
    assert "=" not in salt + digest
    assert len(auth._unb64(salt)) == 16 and len(auth._unb64(digest)) == 32
    assert auth.verify_password(password, stored)
    assert not auth.verify_password(password + "x", stored)
    assert not auth.verify_password(password, "garbage")
    assert auth.hash_password(password) != stored  # random salt


def test_username_rules():
    assert auth.validate_username("Alice.B-1") == "Alice.B-1"
    assert auth.validate_username("ａｌｉｃｅ") == "alice"  # NFKC
    for bad in ("ab", "_alice", "a" * 33, "al ice", "张三丰", ""):
        with pytest.raises(ValueError):
            auth.validate_username(bad)
    assert auth.normalize_username("ＡＬＩＣＥ") == auth.normalize_username("alice") == "alice"


def test_password_rules():
    with pytest.raises(ValueError):
        auth.validate_new_password("short", "alice")
    with pytest.raises(ValueError):
        auth.validate_new_password(" " * 12, "alice")
    with pytest.raises(ValueError):
        auth.validate_new_password("AliceAlice12", "alicealice12")
    with pytest.raises(ValueError):
        auth.validate_new_password("x" * 129, "alice")
    assert auth.validate_new_password("x" * 12, "alice")


def test_create_and_verify_login(store, password):
    auth.create_user(store, "Alice", password)
    with pytest.raises(auth.UserExists):
        auth.create_user(store, "alice", password)
    user = auth.verify_login(store, "ALICE", password)
    assert user and user["username"] == "Alice"
    assert auth.verify_login(store, "alice", password + "x") is None
    assert auth.verify_login(store, "nobody", password) is None
    assert auth.verify_login(store, "x", password) is None  # invalid name still answers None
    with store.connect() as con:
        row = con.execute("SELECT tenant_id,last_login_at,password_hash FROM auth_users").fetchone()
    assert row["tenant_id"] is None and row["last_login_at"]
    assert password not in row["password_hash"]


def test_rehash_on_login_when_parameters_change(store, password):
    auth.create_user(store, "alice", password)
    with store.connect() as con:
        con.execute("UPDATE auth_users SET password_hash=?", (auth.hash_password(password, n=2 ** 14),))
    assert auth.verify_login(store, "alice", password)
    with store.connect() as con:
        assert con.execute("SELECT password_hash FROM auth_users").fetchone()[0].startswith("scrypt$32768$")


def test_disabled_user_cannot_login_and_sessions_end(store, password):
    auth.create_user(store, "alice", password)
    user = auth.verify_login(store, "alice", password)
    cookie, _ = auth.create_session(store, user["id"], T0, CFG)
    assert auth.resolve_session(store, cookie, T0, CFG)
    auth.set_disabled(store, "alice", True)
    assert auth.verify_login(store, "alice", password) is None
    assert auth.resolve_session(store, cookie, T0, CFG) is None
    auth.set_disabled(store, "alice", False)
    assert auth.verify_login(store, "alice", password)
    with pytest.raises(auth.UserMissing):
        auth.set_disabled(store, "bob", True)


def test_reset_password_clears_sessions(store, password):
    auth.create_user(store, "alice", password)
    user = auth.verify_login(store, "alice", password)
    cookie, _ = auth.create_session(store, user["id"], T0, CFG)
    new = secrets.token_urlsafe(18)
    auth.set_password(store, "alice", new)
    assert auth.resolve_session(store, cookie, T0, CFG) is None
    assert auth.verify_login(store, "alice", new) and not auth.verify_login(store, "alice", password)


def test_session_only_stores_hashes(store, password):
    auth.create_user(store, "alice", password)
    user = auth.verify_login(store, "alice", password)
    cookie, csrf = auth.create_session(store, user["id"], T0, CFG)
    with closing(sqlite3.connect(store.path)) as con:
        rows = con.execute("SELECT * FROM auth_sessions").fetchall()
    assert len(rows) == 1
    assert cookie not in repr(rows)
    assert rows[0][0] == auth.sha256_hex(cookie)
    assert rows[0][-1] is None  # tenant_id
    session = auth.resolve_session(store, cookie, T0, CFG)
    assert session.csrf_token == csrf and session.username == "alice"


def test_idle_and_absolute_expiry(store, password):
    auth.create_user(store, "alice", password)
    uid = auth.verify_login(store, "alice", password)["id"]
    cookie, _ = auth.create_session(store, uid, T0, CFG)
    # Idle: 24 h without use ends the session.
    assert auth.resolve_session(store, cookie, T0 + timedelta(hours=23, minutes=59), CFG)
    later = T0 + timedelta(hours=23, minutes=59)
    # Touched above: idle deadline moved.
    assert auth.resolve_session(store, cookie, later + timedelta(hours=23), CFG)
    cookie2, _ = auth.create_session(store, uid, T0, CFG)
    assert auth.resolve_session(store, cookie2, T0 + timedelta(hours=24, seconds=1), CFG) is None
    # Absolute: never beyond 7 days even when used every few hours.
    cookie3, _ = auth.create_session(store, uid, T0, CFG)
    moment = T0
    while moment < T0 + timedelta(days=7) - timedelta(hours=6):
        moment += timedelta(hours=6)
        assert auth.resolve_session(store, cookie3, moment, CFG), moment
    assert auth.resolve_session(store, cookie3, T0 + timedelta(days=7, seconds=1), CFG) is None
    # Expired rows stay until purge.
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM auth_sessions WHERE id_hash=?",
                           (auth.sha256_hex(cookie2),)).fetchone()[0] == 1
    assert auth.purge_expired(store, T0 + timedelta(days=8)) >= 2
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 0


def test_touch_failure_still_resolves(store, password, monkeypatch):
    auth.create_user(store, "alice", password)
    uid = auth.verify_login(store, "alice", password)["id"]
    cookie, _ = auth.create_session(store, uid, T0, CFG)
    real = sqlite3.connect
    def broken(path, *args, **kwargs):
        if kwargs.get("timeout") == 0.2:
            raise sqlite3.OperationalError("database is locked")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(auth.sqlite3, "connect", broken)
    assert auth.resolve_session(store, cookie, T0 + timedelta(hours=1), CFG)


def test_touch_skipped_when_disabled(store, password):
    auth.create_user(store, "alice", password)
    uid = auth.verify_login(store, "alice", password)["id"]
    cookie, _ = auth.create_session(store, uid, T0, CFG)
    auth.resolve_session(store, cookie, T0 + timedelta(hours=1), CFG, touch=False)
    with store.connect() as con:
        assert con.execute("SELECT last_seen_at FROM auth_sessions").fetchone()[0] == auth.iso(T0)


def test_session_cap_and_rotation(store, password):
    auth.create_user(store, "alice", password)
    uid = auth.verify_login(store, "alice", password)["id"]
    cookies = [auth.create_session(store, uid, T0 + timedelta(seconds=i), CFG)[0] for i in range(12)]
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 10
    now = T0 + timedelta(seconds=20)
    assert auth.resolve_session(store, cookies[0], now, CFG) is None
    assert auth.resolve_session(store, cookies[-1], now, CFG)
    replaced, _ = auth.create_session(store, uid, now, CFG, replace_id_hash=auth.sha256_hex(cookies[-1]))
    assert auth.resolve_session(store, cookies[-1], now, CFG) is None
    assert auth.resolve_session(store, replaced, now, CFG)


def test_logout_all(store, password):
    auth.create_user(store, "alice", password)
    auth.create_user(store, "bob01", password)
    for name in ("alice", "bob01"):
        auth.create_session(store, auth.verify_login(store, name, password)["id"], T0, CFG)
    assert auth.delete_user_sessions(store, "alice") == 1
    assert auth.delete_user_sessions(store) == 1
    with pytest.raises(auth.UserMissing):
        auth.delete_user_sessions(store, "carol")


def test_snapshot_round_trip(store, tmp_path, password):
    auth.create_user(store, "alice", password)
    auth.create_session(store, auth.verify_login(store, "alice", password)["id"], T0, CFG)
    with store.connect() as con:
        snap = auth.snapshot(con)
    other = tmp_path / "other.sqlite3"
    with closing(sqlite3.connect(other)) as con:
        from leadworkbench.auth_migrations import _auth_ddl
        _auth_ddl(None, con)
        auth.replace_all(con, snap)
        con.commit()
        assert con.execute("SELECT count(*) FROM auth_users").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1


def test_auth_lock_times_out(tmp_path):
    with worker_lock(tmp_path, "auth.lock"):
        import threading
        errors = []
        def other():
            try:
                with auth.auth_lock(tmp_path, wait_seconds=0.2):
                    pass
            except LockTimeout as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(5)
    # flock is per open file description: a second open in the same process conflicts too.
    assert errors == [auth.RESTORING]
