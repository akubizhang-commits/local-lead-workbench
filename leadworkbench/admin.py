"""Administrator command line (AWS trial, design §2.8).

    python -m leadworkbench.admin [--data-dir DIR] create-user <用户名>
    ... reset-password <用户名> | disable-user <用户名> | enable-user <用户名> | list-users
    ... logout-all [<用户名>] | backup | restore <备份文件名> [--accounts current|backup] [--yes]

Passwords are read with getpass (twice) on a TTY, otherwise one line from stdin.
Never writes log files and never prints passwords, hashes or account contents.
Exit codes: 0 success, 1 recoverable error, 2 invalid arguments or input.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import getpass
import logging
from pathlib import Path
import sqlite3
import sys
import unicodedata

from . import auth, config
from .worker import LockTimeout, worker_lock

AUTH_LOCK_SECONDS = 30
PROCESS_LOCK_SECONDS = 2
STOP_API = "请先停止 api：docker compose stop api worker"
STOP_WORKER = "请先停止 worker：docker compose stop worker"
BLOCKED_HINT = ("请先执行 docker compose up -d，在网页“轮次”中暂停以上轮次，再执行 docker compose stop api worker 后重试；"
                "或改用 --accounts backup（会使用备份中的账号）")
OPEN_FAILED = "无法打开当前数据库（{name}），无法保留当前账号；如需继续，请加 --accounts backup"
AUTH_UNREADABLE = "无法读取当前账号；如需继续，请加 --accounts backup（将使用备份里的账号）"
PASSWORD_ATTEMPTS = 3


class Exit(Exception):
    def __init__(self, code, message=""):
        super().__init__(message)
        self.code = code
        self.message = message


def out(message):
    print(message, file=sys.stdout, flush=True)


def read_password(username):
    """A valid new password, re-asked up to three times."""
    for _ in range(PASSWORD_ATTEMPTS):
        if sys.stdin.isatty():
            first = getpass.getpass("新密码：")
            second = getpass.getpass("再次输入新密码：")
            if first != second:
                print("两次输入的密码不一致", file=sys.stderr)
                continue
        else:
            first = sys.stdin.readline().rstrip("\r\n")
        try:
            return auth.validate_new_password(first, username)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
    raise Exit(2, "密码不符合要求，已放弃")


def open_store(directory):
    from .db import Store
    try:
        return Store(directory)
    except Exception as exc:  # any failure to open: tell the class name only
        raise Exit(1, f"无法打开数据库：{type(exc).__name__}") from exc


class _AuthWrite:
    """auth.lock for write commands; times out with the restore message."""

    def __init__(self, directory):
        self.stack = ExitStack()
        self.directory = directory

    def __enter__(self):
        try:
            self.stack.enter_context(auth.auth_lock(self.directory, wait_seconds=AUTH_LOCK_SECONDS))
        except LockTimeout:
            raise Exit(1, auth.RESTORING) from None
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def cmd_create_user(store, args):
    try:
        username = auth.validate_username(args.username)
    except ValueError as exc:
        raise Exit(2, str(exc)) from None
    if auth.user_known(store, username):
        raise Exit(1, "账号已存在")
    password = read_password(username)
    with _AuthWrite(store.directory):
        try:
            auth.create_user(store, username, password)
        except auth.UserExists:
            raise Exit(1, "账号已存在") from None
    out(f"已创建账号 {username}")


def cmd_reset_password(store, args):
    if not auth.user_known(store, args.username):
        raise Exit(1, "账号不存在")
    password = read_password(unicodedata.normalize("NFKC", args.username))
    with _AuthWrite(store.directory):
        try:
            auth.set_password(store, args.username, password)
        except auth.UserMissing:
            raise Exit(1, "账号不存在") from None
    out(f"已重置账号 {args.username} 的密码，该账号已在所有设备上退出登录")


def cmd_set_disabled(store, args, disabled):
    with _AuthWrite(store.directory):
        try:
            auth.set_disabled(store, args.username, disabled)
        except auth.UserMissing:
            raise Exit(1, "账号不存在") from None
    out(f"已停用账号 {args.username}，该账号已退出登录" if disabled else f"已启用账号 {args.username}")


def cmd_list_users(store, args):
    users = auth.list_users(store)
    if not users:
        out("还没有账号，请用 create-user 创建")
        return
    out("用户名\t状态\t创建时间\t最后登录")
    for user in users:
        out(f"{user['username']}\t{'已停用' if user['disabled'] else '正常'}\t{user['created_at']}\t"
            f"{user['last_login_at'] or '-'}")


def cmd_logout_all(store, args):
    with _AuthWrite(store.directory):
        try:
            count = auth.delete_user_sessions(store, args.username)
        except auth.UserMissing:
            raise Exit(1, "账号不存在") from None
    out(f"已强制退出 {count} 个登录会话")


def cmd_backup(store, args):
    try:
        out(store.backup()["name"])
    except (sqlite3.Error, OSError) as exc:
        raise Exit(1, f"备份失败：{type(exc).__name__}") from exc


def hold_process_locks(stack, directory):
    """api.lock, worker-single.lock and worker.lock, held until the restore ends (§2.8.1)."""
    for filename, message in (("api.lock", STOP_API), ("worker-single.lock", STOP_WORKER),
                              ("worker.lock", STOP_WORKER)):
        try:
            stack.enter_context(worker_lock(directory, filename, wait_seconds=PROCESS_LOCK_SECONDS))
        except LockTimeout:
            raise Exit(1, message) from None


def confirm_local(args):
    out("请确认已停止 scripts/start.sh")
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise Exit(2, "非交互环境请加 --yes")
    if input("输入 yes 继续：").strip() != "yes":
        raise Exit(1, "已取消")


def cmd_restore(directory, cfg, args):
    from .db import Conflict, Invalid, RestoreSaveFailed, restore_offline
    with ExitStack() as stack:
        hold_process_locks(stack, directory)
        if not cfg.server:
            confirm_local(args)
        if args.accounts == "backup":
            try:
                result = restore_offline(directory, args.name)
            except (Invalid, RestoreSaveFailed) as exc:
                raise Exit(1, str(exc)) from None
            out(f"已从 {args.name} 恢复；账号使用备份中的数据（{result['users']} 个账号）")
            if not result["users"]:
                out("备份中没有账号，请用 create-user 创建账号")
            return
        from .db import Store
        try:
            store = Store(directory)
        except Exception as exc:
            raise Exit(1, OPEN_FAILED.format(name=type(exc).__name__)) from exc
        blockers = store.restore_blockers()
        if blockers["runs_total"] or blockers["social_runs_total"]:
            for run in blockers["runs"]:
                name = run.get("task_name") or run.get("batch_market_name") or run.get("company_name") or "-"
                out(f"{run['id']}\t{run['kind']}\t{name}")
            for run in blockers["social_runs"]:
                out(f"{run['id']}\tsocial\t{run.get('task_name') or run.get('company_name') or '-'}")
            raise Exit(1, BLOCKED_HINT)
        try:
            store.restore(args.name)
        except Conflict as exc:
            if exc.code == "restore_auth_unreadable":
                raise Exit(1, AUTH_UNREADABLE) from None
            raise Exit(1, str(exc)) from None
        except Invalid as exc:
            raise Exit(1, str(exc)) from None
        except LockTimeout:
            raise Exit(1, auth.RESTORING) from None
        with store.connect() as con:
            users = auth.counts(con)["users"]
        out(f"已从 {args.name} 恢复；保留当前账号（{users} 个账号）")


def parser():
    root = argparse.ArgumentParser(prog="python -m leadworkbench.admin", description="寻迹线索工作台管理员命令")
    root.add_argument("--data-dir", default=None, help="数据目录（默认 LEAD_DATA_DIR）")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("create-user", "reset-password", "disable-user", "enable-user"):
        commands.add_parser(name).add_argument("username")
    commands.add_parser("list-users")
    commands.add_parser("logout-all").add_argument("username", nargs="?")
    commands.add_parser("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("name")
    restore.add_argument("--accounts", choices=("current", "backup"), default="current")
    restore.add_argument("--yes", action="store_true")
    return root


def run(argv=None):
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    from .serve import data_dir
    directory = Path(args.data_dir).expanduser().resolve() if args.data_dir else data_dir()
    try:
        if args.command == "restore":
            cmd_restore(directory, cfg, args)
            return 0
        store = open_store(directory)
        handlers = {
            "create-user": cmd_create_user, "reset-password": cmd_reset_password,
            "disable-user": lambda s, a: cmd_set_disabled(s, a, True),
            "enable-user": lambda s, a: cmd_set_disabled(s, a, False),
            "list-users": cmd_list_users, "logout-all": cmd_logout_all, "backup": cmd_backup,
        }
        handlers[args.command](store, args)
        return 0
    except Exit as exc:
        if exc.message:
            print(exc.message, file=sys.stderr)
        return exc.code
    except (sqlite3.Error, OSError) as exc:
        print(f"无法打开数据库：{type(exc).__name__}", file=sys.stderr)
        return 1


def main():
    # No log files and no tracebacks on the console: messages above are the whole output.
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    raise SystemExit(run())


if __name__ == "__main__":
    main()
