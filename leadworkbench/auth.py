"""Accounts and sessions for server mode (AWS trial, design §2.1–2.4).

``Store`` only provides connections; every account/session rule lives here.
Session identifiers are never stored: the database keeps sha256(id) only.
Request-path session resolution is read-only; extending the idle deadline
("touch") is best effort on a separate short-timeout connection.
"""
from __future__ import annotations
import base64
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata

log = logging.getLogger(__name__)

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SCRYPT_DKLEN = 32
MAX_SESSIONS_PER_USER = 10
TOUCH_AFTER = timedelta(minutes=5)
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
USER_COLUMNS = ("id", "username", "username_key", "password_hash", "disabled", "tenant_id", "created_at",
                "updated_at", "password_changed_at", "last_login_at")
SESSION_COLUMNS = ("id_hash", "user_id", "csrf_token", "created_at", "last_seen_at", "expires_at", "tenant_id")
RESTORING = "正在恢复备份，请稍后重试"


class UserExists(ValueError):
    pass


class UserMissing(LookupError):
    pass


@dataclass(frozen=True)
class Session:
    id_hash: str
    user_id: str
    username: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


def utcnow():
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Fixed-width UTC timestamps so string comparison in SQL equals time order."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---- validation (shared by the admin CLI and the login endpoint, §2.2) ----

def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username).casefold()


def validate_username(username: str) -> str:
    """NFKC form, 3–32 characters of [A-Za-z0-9._-], starting with a letter or digit."""
    value = unicodedata.normalize("NFKC", username or "")
    if not USERNAME.match(value):
        raise ValueError("用户名需为 3–32 个字符，只能包含字母、数字、点、下划线和短横线，且以字母或数字开头")
    return value


def validate_new_password(password: str, username: str) -> str:
    if not 12 <= len(password or "") <= 128:
        raise ValueError("密码长度需为 12–128 个字符")
    if not password.strip():
        raise ValueError("密码不能全是空白字符")
    if password.casefold() == (username or "").casefold():
        raise ValueError("密码不能与用户名相同")
    return password


# ---- password hashing (§2.3) ----

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                            maxmem=SCRYPT_MAXMEM, dklen=SCRYPT_DKLEN)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def _parse_hash(stored: str):
    scheme, n, r, p, salt, digest = stored.split("$")
    if scheme != "scrypt":
        raise ValueError("unknown hash scheme")
    return int(n), int(r), int(p), _unb64(salt), _unb64(digest)


def verify_password(password: str, stored: str) -> bool:
    try:
        n, r, p, salt, expected = _parse_hash(stored)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                                maxmem=SCRYPT_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    try:
        n, r, p, _, _ = _parse_hash(stored)
    except (ValueError, TypeError):
        return True
    return (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


_DUMMY_LOCK = threading.Lock()
_DUMMY: list[str] = []


def _dummy_hash() -> str:
    """Hash of a random password generated once per process (constant-time misses)."""
    with _DUMMY_LOCK:
        if not _DUMMY:
            _DUMMY.append(hash_password(secrets.token_urlsafe(18)))
        return _DUMMY[0]


# ---- accounts ----

def _user(con, username):
    return con.execute(f"SELECT {','.join(USER_COLUMNS)} FROM auth_users WHERE username_key=?",
                       (normalize_username(username),)).fetchone()


def create_user(store, username: str, password: str, now: datetime | None = None) -> dict:
    from .db import uid
    shown = validate_username(username)
    validate_new_password(password, shown)
    stamp = iso(now or utcnow())
    digest = hash_password(password)
    try:
        with store.connect() as con:
            con.execute("INSERT INTO auth_users(id,username,username_key,password_hash,disabled,tenant_id,"
                        "created_at,updated_at,password_changed_at,last_login_at) VALUES(?,?,?,?,0,NULL,?,?,?,NULL)",
                        (uid(), shown, normalize_username(shown), digest, stamp, stamp, stamp))
    except sqlite3.IntegrityError as exc:
        raise UserExists("账号已存在") from exc
    return {"username": shown}


def set_password(store, username: str, password: str, now: datetime | None = None):
    """Reset the password and sign the user out everywhere."""
    stamp = iso(now or utcnow())
    with store.connect() as con:
        row = _user(con, username)
        if row is None:
            raise UserMissing("账号不存在")
        validate_new_password(password, row["username"])
        con.execute("UPDATE auth_users SET password_hash=?,updated_at=?,password_changed_at=? WHERE id=?",
                    (hash_password(password), stamp, stamp, row["id"]))
        con.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))


