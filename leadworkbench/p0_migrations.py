"""Forward-only, idempotent, ledgered P0 migrations (design §1.1).

Each item runs as ``BEGIN IMMEDIATE -> ledger check -> work -> ledger row -> COMMIT``
so two processes starting at once never apply an item twice. Items never change
existing rows of the legacy tables except the user-approved repair item.
``user_version`` stays 2: all new tables are side tables, triggers are plain SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

log = logging.getLogger(__name__)

LEDGER = "CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"

INDEXES = """
CREATE INDEX IF NOT EXISTS contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS contacts_company_email ON contacts(company_id, lower(trim(email)));
CREATE INDEX IF NOT EXISTS contact_versions_contact ON contact_versions(contact_id, observed_at);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status, created_at);
CREATE INDEX IF NOT EXISTS reviews_company_field ON reviews(company_id, kind, field, status);
CREATE INDEX IF NOT EXISTS reviews_contact ON reviews(kind, new_value, status);
CREATE INDEX IF NOT EXISTS locations_company ON locations(company_id);
CREATE INDEX IF NOT EXISTS changes_company ON changes(company_id, created_at);
CREATE INDEX IF NOT EXISTS observations_company_field ON observations(company_id, field, observed_at);
CREATE INDEX IF NOT EXISTS companies_newest ON companies(created_at DESC, id);
CREATE INDEX IF NOT EXISTS companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS listings_company ON listings(company_id);
CREATE INDEX IF NOT EXISTS work_items_target ON work_items(target, stage)
"""


class SkipMigration(Exception):
    """Recoverable: roll back this item, leave it unrecorded, continue with the rest."""


class Pending(Exception):
    """Batch item committed one batch but is not finished; run another batch."""


def now():
    return datetime.now(timezone.utc).isoformat()


def _statements(script):
    return [s.strip() for s in script.split(";") if s.strip()]


def _indexes(store, con):
    for statement in _statements(INDEXES):
        con.execute(statement)
    con.execute("ANALYZE")


MIGRATIONS = [
    ("p0_20261018_indexes", _indexes),
]


def migrate_p0(store, con):
    if con.in_transaction:
        con.commit()
    con.execute(LEDGER)
    if con.in_transaction:
        con.commit()
    for id, step in MIGRATIONS:
        while True:
            start = time.perf_counter()
            con.execute("BEGIN IMMEDIATE")
            try:
                if con.execute("SELECT 1 FROM schema_migrations WHERE id=?", (id,)).fetchone():
                    con.rollback()
                    break
                finished = True
                try:
                    step(store, con)
                except Pending:
                    finished = False
                if finished:
                    con.execute("INSERT INTO schema_migrations VALUES(?,?)", (id, now()))
                con.commit()
            except SkipMigration as exc:
                con.rollback()
                log.warning("P0 迁移 %s 已跳过，未写入台账：%s", id, exc)
                break
            except BaseException:
                con.rollback()
                log.exception("P0 迁移 %s 失败，未写入台账", id)
                raise
            log.info("P0 迁移 %s 用时 %.2fs", id, time.perf_counter() - start)
            if finished:
                break
    con.execute("PRAGMA optimize")
