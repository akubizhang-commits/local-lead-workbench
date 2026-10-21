"""AWS trial D2: administrator command line (design §2.8, §2.8.1, §10.1 test_admin_cli)."""
from contextlib import closing
import io
import os
import secrets
import sqlite3
import subprocess
import sys

import pytest

from leadworkbench import admin, auth
from leadworkbench.config import load
from leadworkbench.db import Store
from leadworkbench.worker import worker_lock
from db_hash import content_hash
from legacy_db import load_upgrade_fixture

HOLDER = """
import fcntl, sys, time
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(30)
"""


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("LEAD_MODE", "LEAD_AUTH", "LEAD_BIND", "LEAD_ALLOWED_HOSTS", "LEAD_PUBLIC_ORIGIN",
                 "LEAD_BACKUP_DAILY", "LEAD_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(admin, "PROCESS_LOCK_SECONDS", 0.2)
    monkeypatch.setattr(admin, "AUTH_LOCK_SECONDS", 0.2)


@pytest.fixture
def server_env(monkeypatch):
    monkeypatch.setenv("LEAD_MODE", "server")
    monkeypatch.setenv("LEAD_PUBLIC_ORIGIN", "https://lw.test")


@pytest.fixture
def password():
    return secrets.token_urlsafe(18)


def cli(data, *args, stdin=""):
    """(exit code, stdout, stderr)."""
    old = sys.stdin, sys.stdout, sys.stderr
    sys.stdin, sys.stdout, sys.stderr = io.StringIO(stdin), io.StringIO(), io.StringIO()
    try:
        code = admin.run(["--data-dir", str(data), *args])
        return code, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


def sha(path):
    """Logical content (opening a Store may checkpoint the WAL, which changes raw bytes)."""
    return content_hash(path)


def holder(path):
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "held"
    return process


def test_account_lifecycle(tmp_path, password):
    code, out, err = cli(tmp_path, "create-user", "alice", stdin=password + "\n")
    assert (code, out.strip()) == (0, "已创建账号 alice"), err
    store = Store(tmp_path)
    assert auth.verify_login(store, "alice", password)
    assert cli(tmp_path, "create-user", "Alice", stdin=password + "\n")[:1] == (1,)
    code, out, _ = cli(tmp_path, "list-users")
    assert code == 0 and "alice" in out and "正常" in out
    assert password not in out
    new = secrets.token_urlsafe(18)
    auth.create_session(store, auth.verify_login(store, "alice", password)["id"], auth.utcnow(),
                        load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"}))
    code, out, err = cli(tmp_path, "reset-password", "alice", stdin=new + "\n")
    assert code == 0 and new not in out + err
    assert auth.verify_login(store, "alice", new) and not auth.verify_login(store, "alice", password)
    assert cli(tmp_path, "disable-user", "alice")[0] == 0
    assert "已停用" in cli(tmp_path, "list-users")[1]
    assert auth.verify_login(store, "alice", new) is None
    assert cli(tmp_path, "enable-user", "alice")[0] == 0
    assert auth.verify_login(store, "alice", new)
    code, out, _ = cli(tmp_path, "logout-all", "alice")
    assert code == 0 and "已强制退出" in out
    assert cli(tmp_path, "logout-all")[0] == 0
    code, out, _ = cli(tmp_path, "backup")
    assert code == 0 and out.strip().startswith("backup-") and (tmp_path / "backups" / out.strip()).is_file()


def test_errors_and_exit_codes(tmp_path, password):
    assert cli(tmp_path, "create-user", "x", stdin=password + "\n")[0] == 2
    code, _, err = cli(tmp_path, "create-user", "alice", stdin="short\nalso short\nstill\n")
    assert code == 2 and "密码长度" in err
    assert cli(tmp_path, "reset-password", "nobody", stdin=password + "\n")[:1] == (1,)
    code, _, err = cli(tmp_path, "disable-user", "nobody")
    assert code == 1 and "账号不存在" in err
    assert cli(tmp_path, "logout-all", "nobody")[0] == 1
    assert cli(tmp_path, "unknown-command")[0] == 2
    # Two bad passwords then a good one.
    code, out, err = cli(tmp_path, "create-user", "bob01", stdin=f"short\nbob01\n{password}\n")
    assert code == 0 and password not in out + err


def test_auth_lock_busy(tmp_path, password):
    Store(tmp_path)
    with worker_lock(tmp_path, "auth.lock"):
        code, _, err = cli(tmp_path, "create-user", "alice", stdin=password + "\n")
    assert code == 1 and auth.RESTORING in err


def test_unopenable_database(tmp_path):
    (tmp_path / "workbench.sqlite3").write_bytes(b"not a database" * 100)
    code, _, err = cli(tmp_path, "list-users")
    assert code == 1 and err.startswith("无法打开数据库：")


# ---- restore ----

def setup_restore(tmp_path, password):
    data = tmp_path / "data"
    store = Store(data)
    auth.create_user(store, "alice", password)
    name = store.backup()["name"]  # backup with alice only
    auth.create_user(store, "carol", password)
    return data, store, name


@pytest.mark.parametrize("filename,message", [("api.lock", admin.STOP_API),
                                              ("worker-single.lock", admin.STOP_WORKER),
                                              ("worker.lock", admin.STOP_WORKER)])
def test_restore_requires_stopped_services(tmp_path, password, server_env, filename, message):
    data, store, name = setup_restore(tmp_path, password)
    process = holder(data / filename)
    try:
        before = sha(store.path)
        code, _, err = cli(data, "restore", name)
    finally:
        process.kill()
        process.wait()
    assert code == 1 and message in err
    assert sha(store.path) == before


def test_restore_current_accounts(tmp_path, password, server_env):
    data, store, name = setup_restore(tmp_path, password)
    with store.connect() as con:
        users = sorted(tuple(r) for r in con.execute("SELECT * FROM auth_users"))
    code, out, err = cli(data, "restore", name)
    assert code == 0, err
    assert "保留当前账号（2 个账号）" in out and "alice" not in out and "carol" not in out
    with store.connect() as con:
        assert sorted(tuple(r) for r in con.execute("SELECT * FROM auth_users")) == users
    assert list((data / "backups").glob("auto-pre-restore-*.sqlite3"))


def test_restore_current_unreadable_accounts(tmp_path, password, server_env, monkeypatch):
    data, store, name = setup_restore(tmp_path, password)
    def broken(con):
        raise sqlite3.DatabaseError("broken")
    monkeypatch.setattr(auth, "snapshot", broken)
    before = sha(store.path)
    code, _, err = cli(data, "restore", name)
    assert code == 1 and "--accounts backup" in err
    assert sha(store.path) == before


def test_restore_current_blocked_by_rounds(tmp_path, password, server_env):
    data = tmp_path / "data"
    load_upgrade_fixture(data, "v1")  # has a queued round r1
    store = Store(data)
    auth.create_user(store, "alice", password)
    name = store.backup()["name"]
    before = sha(store.path)
    code, out, err = cli(data, "restore", name)
    assert code == 1
    assert "r1" in out and admin.BLOCKED_HINT in err
    assert sha(store.path) == before
    # The offline path does not check rounds.
    code, out, err = cli(data, "restore", name, "--accounts", "backup")
    assert code == 0, err


def test_restore_backup_accounts_from_a_broken_database(tmp_path, password, server_env):
    data, store, name = setup_restore(tmp_path, password)
    cfg = load({"LEAD_MODE": "server", "LEAD_PUBLIC_ORIGIN": "https://lw.test"})
    with closing(sqlite3.connect(data / "backups" / name)) as con:
        assert con.execute("SELECT count(*) FROM auth_users").fetchone()[0] == 1
    auth.create_session(store, auth.verify_login(store, "alice", password)["id"], auth.utcnow(), cfg)
    garbage = os.urandom(8192)
    for suffix in ("-wal", "-shm"):
        (data / f"workbench.sqlite3{suffix}").unlink(missing_ok=True)
    (data / "workbench.sqlite3").write_bytes(garbage)
    code, out, err = cli(data, "restore", name, "--accounts", "backup")
    assert code == 0, err
    assert "1 个账号" in out and "alice" not in out
    damaged = list((data / "backups").glob("damaged-*"))
    assert len(damaged) == 1 and (damaged[0] / "workbench.sqlite3").read_bytes() == garbage
    restored = Store(data)
    assert [u["username"] for u in auth.list_users(restored)] == ["alice"]
    with restored.connect() as con:
        assert con.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 0


def test_restore_backup_without_auth_tables(tmp_path, password, server_env):
    data = tmp_path / "data"
    Store(data)
    legacy = load_upgrade_fixture(tmp_path / "legacy", "v1")
    (data / "backups").mkdir(exist_ok=True)
    (data / "backups" / "legacy.sqlite3").write_bytes(legacy.read_bytes())
    code, out, err = cli(data, "restore", "legacy.sqlite3", "--accounts", "backup")
    assert code == 0, err
    assert "create-user" in out
    with closing(sqlite3.connect(data / "workbench.sqlite3")) as con:
        assert con.execute("SELECT count(*) FROM auth_users").fetchone()[0] == 0
        assert con.execute("SELECT 1 FROM schema_migrations WHERE id='aws_20261021_auth'").fetchone()
    assert list((data / "backups").glob("auto-pre-restore-*.sqlite3"))


@pytest.mark.parametrize("accounts", ["current", "backup"])
def test_restore_rejects_bad_backups(tmp_path, password, server_env, accounts):
    data, store, name = setup_restore(tmp_path, password)
    (data / "backups" / "broken.sqlite3").write_bytes(b"garbage" * 1000)
    os.symlink(data / "backups" / name, data / "backups" / "link.sqlite3")
    before = sha(store.path)
    for bad in ("../x.sqlite3", "missing.sqlite3", "link.sqlite3", "broken.sqlite3", "x.txt"):
        code, _, err = cli(data, "restore", bad, "--accounts", accounts)
        assert code == 1, (bad, err)
    assert sha(store.path) == before


def test_local_restore_needs_confirmation(tmp_path, password):
    data, store, name = setup_restore(tmp_path, password)
    code, out, err = cli(data, "restore", name)
    assert code == 2 and "请确认已停止 scripts/start.sh" in out and "--yes" in err
    code, out, err = cli(data, "restore", name, "--yes")
    assert code == 0, err