def set_disabled(store, username: str, disabled: bool, now: datetime | None = None):
    stamp = iso(now or utcnow())
    with store.connect() as con:
        row = _user(con, username)
        if row is None:
            raise UserMissing("账号不存在")
        con.execute("UPDATE auth_users SET disabled=?,updated_at=? WHERE id=?", (int(disabled), stamp, row["id"]))
        if disabled:
            con.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))


def list_users(store) -> list[dict]:
    with store.connect() as con:
        rows = con.execute("SELECT username,disabled,created_at,last_login_at FROM auth_users "
                           "ORDER BY username_key").fetchall()
    return [{"username": r["username"], "disabled": bool(r["disabled"]), "created_at": r["created_at"],
             "last_login_at": r["last_login_at"]} for r in rows]


def user_by_id(store, user_id: str) -> dict | None:
    with store.connect() as con:
        row = con.execute("SELECT username,last_login_at FROM auth_users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def verify_login(store, username: str, password: str, now: datetime | None = None) -> dict | None:
    """The user row on success, else None. Unknown or disabled users still cost one scrypt."""
    try:
        key_ok = bool(USERNAME.match(unicodedata.normalize("NFKC", username or "")))
    except TypeError:
        key_ok = False
    row = None
    if key_ok:
        with store.connect() as con:
            row = _user(con, username)
    if row is None or row["disabled"]:
        verify_password(password, _dummy_hash())
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    stamp = iso(now or utcnow())
    with store.connect() as con:
        if needs_rehash(row["password_hash"]):
            con.execute("UPDATE auth_users SET password_hash=?,updated_at=? WHERE id=?",
                        (hash_password(password), stamp, row["id"]))
        con.execute("UPDATE auth_users SET last_login_at=? WHERE id=?", (stamp, row["id"]))
    return {"id": row["id"], "username": row["username"]}


def user_known(store, username: str) -> bool:
    try:
        with store.connect() as con:
            return _user(con, username) is not None
    except (sqlite3.Error, OSError, TypeError):
        return False


# ---- sessions (§2.4) ----

def _expiry(created: datetime, seen: datetime, cfg) -> datetime:
    return min(created + cfg.session_max, seen + cfg.session_idle)


def create_session(store, user_id: str, now: datetime, cfg, replace_id_hash: str | None = None):
    """New session (login always rotates). Returns (cookie value, csrf token)."""
    value = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with store.connect() as con:
        if replace_id_hash:
            con.execute("DELETE FROM auth_sessions WHERE id_hash=?", (replace_id_hash,))
        con.execute("DELETE FROM auth_sessions WHERE user_id=? AND expires_at<=?", (user_id, iso(now)))
        con.execute("INSERT INTO auth_sessions(id_hash,user_id,csrf_token,created_at,last_seen_at,expires_at,tenant_id) "
                    "VALUES(?,?,?,?,?,?,NULL)",
                    (sha256_hex(value), user_id, csrf, iso(now), iso(now), iso(_expiry(now, now, cfg))))
        extra = con.execute("SELECT id_hash FROM auth_sessions WHERE user_id=? ORDER BY created_at DESC, rowid DESC "
                            "LIMIT -1 OFFSET ?", (user_id, MAX_SESSIONS_PER_USER)).fetchall()
        for row in extra:
            con.execute("DELETE FROM auth_sessions WHERE id_hash=?", (row[0],))
    return value, csrf


_THROTTLE_LOCK = threading.Lock()
_THROTTLE: dict[str, float] = {}


def _debug_once_per_minute(event, error_type):
    with _THROTTLE_LOCK:
        last = _THROTTLE.get(event, 0.0)
        if time.monotonic() - last < 60:
            return
        _THROTTLE[event] = time.monotonic()
    log.debug("session touch failed", extra={"event": event, "error_type": error_type})


def _touch(path, id_hash, now: datetime, created: datetime, cfg):
    try:
        with closing(sqlite3.connect(path, timeout=0.2)) as con:
            con.execute("PRAGMA busy_timeout=200")
            con.execute("UPDATE auth_sessions SET last_seen_at=?, expires_at=? WHERE id_hash=?",
                        (iso(now), iso(_expiry(created, now, cfg)), id_hash))
            con.commit()
    except (sqlite3.Error, OSError) as exc:
        _debug_once_per_minute("auth_touch_failed", type(exc).__name__)


def resolve_session(store, cookie_value: str, now: datetime, cfg, *, touch=True) -> Session | None:
    """Read-only lookup; sqlite3.Error / OSError propagate (the caller answers 503)."""
    if not cookie_value:
        return None
    id_hash = sha256_hex(cookie_value)
    with store.connect() as con:
        row = con.execute("SELECT s.user_id,s.csrf_token,s.created_at,s.last_seen_at,s.expires_at,"
                          "u.username,u.disabled FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id "
                          "WHERE s.id_hash=?", (id_hash,)).fetchone()
    if row is None or row["disabled"]:
        return None
    expires = parse(row["expires_at"])
    if expires <= now:
        return None
    created = parse(row["created_at"])
    if touch and now - parse(row["last_seen_at"]) > TOUCH_AFTER:
        _touch(store.path, id_hash, now, created, cfg)
        expires = _expiry(created, now, cfg)
    return Session(id_hash=id_hash, user_id=row["user_id"], username=row["username"],
                   csrf_token=row["csrf_token"], created_at=created, expires_at=expires)


def delete_session(store, id_hash: str):
    with store.connect() as con:
        con.execute("DELETE FROM auth_sessions WHERE id_hash=?", (id_hash,))


def delete_user_sessions(store, username: str | None = None) -> int:
    """Sign one user (or everybody) out."""
    with store.connect() as con:
        if username is None:
            return con.execute("DELETE FROM auth_sessions").rowcount
        row = _user(con, username)
        if row is None:
            raise UserMissing("账号不存在")
        return con.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],)).rowcount


