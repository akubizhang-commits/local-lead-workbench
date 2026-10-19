"""P1 batch 2 migrations (design §3): forward-only ``once`` entries of the unified registry.

They only create side tables, indexes and triggers, insert rows into them, and
(schedule_dedupe) set ``schedules.enabled=0`` on duplicate enabled plans of one
market with an audit row each. FREEZE RULE as in migrations.py.
"""
from __future__ import annotations

import json
import logging
import uuid

from .p0_migrations import Pending, now

log = logging.getLogger(__name__)

MARKET_ADMIN = [
    """CREATE TABLE IF NOT EXISTS task_state(
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), archived_at TEXT, updated_at TEXT NOT NULL)""",
    # Audit rows; no foreign key on purpose: legacy plans may point at markets that no longer exist.
    """CREATE TABLE IF NOT EXISTS market_changes(
        id TEXT PRIMARY KEY, market_id TEXT NOT NULL, field TEXT NOT NULL,
        old_value TEXT, new_value TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS market_changes_market ON market_changes(market_id, created_at)",
    """CREATE TABLE IF NOT EXISTS market_recheck_runs(
        run_id TEXT PRIMARY KEY REFERENCES runs(id), market_id TEXT NOT NULL REFERENCES tasks(id),
        requested INTEGER NOT NULL, skipped TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS market_recheck_market ON market_recheck_runs(market_id, created_at)",
]
DUPLICATE_SCHEDULES = """SELECT id, task_id, interval_months FROM (
    SELECT id, task_id, interval_months,
           row_number() OVER (PARTITION BY task_id ORDER BY anchor IS NULL, anchor, rowid) AS n
    FROM schedules WHERE enabled=1 AND task_id IS NOT NULL)
    WHERE n > 1"""
ONE_ENABLED_INDEX = """CREATE UNIQUE INDEX IF NOT EXISTS schedules_one_enabled_market
    ON schedules(task_id) WHERE enabled=1 AND task_id IS NOT NULL"""
DEDUPE_REASON = "升级：同一市场只保留最早的启用计划，此计划已停用（未删除）"
ROUND_SUMMARIES = [
    """CREATE TABLE IF NOT EXISTS market_round_baselines(
        run_id TEXT NOT NULL REFERENCES runs(id), company_id TEXT NOT NULL REFERENCES companies(id),
        rank INTEGER NOT NULL, PRIMARY KEY(run_id, company_id))""",
    """CREATE TABLE IF NOT EXISTS market_round_summaries(
        run_id TEXT PRIMARY KEY REFERENCES runs(id), market_id TEXT NOT NULL REFERENCES tasks(id),
        payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS market_round_summaries_market ON market_round_summaries(market_id, created_at)",
]
REVIEW_RESOLUTIONS = [
    """CREATE TABLE IF NOT EXISTS review_resolutions(
        review_id TEXT PRIMARY KEY REFERENCES reviews(id), previous_status TEXT NOT NULL,
        status TEXT NOT NULL, rule TEXT NOT NULL, batch_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
]
COMPANY_CITIES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS company_cities(
        company_id TEXT PRIMARY KEY REFERENCES companies(id), city TEXT NOT NULL, city_key TEXT NOT NULL,
        target_keys TEXT NOT NULL, target_scope TEXT NOT NULL, nearby_global INTEGER NOT NULL,
        parsed INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS company_cities_key ON company_cities(city_key, company_id)",
    "CREATE TABLE IF NOT EXISTS company_cities_dirty(company_id TEXT PRIMARY KEY)",
    """CREATE TRIGGER IF NOT EXISTS companies_city_insert AFTER INSERT ON companies
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.id); END""",
    """CREATE TRIGGER IF NOT EXISTS companies_city_update AFTER UPDATE OF address,country,city ON companies
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.id); END""",
    # target_scope depends on the market that first found the company.
    """CREATE TRIGGER IF NOT EXISTS memberships_city_insert AFTER INSERT ON market_memberships
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.company_id); END""",
]
CITY_BATCH = 5000


def _run(statements):
    def step(store, con):
        for statement in statements:
            con.execute(statement)
    return step


def _schedule_dedupe(store, con):
    rows = con.execute(DUPLICATE_SCHEDULES).fetchall()
    stamp = now()
    for row in rows:
        con.execute("UPDATE schedules SET enabled=0 WHERE id=?", (row["id"],))
        con.execute("""INSERT INTO market_changes(id,market_id,field,old_value,new_value,reason,created_at)
            VALUES(?,?,?,?,?,?,?)""", (
            uuid.uuid4().hex, row["task_id"], "schedule",
            json.dumps({"schedule_id": row["id"], "enabled": True, "interval_months": row["interval_months"]},
                       ensure_ascii=False),
            json.dumps({"enabled": False}), DEDUPE_REASON, stamp))
    if rows:
        log.warning("disabled %d duplicate enabled market schedules", len(rows),
                    extra={"event": "schedule_dedupe", "count": len(rows)})
    con.execute(ONE_ENABLED_INDEX)


def _cities_mark(store, con):
    con.execute("INSERT OR IGNORE INTO company_cities_dirty(company_id) SELECT id FROM companies")


def _cities_fill(store, con):
    # The dirty table is the resume point; each batch is its own transaction.
    store._refresh_cities_locked(con, limit=CITY_BATCH)
    if con.execute("SELECT 1 FROM company_cities_dirty LIMIT 1").fetchone():
        raise Pending()


MIGRATIONS = [
    ("p1b_20261019_market_admin", _run(MARKET_ADMIN)),
    ("p1b_20261019_schedule_dedupe", _schedule_dedupe),
    ("p1b_20261019_round_summaries", _run(ROUND_SUMMARIES)),
    ("p1b_20261019_review_resolutions", _run(REVIEW_RESOLUTIONS)),
    ("p1b_20261019_company_cities_schema", _run(COMPANY_CITIES_SCHEMA)),
    ("p1b_20261019_company_cities_mark", _cities_mark),
    ("p1b_20261019_company_cities_fill", _cities_fill),
]
