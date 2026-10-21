"""Unified migration registry and executor (P1-9, design §3.2).

Every process that opens the database (API, worker, restore) runs ``migrate()``:
inside the cross-process ``{data}/migrate.lock`` it walks REGISTRY in order, one
transaction per entry, on the caller's connection only.

- ``once`` entries (ids ``0001_…``–``0011_…``, the P0 ids and the P1 batch 2 ``p1b_…`` ids) run once per database
  and are recorded in ``schema_migrations``. FREEZE RULE: once an entry has shipped,
  its implementation must never change meaning; schema changes need a new entry.
  (Equivalent rewrites such as explicit INSERT column lists are allowed; the frozen
  upgrade snapshots in tests/fixtures prove the equivalence.)
- ``each`` entries (ids ``R01_…``–``R08_…``) run on every start, are never recorded,
  and behave exactly like the historical start-up back-fills and ``resolve_*`` repairs.

Entry steps take ``(store, con)``, use only ``con`` and never BEGIN/COMMIT themselves.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Literal

log = logging.getLogger(__name__)
LOCK_WAIT_SECONDS = 120
LOCK_RETRY_SECONDS = 0.1
LEDGER = "CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
OPTIONAL = frozenset({"p0_20261018_repair_rejected"})  # may stay unrecorded when its backup failed


class MigrationError(Exception):
    """Schema initialisation failed; the database must not be used."""


class MigrationLockTimeout(MigrationError):
    def __init__(self, message="数据库正在由另一个进程初始化，请稍后重试"):
        super().__init__(message)


class SchemaNotReady(MigrationError):
    def __init__(self, message="数据库尚未完成初始化，请稍后重试"):
        super().__init__(message)


@contextmanager
def migrate_lock(data_dir, *, wait_seconds=None):
    """Exclusive ``{data}/migrate.lock``; LOCK_NB retried every 0.1 s, up to 120 s."""
    wait_seconds = LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    path = Path(data_dir) / "migrate.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        start = time.monotonic()
        logged = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = time.monotonic() - start
                if waited >= wait_seconds:
                    raise MigrationLockTimeout() from None
                if waited > 2 and not logged:
                    logged = True
                    log.info("waiting for migrate.lock", extra={"event": "migration_lock_wait"})
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def statements(sql: str) -> list[str]:
    """Split this repository's own DDL (no trigger bodies, no ';' in literals) into statements."""
    return [part.strip() for part in sql.split(";") if part.strip()]


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---- entry steps ----------------------------------------------------------

def _version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def _base_v1(store, con):
    from .db import SCHEMA
    if _version(con) == 0:
        for statement in statements(SCHEMA):
            con.execute(statement)  # ends with PRAGMA user_version=1


def _market_v2(store, con):
    if _version(con) < 2:
        store._migrate_market_locked(con)


def _maps_access_policy(store, con):
    from .maps_policy import DDL
    for statement in statements(DDL):
        con.execute(statement)


def _website_host_gates(store, con):
    from .website_policy import _migrate_locked
    _migrate_locked(con)


def _market_exhausted_items(store, con):
    con.execute("""CREATE TABLE IF NOT EXISTS market_exhausted_items(
                item_id TEXT PRIMARY KEY REFERENCES work_items(id),reason TEXT NOT NULL)""")


def _method(name):
    def step(store, con):
        return getattr(store, name)(con)
    step.__name__ = name
    return step


@dataclass(frozen=True)
class Entry:
    id: str
    kind: Literal["once", "each"]
    step: Callable