def purge_expired(store, now: datetime) -> int:
    with store.connect() as con:
        count = con.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (iso(now),)).rowcount
        count += con.execute("DELETE FROM auth_sessions WHERE user_id IN "
                             "(SELECT id FROM auth_users WHERE disabled=1)").rowcount
    return count


def counts(con) -> dict:
    return {"users": con.execute("SELECT count(*) FROM auth_users").fetchone()[0],
            "sessions": con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0]}


# ---- restore support (§2.1.1) ----

def snapshot(con) -> dict:
    """Explicit-column copy of both tables (users first)."""
    return {"users": [tuple(r) for r in con.execute(f"SELECT {','.join(USER_COLUMNS)} FROM auth_users")],
            "sessions": [tuple(r) for r in con.execute(f"SELECT {','.join(SESSION_COLUMNS)} FROM auth_sessions")]}


def replace_all(con, snap: dict):
    """Write a snapshot into ``con`` (tables must exist and be empty)."""
    con.executemany(f"INSERT INTO auth_users({','.join(USER_COLUMNS)}) VALUES({','.join('?' * len(USER_COLUMNS))})",
                    snap["users"])
    con.executemany(f"INSERT INTO auth_sessions({','.join(SESSION_COLUMNS)}) "
                    f"VALUES({','.join('?' * len(SESSION_COLUMNS))})", snap["sessions"])


@contextmanager
def auth_lock(directory, wait_seconds: float):
    """``{data}/auth.lock``; raises worker.LockTimeout after ``wait_seconds``."""
    from .worker import worker_lock
    with worker_lock(directory, "auth.lock", wait_seconds=wait_seconds, message=RESTORING):
        yield
