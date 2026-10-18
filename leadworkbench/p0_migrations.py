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


FACETS_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS contact_facets(
        contact_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, channel_ok INTEGER NOT NULL,
        category TEXT NOT NULL, tier TEXT NOT NULL, tier_rank INTEGER NOT NULL,
        identity_key TEXT NOT NULL, email_key TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS contact_facets_company ON contact_facets(company_id, category, tier_rank)",
    "CREATE INDEX IF NOT EXISTS contact_facets_company_email ON contact_facets(company_id, email_key)",
    "CREATE TABLE IF NOT EXISTS contact_facets_dirty(contact_id TEXT PRIMARY KEY)",
    """CREATE TABLE IF NOT EXISTS manual_contacts(
        contact_id TEXT PRIMARY KEY REFERENCES contacts(id),
        phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS contacts_facets_insert AFTER INSERT ON contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty VALUES(new.id); END""",
    """CREATE TRIGGER IF NOT EXISTS contacts_facets_update
        AFTER UPDATE OF company_id,name,role,email,url,kind,role_status,association_status,evidence ON contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty VALUES(new.id); END""",
    """CREATE TRIGGER IF NOT EXISTS contacts_facets_delete AFTER DELETE ON contacts
        BEGIN DELETE FROM contact_facets WHERE contact_id=old.id;
              DELETE FROM contact_facets_dirty WHERE contact_id=old.id; END""",
    """CREATE TRIGGER IF NOT EXISTS manual_contacts_facets_insert AFTER INSERT ON manual_contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty VALUES(new.contact_id); END""",
    """CREATE TRIGGER IF NOT EXISTS manual_contacts_facets_update AFTER UPDATE ON manual_contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty VALUES(new.contact_id); END""",
]
FACET_BATCH = 5000


def _facets_schema(store, con):
    for statement in FACETS_SCHEMA:
        con.execute(statement)


def _facets_mark(store, con):
    con.execute("INSERT OR IGNORE INTO contact_facets_dirty SELECT id FROM contacts")


def _facets_fill(store, con):
    # The dirty table is the resume point; each batch is its own transaction.
    store._refresh_facets_locked(con, limit=FACET_BATCH)
    if con.execute("SELECT 1 FROM contact_facets_dirty LIMIT 1").fetchone():
        raise Pending()


PROPOSALS_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS contact_change_proposals(
        review_id TEXT PRIMARY KEY REFERENCES reviews(id),
        contact_id TEXT NOT NULL REFERENCES contacts(id),
        proposal TEXT NOT NULL, proposal_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','superseded')),
        origin TEXT NOT NULL DEFAULT 'proposal' CHECK(origin IN ('proposal','legacy','repair')),
        created_at TEXT NOT NULL, decided_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS contact_proposals_contact ON contact_change_proposals(contact_id, proposal_key, status)",
]


def _contact_proposals(store, con):
    # Table only: legacy pending conflicts are converted when the user decides (design §3.5).
    for statement in PROPOSALS_SCHEMA:
        con.execute(statement)
    legacy = con.execute("""SELECT count(*) FROM reviews WHERE kind='contact' AND status='pending'
        AND COALESCE(old_value,'') NOT IN ('','{}')""").fetchone()[0]
    if legacy:
        log.info("P0：%d 条旧版待审核联系人冲突保持原样，等待用户在审核页处理", legacy)


def _registry():
    from .contact_tiers import FACET_VERSION
    return [
        ("p0_20261018_indexes", _indexes),
        ("p0_20261018_facets_schema", _facets_schema),
        (f"contact_facets_v{FACET_VERSION}_mark", _facets_mark),
        (f"contact_facets_v{FACET_VERSION}", _facets_fill),
        ("p0_20261018_contact_proposals", _contact_proposals),
    ]


MIGRATIONS = _registry()


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
