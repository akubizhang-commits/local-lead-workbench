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


CATEGORIES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS company_categories(
        company_id TEXT NOT NULL REFERENCES companies(id), category_key TEXT NOT NULL, category TEXT NOT NULL,
        source_url TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        PRIMARY KEY(company_id, category_key))""",
    "CREATE INDEX IF NOT EXISTS company_categories_key ON company_categories(category_key, company_id)",
    """CREATE TABLE IF NOT EXISTS company_type_hints(
        company_id TEXT NOT NULL REFERENCES companies(id), business_type TEXT NOT NULL,
        market_id TEXT NOT NULL REFERENCES tasks(id), created_at TEXT NOT NULL,
        PRIMARY KEY(company_id, business_type, market_id))""",
    """CREATE TABLE IF NOT EXISTS exclusion_rules(
        id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('global','market')),
        market_id TEXT REFERENCES tasks(id), kind TEXT NOT NULL CHECK(kind IN ('category','keyword')),
        value TEXT NOT NULL, value_key TEXT NOT NULL, created_at TEXT NOT NULL,
        CHECK((scope='global' AND market_id IS NULL) OR (scope='market' AND market_id IS NOT NULL)))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS exclusion_rules_unique
        ON exclusion_rules(scope, COALESCE(market_id,''), kind, value_key)""",
]
# Market company types -> initial (unverified) business type hints.
TYPE_HINTS = {"wholesale": "wholesaler", "retail": "retailer", "import": "importer"}


def _categories(store, con):
    import json
    from .market_store import normalized
    for statement in CATEGORIES_SCHEMA:
        con.execute(statement)
    grouped = {}
    for row in con.execute("""SELECT company_id,value,source_url,observed_at FROM observations
            WHERE field='category' AND method='maps' ORDER BY observed_at,rowid"""):
        key = normalized(row["value"])
        if not key:
            continue
        item = grouped.setdefault((row["company_id"], key), [row["value"], row["source_url"], row["observed_at"],
                                                             row["observed_at"]])
        if row["observed_at"] >= item[3]:
            item[0], item[1], item[3] = row["value"], row["source_url"], row["observed_at"]
    con.executemany("INSERT OR IGNORE INTO company_categories VALUES(?,?,?,?,?,?)",
                    [(cid, key, v[0], v[1] or "", v[2], v[3]) for (cid, key), v in grouped.items()])
    stamp = now()
    hints = []
    for task in con.execute("SELECT id,company_types FROM tasks").fetchall():
        try:
            types = json.loads(task["company_types"] or "[]")
        except ValueError:
            continue
        for kind in types if isinstance(types, list) else []:
            if kind in TYPE_HINTS:
                hints += [(row[0], TYPE_HINTS[kind], task["id"], stamp) for row in con.execute(
                    "SELECT company_id FROM market_memberships WHERE market_id=?", (task["id"],))]
    con.executemany("INSERT OR IGNORE INTO company_type_hints VALUES(?,?,?,?)", hints)


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
        log.info("P0：%d 条旧版待审核联系人冲突保持原样，等待用户在审核页处理", legacy,
                 extra={"event": "legacy_conflicts_kept"})


REPAIR_CANDIDATES = """SELECT r.*,c.review_status contact_status FROM reviews r JOIN contacts c ON c.id=r.new_value
    WHERE r.kind='contact' AND r.status='reject' AND COALESCE(r.old_value,'') NOT IN ('','{}')
    AND c.review_status='reject'
    AND NOT EXISTS(SELECT 1 FROM contact_change_proposals p WHERE p.review_id=r.id)
    AND r.rowid=(SELECT max(r2.rowid) FROM reviews r2 WHERE r2.kind='contact' AND r2.new_value=r.new_value)
    ORDER BY r.created_at,r.rowid"""


def _repair_rejected(store, con):
    """D2=A: restore contacts that the old "标记为不采用" excluded together with a rejected change (§3.6)."""
    from .db import MATERIAL_KEYS, encode, proposal_key, uid
    candidates = con.execute(REPAIR_CANDIDATES).fetchall()
    if not candidates:
        return
    try:
        backup = store.backup()["name"]
    except Exception as exc:
        raise SkipMigration(f"修复前备份失败：{exc}") from exc
    stamp = now()
    for review in candidates:
        contact_id = review["new_value"]
        current = con.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        store._restore_contact(con, contact_id, store._pre_proposal_snapshot(con, review, current))
        before = con.execute("""SELECT old_value FROM changes WHERE field='contact_adoption' AND company_id=?
            AND json_extract(new_value,'$.contact_id')=? AND json_extract(new_value,'$.adoption_status')='excluded'
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (review["company_id"], contact_id)).fetchone()
        prior = before[0] if before else "automatic"
        if prior == "accepted":
            con.execute("INSERT OR REPLACE INTO contact_decisions VALUES(?,'accepted',?)", (contact_id, stamp))
        elif prior != "excluded":
            con.execute("DELETE FROM contact_decisions WHERE contact_id=?", (contact_id,))
        rejected = {k: current[k] or "" for k in MATERIAL_KEYS}
        rejected.update(source_url=current["source_url"] or "", observed_at=current["observed_at"] or "")
        con.execute("INSERT INTO contact_change_proposals VALUES(?,?,?,?,?,?,?,?)",
                    (review["id"], contact_id, encode(rejected), proposal_key(rejected), "rejected", "repair",
                     stamp, stamp))
        con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                    (uid(), review["company_id"], "contact", encode({k: rejected[k] for k in MATERIAL_KEYS}),
                     review["old_value"], stamp,
                     f"数据修复：恢复因‘标记为不采用’被误排除的原联系人（修复前备份 {backup}）"))
    if store._facets_enabled or con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_facets'").fetchone():
        store._refresh_facets_locked(con)
    log.warning("restored %d contacts wrongly excluded by rejected change reviews; backup %s",
                len(candidates), backup, extra={"event": "rejected_contacts_restored"})


def _registry():
    from .contact_tiers import FACET_VERSION
    return [
        ("p0_20261018_indexes", _indexes),
        ("p0_20261018_facets_schema", _facets_schema),
        (f"contact_facets_v{FACET_VERSION}_mark", _facets_mark),
        (f"contact_facets_v{FACET_VERSION}", _facets_fill),
        ("p0_20261018_categories", _categories),
        ("p0_20261018_contact_proposals", _contact_proposals),
        ("p0_20261018_repair_rejected", _repair_rejected),
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
            store._begin(con)
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
                log.warning("P0 迁移 %s 已跳过，未写入台账：%s", id, exc, extra={"event": "migration_skipped", "id": id})
                break
            except BaseException:
                con.rollback()
                log.exception("P0 迁移 %s 失败，未写入台账", id, extra={"event": "migration_failed", "id": id})
                raise
            elapsed = time.perf_counter() - start
            log.info("P0 迁移 %s 用时 %.2fs", id, elapsed,
                     extra={"event": "migration", "id": id, "duration_ms": round(elapsed * 1000)})
            if finished:
                break
    con.execute("PRAGMA optimize")