def _registry():
    from .p0_migrations import MIGRATIONS as P0
    from .p1b_migrations import MIGRATIONS as P1B
    from .auth_migrations import MIGRATIONS as AUTH
    return (
        Entry("0001_base_v1", "once", _base_v1),
        Entry("0002_market_v2", "once", _market_v2),
        Entry("0003_maps_access_policy", "once", _maps_access_policy),
        Entry("0004_website_host_gates", "once", _website_host_gates),
        Entry("0005_listing_aliases", "once", _method("_source_aliases_ddl")),
        Entry("R01_listing_aliases_backfill", "each", _method("_source_aliases_backfill")),
        Entry("0006_audit_tables", "once", _method("_audit_ddl")),
        Entry("R02_contact_decisions_backfill", "each", _method("_contact_decisions_backfill")),
        Entry("R03_accepted_overrides_backfill", "each", _method("_accepted_overrides_backfill")),
        Entry("0007_social_tables", "once", _method("_social_ddl")),
        Entry("0008_social_discovery_tables", "once", _method("_social_discovery_ddl")),
        Entry("R04_social_work_missing_website", "each", _method("_social_work_missing_website")),
        Entry("0009_social_legacy_links", "once", _method("_social_legacy_links_locked")),
        Entry("0010_market_social_tables", "once", _method("_market_social_ddl")),
        Entry("0011_market_exhausted_items", "once", _market_exhausted_items),
        *(Entry(id, "once", step) for id, step in P0),
        Entry("R05_resolve_branch_reviews", "each", _method("_resolve_branch_reviews_locked")),
        Entry("R06_resolve_empty_reviews", "each", _method("_resolve_empty_reviews_locked")),
        Entry("R07_resolve_format_reviews", "each", _method("_resolve_format_reviews_locked")),
        Entry("R08_resolve_duplicate_reviews", "each", _method("_resolve_duplicate_reviews_locked")),
        *(Entry(id, "once", step) for id, step in P1B),
        *(Entry(id, "once", step) for id, step in AUTH),
    )


REGISTRY = _registry()
ONCE_IDS = tuple(entry.id for entry in REGISTRY if entry.kind == "once")
REGISTRY_IDS = frozenset(ONCE_IDS)
RETIRED: frozenset[str] = frozenset()  # ids removed from REGISTRY later go here and stay forever
FAMILY = re.compile(r"^contact_facets_v(\d+)(_mark)?$")


def is_known(id: str) -> bool:
    """False for ledger ids written by a newer application version."""
    if id in REGISTRY_IDS or id in RETIRED:
        return True
    from .contact_tiers import FACET_VERSION
    match = FAMILY.match(id)
    return bool(match and int(match.group(1)) <= FACET_VERSION)


def ready(con) -> bool:
    """Every once entry except the optional repair is recorded (export child, migrate=False)."""
    try:
        if _version(con) != 2:
            return False
        done = {row[0] for row in con.execute("SELECT id FROM schema_migrations")}
    except sqlite3.OperationalError:
        return False
    return (REGISTRY_IDS - OPTIONAL) <= done


# ---- executor -------------------------------------------------------------

def migrate(store, con):
    """Bring the database on ``con`` up to date; serialised across processes by migrate.lock."""
    with migrate_lock(store.directory):
        _run(store, con)


def _run(store, con):
    from .errors import Invalid
    from .p0_migrations import Pending, SkipMigration
    if con.in_transaction:
        con.commit()
    if _version(con) > 2:
        raise Invalid("数据库版本高于应用版本")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(LEDGER)
    if con.in_transaction:
        con.commit()
    unknown = sorted(row[0] for row in con.execute("SELECT id FROM schema_migrations") if not is_known(row[0]))
    if unknown:
        # Never lock the user out at start-up; restore refuses such backups instead.
        log.warning("unknown migration ids in ledger: %s", ", ".join(unknown),
                    extra={"event": "unknown_migration_ids"})
    for entry in REGISTRY:
        while True:
            start = time.perf_counter()
            store._begin(con)
            finished = True
            try:
                if entry.kind == "once" and con.execute(
                        "SELECT 1 FROM schema_migrations WHERE id=?", (entry.id,)).fetchone():
                    con.rollback()
                    break
                try:
                    entry.step(store, con)
                except Pending:
                    finished = False
                if finished and entry.kind == "once":
                    con.execute("INSERT INTO schema_migrations(id, applied_at) VALUES(?,?)", (entry.id, _now()))
                con.commit()
            except SkipMigration as exc:
                con.rollback()
                log.warning("迁移 %s 已跳过，未写入台账：%s", entry.id, exc,
                            extra={"event": "migration_skipped", "id": entry.id})
                break
            except BaseException:
                con.rollback()
                elapsed = time.perf_counter() - start
                log.exception("迁移 %s 失败，未写入台账", entry.id,
                              extra={"event": "migration_failed", "id": entry.id,
                                     "duration_ms": round(elapsed * 1000)})
                raise
            elapsed = time.perf_counter() - start
            log.info("迁移 %s 用时 %.2fs", entry.id, elapsed,
                     extra={"event": "migration", "id": entry.id, "kind": entry.kind,
                            "duration_ms": round(elapsed * 1000)})
            if finished:
                break
    con.execute("PRAGMA optimize")
