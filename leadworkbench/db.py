"""Transactional business store. Source observations never overwrite user edits."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import calendar
import hashlib
import json
import logging
import re
from pathlib import Path
import shutil
import sqlite3
import uuid
from zoneinfo import ZoneInfo
from .run_activity import RunActivityMixin
from .market_store import MarketStoreMixin
from .social_store import SocialStoreMixin
from .market_social import MarketSocialMixin
from .contact_projection import group_contact_records, contact_key
from .company_comparison import website_key, brief, distinct_branches, branch_identity, equivalent_format

UTC = timezone.utc
log = logging.getLogger(__name__)


def _remove_database_files(path: Path, *, include_main=True):
    for suffix in ("", "-wal", "-shm") if include_main else ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Conflict(ValueError):
    pass


def calendar_next(anchor: datetime, after: datetime, months: int, zone: str):
    """First calendar occurrence after `after`, preserving the original day."""
    from dateutil.tz import resolve_imaginary
    tz = ZoneInfo(zone)
    local = anchor.astimezone(tz)
    cursor = after.astimezone(tz)
    elapsed = (cursor.year - local.year) * 12 + cursor.month - local.month
    n = max(1, elapsed // months)
    while True:
        index = local.year * 12 + local.month - 1 + n * months
        year, month0 = divmod(index, 12)
        day = min(local.day, calendar.monthrange(year, month0 + 1)[1])
        candidate = resolve_imaginary(local.replace(year=year, month=month0 + 1, day=day, fold=0))
        if candidate.astimezone(UTC) > after.astimezone(UTC):
            return candidate.astimezone(UTC)
        n += 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,address TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',
 website TEXT NOT NULL DEFAULT '',city TEXT NOT NULL DEFAULT '',country TEXT NOT NULL DEFAULT 'US',
 business_types TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'candidate',notes TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1,source_kind TEXT NOT NULL,created_at TEXT NOT NULL,last_checked_at TEXT,
 overrides TEXT NOT NULL DEFAULT '[]',social_links TEXT NOT NULL DEFAULT '[]',merged_into TEXT
);
CREATE TABLE IF NOT EXISTS listings(
 source_key TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_url TEXT NOT NULL,
 first_seen TEXT NOT NULL,last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_key TEXT UNIQUE,
 address TEXT,latitude REAL,longitude REAL,source_url TEXT,observed_at TEXT
);
CREATE TABLE IF NOT EXISTS observations(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),field TEXT NOT NULL,value TEXT NOT NULL,
 source_url TEXT NOT NULL,evidence TEXT NOT NULL,observed_at TEXT NOT NULL,method TEXT NOT NULL,
 run_id TEXT,query TEXT,fingerprint TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS contacts(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),name TEXT,role TEXT,email TEXT,url TEXT,
 kind TEXT,role_status TEXT,association_status TEXT,source_url TEXT,evidence TEXT,observed_at TEXT,
 review_status TEXT NOT NULL DEFAULT 'pending',fingerprint TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS changes(
 id TEXT PRIMARY KEY,company_id TEXT REFERENCES companies(id),field TEXT,old_value TEXT,new_value TEXT,
 created_at TEXT,reason TEXT
);
CREATE TABLE IF NOT EXISTS reviews(
 id TEXT PRIMARY KEY,company_id TEXT REFERENCES companies(id),kind TEXT,field TEXT,old_value TEXT,new_value TEXT,
 source_url TEXT,evidence TEXT,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT,fingerprint TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY,name TEXT,country TEXT,state TEXT,city TEXT,keywords TEXT,company_types TEXT,
 "limit" INTEGER,auto_enrich INTEGER,created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),kind TEXT,company_id TEXT,status TEXT,progress TEXT,
 message TEXT NOT NULL DEFAULT '',error TEXT,created_at TEXT,finished_at TEXT,snapshot TEXT
);
CREATE TABLE IF NOT EXISTS work_items(
 id TEXT PRIMARY KEY,run_id TEXT REFERENCES runs(id),stage TEXT,target TEXT,state TEXT,result TEXT,
 UNIQUE(run_id,stage,target)
);
CREATE TABLE IF NOT EXISTS schedules(
 id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),company_id TEXT REFERENCES companies(id),kind TEXT,
 interval_months INTEGER,timezone TEXT,next_due_at TEXT,enabled INTEGER,anchor TEXT
);
CREATE TABLE IF NOT EXISTS occurrences(
 schedule_id TEXT,due_at TEXT,run_id TEXT,PRIMARY KEY(schedule_id,due_at)
);
CREATE INDEX IF NOT EXISTS observations_company ON observations(company_id,observed_at);
CREATE INDEX IF NOT EXISTS work_items_run ON work_items(run_id,state);
PRAGMA user_version=1;
"""


class Store(MarketSocialMixin, RunActivityMixin, MarketStoreMixin, SocialStoreMixin):
    def __init__(self, directory: Path | str):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "workbench.sqlite3"
        with self.connect() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("数据库版本高于应用版本")
            con.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                con.executescript(SCHEMA)
            if version < 2:
                self._migrate_market(con)
            self._migrate_audit(con)
            self._migrate_social(con)
            self._migrate_market_social(con)
        self.path.chmod(0o600)
        self.resolve_branch_reviews()
        self.resolve_empty_reviews()
        self.resolve_format_reviews()
        self.resolve_duplicate_reviews()

    def _migrate_audit(self, con):
        from .maps_policy import DDL
        con.executescript(DDL)
        from .website_policy import migrate as migrate_website_policy
        migrate_website_policy(con)
        self._migrate_source_aliases(con)
        con.executescript("""
            CREATE TABLE IF NOT EXISTS task_metadata(
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                created_by_user_id TEXT DEFAULT NULL);
            CREATE TABLE IF NOT EXISTS task_geography(
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contact_decisions(
                contact_id TEXT PRIMARY KEY REFERENCES contacts(id),
                adoption_status TEXT NOT NULL,decided_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contact_versions(
                id TEXT PRIMARY KEY,contact_id TEXT NOT NULL REFERENCES contacts(id),
                payload TEXT NOT NULL,observed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_manual_checks(
                company_id TEXT PRIMARY KEY REFERENCES companies(id),checked_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_evidence(
                review_id TEXT NOT NULL REFERENCES reviews(id),
                fingerprint TEXT NOT NULL UNIQUE,
                source_url TEXT,evidence TEXT,observed_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS review_evidence_review ON review_evidence(review_id);
        """)
        # Backfill decisions, including exclusions superseded by later observations.
        con.execute("""INSERT OR IGNORE INTO contact_decisions
            SELECT c.id,CASE r.status WHEN 'reject' THEN 'excluded' ELSE 'accepted' END,r.created_at
            FROM contacts c JOIN reviews r ON r.new_value=c.id AND r.kind='contact'
            WHERE r.status IN ('accept','reject') AND r.rowid=(
                SELECT max(r2.rowid) FROM reviews r2 WHERE r2.new_value=c.id
                AND r2.kind='contact' AND r2.status IN ('accept','reject'))""")
        con.execute("""INSERT OR IGNORE INTO contact_decisions
            SELECT id,CASE review_status WHEN 'reject' THEN 'excluded' ELSE 'accepted' END,observed_at
            FROM contacts WHERE review_status IN ('accept','reject')""")
        # Adopted legacy values gain the same protection if still current.
        for review in con.execute("""SELECT * FROM reviews WHERE kind='field_update' AND status='accept'
                                      ORDER BY created_at,rowid""").fetchall():
            field = review["field"]
            if field not in {"name", "phone", "address", "website", "business_types"}:
                continue
            company = con.execute("SELECT * FROM companies WHERE id=?", (review["company_id"],)).fetchone()
            if company and company[field] == review["new_value"]:
                overrides = set(json.loads(company["overrides"])) | {field}
                con.execute("UPDATE companies SET overrides=? WHERE id=?", (encode(sorted(overrides)), company["id"]))

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def row(row):
        if row is None:
            raise KeyError("记录不存在")
        result = dict(row)
        for key in ("business_types", "overrides", "social_links", "keywords", "company_types", "progress", "snapshot"):
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        for key in ("enabled", "auto_enrich"):
            if key in result:
                result[key] = bool(result[key])
        return result

    def companies(self, q="", status="", type="", *, max_rows=None, market_id="", run_id="", segment="", contact="", sort="newest"):
        return super().companies(q, status, type, max_rows=max_rows, market_id=market_id,
                                 run_id=run_id, segment=segment, contact=contact, sort=sort)

    def resolve_empty_reviews(self):
        with self.connect() as con:
            count = 0
            for r in con.execute("SELECT * FROM reviews WHERE status='pending' ORDER BY created_at DESC,rowid DESC").fetchall():
                if r["old_value"] not in (None, "", "[]"):
                    continue
                if r["kind"] == "contact":
                    contact = con.execute("SELECT * FROM contacts WHERE id=? AND company_id=?", (r["new_value"],r["company_id"])).fetchone()
                    if not contact:
                        continue
                    conflict = con.execute("SELECT 1 FROM reviews WHERE kind='contact' AND new_value=? AND status='pending' AND COALESCE(old_value,'')!=''", (r["new_value"],)).fetchone()
                    if contact["review_status"] == "pending" and not conflict:
                        con.execute("UPDATE contacts SET review_status='auto_recorded' WHERE id=?",(contact["id"],))
                elif r["kind"] == "field_update" and r["field"] in {"name","phone","address","website","business_types"}:
                    field = r["field"]
                    row = con.execute(f"SELECT {field} FROM companies WHERE id=?",(r["company_id"],)).fetchone()
                    if not row or not r["new_value"]:
                        continue
                    if row[0] in (None,"","[]"):
                        con.execute(f"UPDATE companies SET {field}=?,revision=revision+1 WHERE id=?",(r["new_value"],r["company_id"]))
                        con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",(uid(),r["company_id"],field,row[0],r["new_value"],now(),"自动补全旧空字段"))
                    elif row[0] != r["new_value"]:
                        continue
                else:
                    continue
                con.execute("UPDATE reviews SET status='auto_recorded' WHERE id=?",(r["id"],))
                count += 1
        return count

    def resolve_format_reviews(self):
        with self.connect() as con:
            count = 0
            for review in con.execute("SELECT * FROM reviews WHERE kind='field_update' AND status='pending'").fetchall():
                if equivalent_format(review["field"], review["old_value"], review["new_value"]):
                    con.execute("UPDATE reviews SET status='auto_recorded',evidence=COALESCE(evidence,'') || ? WHERE id=?",
                                ("；格式等价，保留现有值，无需审核", review["id"]))
                    count += 1
        return count

    def resolve_duplicate_reviews(self):
        """Idempotently fold legacy same-value pending field reviews into one review each."""
        from .market_store import semantic_value
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""INSERT OR IGNORE INTO review_evidence
                SELECT r.id,COALESCE(r.fingerprint,r.id),r.source_url,r.evidence,COALESCE(r.created_at,'')
                FROM reviews r WHERE r.kind='field_update'
                AND NOT EXISTS (SELECT 1 FROM review_evidence e WHERE e.review_id=r.id)""")
            groups = {}
            for r in con.execute("""SELECT * FROM reviews WHERE kind='field_update' AND status='pending'
                                    ORDER BY created_at,rowid""").fetchall():
                groups.setdefault((r["company_id"], r["field"], semantic_value(r["field"], r["new_value"])), []).append(r)
            closed = 0
            for (company_id, field, wanted), rows in groups.items():
                primary, *others = rows
                for other in others:
                    con.execute("UPDATE review_evidence SET review_id=? WHERE review_id=?", (primary["id"], other["id"]))
                    con.execute("UPDATE reviews SET status='auto_closed',evidence=COALESCE(evidence,'') || ? WHERE id=?",
                                ("；与同值建议合并", other["id"]))
                    closed += 1
                if field not in {"name", "phone", "address", "website", "business_types"}:
                    continue
                row = con.execute(f"SELECT {field} FROM companies WHERE id=?", (company_id,)).fetchone()
                if not row:
                    continue
                if semantic_value(field, row[0]) == wanted:
                    con.execute("UPDATE reviews SET status='auto_closed',evidence=COALESCE(evidence,'') || ? WHERE id=?",
                                ("；当前值已与建议一致", primary["id"]))
                    closed += 1
                elif primary["old_value"] != row[0]:
                    con.execute("UPDATE reviews SET old_value=? WHERE id=?", (row[0], primary["id"]))
        return closed

    def _close_same_value_reviews(self, con, review, decision):
        """After a field decision, close same-value siblings and re-base competing ones."""
        from .market_store import semantic_value
        field = review["field"]
        wanted = semantic_value(field, review["new_value"])
        note = "；同值建议已接受，自动关闭" if decision == "accept" else "；同值建议已拒绝，自动关闭"
        closed = 0
        for other in con.execute("""SELECT id,new_value FROM reviews WHERE company_id=? AND kind='field_update'
                AND field=? AND status='pending' AND id!=?""", (review["company_id"], field, review["id"])).fetchall():
            if semantic_value(field, other["new_value"]) == wanted:
                con.execute("UPDATE reviews SET status='auto_closed',evidence=COALESCE(evidence,'') || ? WHERE id=?",
                            (note, other["id"]))
                closed += 1
            elif decision == "accept":
                # A competing proposal stays open; compare it against the value just accepted.
                con.execute("UPDATE reviews SET old_value=? WHERE id=?", (review["new_value"], other["id"]))
        return closed

    def resolve_branch_reviews(self):
        # Upgrade old pending suggestions without changing either company.
        with self.connect() as con:
            count = 0
            for review in con.execute("SELECT * FROM reviews WHERE kind='duplicate_candidate' AND status='pending'").fetchall():
                left = con.execute("SELECT * FROM companies WHERE id=?", (review["old_value"],)).fetchone()
                right = con.execute("SELECT * FROM companies WHERE id=?", (review["new_value"],)).fetchone()
                if left and right and distinct_branches(left, right):
                    con.execute("UPDATE reviews SET status='auto_separate',evidence=evidence || ? WHERE id=?",
                        ("；同名同官网、地址和电话不同，自动保留独立门店",review["id"]))
                    count += 1
        return count

    def _decorate_operating(self, con, company):
        row = con.execute("SELECT value,source_url,observed_at FROM observations WHERE company_id=? AND field='operating_status' ORDER BY observed_at DESC,rowid DESC LIMIT 1", (company["id"],)).fetchone()
        company["contact_count"] = self.contact_count(con, company["id"])
        checked = con.execute("SELECT checked_at FROM company_manual_checks WHERE company_id=?", (company["id"],)).fetchone()
        company["manual_checked_at"] = checked[0] if checked else None
        company["operating_status"] = row["value"] if row else "unknown"
        company["operating_source_url"] = row["source_url"] if row else ""
        company["operating_observed_at"] = row["observed_at"] if row else ""
        return company

    def company(self, id):
        with self.connect() as con:
            company = self.row(con.execute("""SELECT c.*,(SELECT count(*) FROM contacts WHERE company_id=c.id)
                contact_count FROM companies c WHERE c.id=?""", (id,)).fetchone())
            return self._decorate_operating(con, company)

    def manual_company(self, name, website, city="", country="US"):
        id = uid()
        with self.connect() as con:
            con.execute("""INSERT INTO companies(id,name,website,city,country,source_kind,created_at)
                VALUES(?,?,?,?,?,'manual',?)""", (id, name, website, city, country, now()))
            self._observe(con, id, "name", name, website, "用户手动录入，未作为自动发现记录", now(), "manual")
        return self.company(id)

    def _observe(self, con, id, field, value, url, evidence, timestamp, method, run_id="", query=""):
        if value is None or value == "":
            return
        if not isinstance(value, str):
            value = encode(value)
        fingerprint = hashlib.sha256(encode([id, field, value, url, timestamp, method, run_id, query]).encode()).hexdigest()
        con.execute("""INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid(), id, field, value, url, evidence[:4000], timestamp, method, run_id, query, fingerprint))

    def _review(self, con, id, kind, field, old, new, url, evidence, *, revision=None):
        """Single write path for reviews. Returns 'created', 'merged' or 'duplicate'.

        Field updates proposing the same (semantic) value merge into one pending
        review; each source page is kept as a review_evidence row."""
        fingerprint = hashlib.sha256(encode([id, kind, field, old, new, url, revision]).encode()).hexdigest()
        if kind != "field_update":
            inserted = con.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (uid(), id, kind, field, old, new, url, evidence[:4000], "pending", now(), fingerprint))
            return "created" if inserted.rowcount else "duplicate"
        if (con.execute("SELECT 1 FROM reviews WHERE fingerprint=?", (fingerprint,)).fetchone()
                or con.execute("SELECT 1 FROM review_evidence WHERE fingerprint=?", (fingerprint,)).fetchone()):
            return "duplicate"
        from .market_store import semantic_value
        wanted = semantic_value(field, new)
        match = next((r for r in con.execute("""SELECT id,old_value,new_value FROM reviews WHERE company_id=?
                AND kind='field_update' AND field=? AND status='pending' ORDER BY created_at,rowid""", (id, field))
                if semantic_value(field, r["new_value"]) == wanted), None)
        if match:
            con.execute("INSERT INTO review_evidence VALUES(?,?,?,?,?)",
                        (match["id"], fingerprint, url, evidence[:4000], now()))
            if match["old_value"] != old:
                con.execute("UPDATE reviews SET old_value=? WHERE id=?", (old, match["id"]))
            return "merged"
        review_id = uid()
        con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (review_id, id, kind, field, old, new, url, evidence[:4000], "pending", now(), fingerprint))
        con.execute("INSERT INTO review_evidence VALUES(?,?,?,?,?)",
                    (review_id, fingerprint, url, evidence[:4000], now()))
        return "created"

    def _observed_field(self, con, company, field, value, url, evidence, timestamp, method, run_id="", query=""):
        if value is None or value == "" or field not in {"name", "address", "phone", "website", "business_types"}:
            return
        if self._market_run(con, run_id):
            return self._market_observed_field(
                con, company, field, value, url, evidence, timestamp, method, run_id, query)
        self._observe(con, company["id"], field, value, url, evidence, timestamp, method, run_id, query)
        if field == "business_types":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = [value]
            if not isinstance(value, list):
                return
            value = encode(value)
        old = company[field]
        if equivalent_format(field, old, value):
            return
        # All competing non-empty values need review. No auto overrides.
        if old is not None and str(old).strip() not in ("", "[]"):
            if self._review(con, company["id"], "field_update", field, old, value, url, evidence) == "merged":
                return  # Same proposal from another page: evidence added, no extra history row.
        else:
            con.execute(f"UPDATE companies SET {field}=?,revision=revision+1 WHERE id=?", (value, company["id"]))
        con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                    (uid(), company["id"], field, old, value, now(), "来源观察；冲突需审核" if old else "补全空字段"))

    def ingest(self, record, run_id, query, *, city="", country="US"):
        if not record.get("source_key") or not record.get("source_url") or not record.get("name"):
            raise ValueError("自动发现结果缺少来源标识、链接或名称")
        timestamp = record.get("observed_at") or now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            link = con.execute("SELECT company_id FROM listings WHERE source_key=?", (record["source_key"],)).fetchone()
            if link:
                id = link[0]
                # Follow explicit merges without losing original listing identity.
                prior = con.execute("SELECT * FROM companies WHERE id=?", (id,)).fetchone()
                id = prior["merged_into"] or id
                con.execute("UPDATE listings SET last_seen=? WHERE source_key=?", (timestamp, record["source_key"]))
            else:
                id = uid()
                con.execute("""INSERT INTO companies(id,name,address,phone,website,city,country,source_kind,created_at)
                    VALUES(?,?,?,?,?,?,?,'maps',?)""",
                    (id, record["name"], record.get("address") or "", record.get("phone") or "",
                     record.get("website") or "", city, country, now()))
                con.execute("INSERT INTO listings VALUES(?,?,?,?,?)",
                            (record["source_key"], id, record["source_url"], timestamp, timestamp))
                con.execute("INSERT INTO locations VALUES(?,?,?,?,?,?,?,?)",
                            (uid(), id, record["source_key"], record.get("address"), record.get("latitude"),
                             record.get("longitude"), record["source_url"], timestamp))
            company = dict(con.execute("SELECT * FROM companies WHERE id=?", (id,)).fetchone())
            for field in ("name", "address", "phone", "website"):
                self._observed_field(con, company, field, record.get(field), record["source_url"],
                                     f"Google Maps 实际查询：{query}", timestamp, "maps", run_id, query)
        return self.company(id)

    def update_company(self, id, revision, fields, notes=None, status=None, business_types=None):
        fields = dict(fields)
        if business_types is not None:
            if not isinstance(business_types, list) or len(business_types) > 20 or any(not isinstance(t,str) or not t.strip() or len(t) > 80 for t in business_types):
                raise ValueError("无效业务类型")
            fields["business_types"] = list(dict.fromkeys(t.strip() for t in business_types))
        allowed = {"name", "address", "phone", "website", "business_types"}
        if set(fields) - allowed:
            raise ValueError("不支持修改此字段")
        if status is not None and status not in {"candidate", "verified", "not_fit", "do_not_contact"}:
            raise ValueError("无效状态")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            company = self.row(con.execute("SELECT * FROM companies WHERE id=?", (id,)).fetchone())
            if company["revision"] != revision:
                raise Conflict("资料已更新，请刷新后重试")
            overrides = set(company["overrides"])
            for field, value in fields.items():
                if company[field] != value:
                    stored = encode(value) if isinstance(value, list) else value
                    old = encode(company[field]) if isinstance(company[field], list) else company[field]
                    con.execute(f"UPDATE companies SET {field}=? WHERE id=?", (stored, id))
                    con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                                (uid(), id, field, old, stored, now(), "人工修正"))
                    self._observe(con, id, field, value, company["website"], "人工修正", now(), "manual")
                overrides.add(field)
            if notes is not None:
                con.execute("UPDATE companies SET notes=? WHERE id=?", (notes, id))
            if status is not None:
                self._set_company_status(con, company, status)
            con.execute("INSERT OR REPLACE INTO company_manual_checks VALUES(?,?)", (id, now()))
            con.execute("UPDATE companies SET overrides=?,revision=revision+1 WHERE id=?", (encode(sorted(overrides)), id))
        return self.company(id)

    def _set_company_status(self, con, company, status):
        if company["status"] != status:
            con.execute("UPDATE companies SET status=? WHERE id=?", (status, company["id"]))
            con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                        (uid(), company["id"], "status", company["status"], status, now(), "人工客户匹配判断"))
        con.execute("INSERT OR REPLACE INTO company_manual_checks VALUES(?,?)", (company["id"], now()))

    def batch_status(self, companies, status):
        if status not in {"candidate", "verified", "not_fit", "do_not_contact"}:
            raise ValueError("无效状态")
        if not companies or len(companies) > 200 or len({c["id"] for c in companies}) != len(companies):
            raise ValueError("请选择1至200家不重复企业")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = []
            for item in companies:
                row = self.row(con.execute("SELECT * FROM companies WHERE id=?", (item["id"],)).fetchone())
                if row["revision"] != item["revision"]:
                    raise Conflict("所选企业资料已更新，请刷新后重试；本批未作修改")
                rows.append(row)
            for row in rows:
                self._set_company_status(con, row, status)
                con.execute("UPDATE companies SET revision=revision+1 WHERE id=?", (row["id"],))
        return {"updated": len(rows)}

    def apply_enrichment(self, id, payload, run_id=""):
        if payload.get("status") not in {"completed", "partial"}:
            return
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            company = dict(con.execute("SELECT * FROM companies WHERE id=?", (id,)).fetchone())
            for obs in payload.get("observations", []):
                if not obs.get("source_url") or not obs.get("evidence"):
                    continue
                company = dict(con.execute("SELECT * FROM companies WHERE id=?", (id,)).fetchone())
                self._observed_field(con, company, obs["field"], obs.get("value"), obs["source_url"],
                                     obs["evidence"], obs.get("observed_at", now()), "website", run_id)
            for contact in payload.get("contacts", []):
                if not contact.get("source_url") or not contact.get("evidence"):
                    continue
                fingerprint = hashlib.sha256(encode([id, contact.get("email") or contact.get("url"),
                                                     contact.get("name"), contact.get("source_url")]).encode()).hexdigest()
                prior = con.execute("SELECT * FROM contacts WHERE fingerprint=?", (fingerprint,)).fetchone()
                if prior and prior["review_status"] in {"invalid", "superseded"}:
                    continue
                if not prior and contact.get("email"):
                    prior = con.execute("""SELECT * FROM contacts WHERE company_id=?
                        AND lower(trim(email))=lower(trim(?)) AND review_status NOT IN ('invalid','superseded')
                        ORDER BY (source_url=?) DESC,observed_at DESC,rowid DESC LIMIT 1""",
                        (id, contact["email"], contact["source_url"])).fetchone()
                if prior:
                    if prior["review_status"] == "reject":
                        con.execute("INSERT OR IGNORE INTO contact_decisions VALUES(?, 'excluded', ?)", (prior["id"], now()))
                    material = ("name", "role", "kind", "role_status", "association_status", "evidence")
                    contact = {**contact, **{k: contact.get(k) or prior[k] or "" for k in material}}
                    conflict = any(prior[k] and contact[k] != prior[k] for k in material if k != "evidence")
                    if conflict or contact["source_url"] != prior["source_url"] or contact["evidence"] != prior["evidence"]:
                        con.execute("INSERT INTO contact_versions VALUES(?,?,?,?)",
                                    (uid(), prior["id"], encode(dict(prior)), prior["observed_at"]))
                    con.execute("INSERT INTO contact_versions VALUES(?,?,?,?)",
                                (uid(), prior["id"], encode(contact), contact.get("observed_at") or now()))
                    if any((prior[k] or "") != (contact.get(k) or "") for k in material if k != "evidence"):
                        self._mark_market_changed(con, run_id, id)
                    if any((prior[k] or "") != (contact.get(k) or "") for k in material):
                        old = encode({k: prior[k] for k in material})
                        new = encode({k: contact.get(k) or "" for k in material})
                        assignments = ",".join(f"{k}=?" for k in material)
                        con.execute(f"UPDATE contacts SET {assignments},review_status=? WHERE id=?",
                                    tuple(contact.get(k) or "" for k in material) + ("pending" if conflict else prior["review_status"], prior["id"]))
                        if conflict:
                            con.execute("UPDATE reviews SET status='superseded' WHERE kind='contact' AND new_value=? AND status='pending'",
                                        (prior["id"],))
                            self._review(con, id, "contact", "contact", old, prior["id"], contact["source_url"],
                                         contact["evidence"], revision=uid())
                        con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                                    (uid(), id, "contact", old, new, now(), "联系人资料变化，需审核" if conflict else "自动补全联系人资料或更新来源证据"))
                    con.execute("UPDATE contacts SET observed_at=?,source_url=? WHERE id=?",
                                (contact.get("observed_at") or now(), contact["source_url"], prior["id"]))
                    continue
                cid = uid()
                vals = [contact.get(k) or "" for k in (
                    "name", "role", "email", "url", "kind", "role_status", "association_status",
                    "source_url", "evidence", "observed_at")]
                vals[-1] = vals[-1] or now()
                con.execute("INSERT INTO contacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, id, *vals, "auto_recorded", fingerprint))
                con.execute("INSERT INTO contact_versions VALUES(?,?,?,?)",
                            (uid(), cid, encode(contact), vals[-1]))
                self._mark_market_changed(con, run_id, id)
            social = sorted(set(json.loads(company["social_links"])) | set(payload.get("social_links", [])))
            if social != sorted(json.loads(company["social_links"])):
                self._mark_market_changed(con, run_id, id)
            con.execute("UPDATE companies SET social_links=?,last_checked_at=COALESCE(?,last_checked_at),revision=revision+1 WHERE id=?",
                        (encode(social), now() if payload.get("status") == "completed" else None, id))
            self._sync_social(con, id)
            self._sync_market_social_links(con, id)
        self.reclassify_contacts(id)

    def contact_count(self, con, company_id=None):
        sql = """SELECT c.* FROM contacts c WHERE review_status NOT IN ('reject','invalid','superseded','pending')
            AND NOT EXISTS (SELECT 1 FROM contact_decisions d WHERE d.contact_id=c.id AND d.adoption_status='excluded')"""
        rows = con.execute(sql + (" AND company_id=?" if company_id else ""),
                           (company_id,) if company_id else ()).fetchall()
        from .contact_projection import outreach_category
        from .contact_quality import invalid_contact_channel_reason
        return len({contact_key(dict(r)) for r in rows
                    if outreach_category(r['kind'], r['role_status'], r['email'], r['role']) != 'irrelevant'
                    and not invalid_contact_channel_reason(r['kind'], r['email'], r['url'], r['evidence'])})

    def contact_records(self, con, company_id):
        """Current identities with source snapshots; attribution history stays separate."""
        records = []
        for row in con.execute("SELECT * FROM contacts WHERE company_id=?", (company_id,)).fetchall():
            current = dict(row)
            decision = con.execute("SELECT adoption_status FROM contact_decisions WHERE contact_id=?", (current["id"],)).fetchone()
            current["adoption_status"] = decision[0] if decision else ("excluded" if current["review_status"] == "reject" else "automatic")
            records.append(current)
            versions = con.execute("SELECT payload FROM contact_versions WHERE contact_id=? ORDER BY observed_at", (current["id"],)).fetchall()
            for version in versions:
                payload = json.loads(version[0])
                # Never attach Alex's phone/job to Bob merely because they shared email.
                keys = ("name", "role", "email", "kind", "role_status", "association_status")
                if all((payload.get(k) or "") == (current.get(k) or "") for k in keys):
                    records.append({**current, **{k:payload.get(k) or current.get(k) for k in
                                    ("source_url","evidence","observed_at")}})
        return records

    def reclassify_contacts(self, company_id=None):
        """Archive demonstrably invalid extraction, keeping evidence and audit history."""
        from .contact_quality import invalid_contact_reason, invalid_email_reason
        archived = 0
        channels_preserved = 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute("""SELECT * FROM contacts WHERE review_status NOT IN ('reject','invalid','superseded','accept','pending')
                               AND NOT EXISTS (SELECT 1 FROM contact_decisions d WHERE d.contact_id=contacts.id
                                               AND d.adoption_status IN ('excluded','accepted'))"""
                               + (" AND company_id=?" if company_id else ""),
                               (company_id,) if company_id else ()).fetchall()
            for row in rows:
                reason = invalid_contact_reason(dict(row))
                if not reason:
                    continue
                # A wrong person/role association does not invalidate an email
                # explicitly present in the source. Keep it as a company channel.
                email = row['email'] or ''
                if (reason != 'third_party_service_email' and email
                        and not invalid_email_reason(email)
                        and email.casefold() in (row['evidence'] or '').casefold()):
                    fingerprint = hashlib.sha256(encode([row['company_id'],email,None,row['source_url']]).encode()).hexdigest()
                    result = con.execute("INSERT OR IGNORE INTO contacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uid(),row['company_id'],'','',email,'','general','unknown','company_only',
                         row['source_url'],row['evidence'],row['observed_at'],'auto_recorded',fingerprint))
                    channels_preserved += result.rowcount
                con.execute("UPDATE contacts SET review_status='invalid' WHERE id=?", (row['id'],))
                con.execute("UPDATE reviews SET status='superseded' WHERE kind='contact' AND new_value=? AND status='pending'", (row['id'],))
                con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                            (uid(), row['company_id'], 'contact', encode(dict(row)),
                             encode({'id':row['id'], 'review_status':'invalid'}), now(),
                             '联系人识别修正：' + {'invalid_person_name':'服务栏目、宣传句或混合文字被误认为姓名', 'invalid_job_title':'普通文案被误认为岗位', 'placeholder_email':'建站占位邮箱', 'invalid_email_format':'邮箱格式无效', 'placeholder_contact_link':'菜单占位链接，不是联系入口', 'non_contact_form':'订阅或购物用途页面，不是联系入口', 'page_wrapper_form':'整页导航外层表单被误认为联系表单', 'invalid_contact_link':'联系地址无效', 'third_party_service_email':'第三方网站插件服务邮箱', 'shared_mailbox_assigned_to_person':'通用邮箱被错误归属个人', 'supplier_link_without_contact_intent':'缺少供应商申请或联系意图'}.get(reason, reason) + '；原始证据保留'))
                archived += 1
        return {'archived': archived, 'channels_preserved': channels_preserved}

    def detail(self, id):
        from .contact_fields import contact_fields
        company = self.company(id)
        with self.connect() as con:
            contacts = self.contact_records(con, id)
            observations = [dict(r) for r in con.execute(
                "SELECT * FROM observations WHERE company_id=? ORDER BY observed_at DESC LIMIT 300", (id,))]
            key = website_key(company["website"])
            related = [brief(row) for row in con.execute("SELECT * FROM companies WHERE id<>? ORDER BY name,address,id", (id,))
                       if key and (website_key(row["website"]) == key or (branch_identity(company) and branch_identity(row) == branch_identity(company)))] if key else []
        from .contact_projection import categorize_outreach
        contacts = categorize_outreach(group_contact_records(contacts, company["website"], include_excluded=True))
        return {"company": company, "contacts": contacts, "observations": observations,
                "changes": self.changes(id), "social_links": company["social_links"], "related_locations": related}

    def reviews(self):
        with self.connect() as con:
            reviews = [dict(r) for r in con.execute("""SELECT r.*,c.name company_name FROM reviews r
                JOIN companies c ON c.id=r.company_id WHERE r.status='pending' ORDER BY r.created_at DESC""")]
            from .contact_fields import contact_fields
            for review in reviews:
                review["evidence_count"] = max(1, con.execute(
                    "SELECT count(*) FROM review_evidence WHERE review_id=?", (review["id"],)).fetchone()[0])
                review["evidence_items"] = [dict(e) for e in con.execute(
                    """SELECT source_url,evidence,observed_at FROM review_evidence WHERE review_id=?
                       ORDER BY observed_at DESC,rowid DESC LIMIT 5""", (review["id"],))]
                if review["kind"] == "contact":
                    row = con.execute("SELECT * FROM contacts WHERE id=? AND company_id=?", (review["new_value"],review["company_id"])).fetchone()
                    review["contact"] = dict(row) if row else None
                    if row:
                        website = con.execute("SELECT website FROM companies WHERE id=?", (review["company_id"],)).fetchone()[0]
                        review["contact"]["fields"] = contact_fields(dict(row),website)
                    try:
                        previous = json.loads(review["old_value"] or "{}")
                    except ValueError:
                        previous = {}
                    review["previous_contact"] = previous if isinstance(previous,dict) else {}
                if review["kind"] == "duplicate_candidate":
                    left = brief(con.execute("SELECT * FROM companies WHERE id=?", (review["old_value"],)).fetchone())
                    right = brief(con.execute("SELECT * FROM companies WHERE id=?", (review["new_value"],)).fetchone())
                    key = website_key(left["website"]) if left else ""
                    same = bool(key and right and key == website_key(right["website"]))
                    review["comparison"] = {"left": left, "right": right, "same_website": same,
                                            "website_group": key if same else ""}
            return reviews

    def review_decision(self, id, decision):
        if decision not in {"accept", "reject"}:
            raise ValueError("无效审核决定")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            r = self.row(con.execute("SELECT * FROM reviews WHERE id=?", (id,)).fetchone())
            if r["status"] != "pending":
                raise Conflict("此记录已审核")
            if r["kind"] == "duplicate_candidate" and decision == "accept":
                raise ValueError("此项需保留为独立记录；不支持将一条企业资料覆盖另一条")
            if r["kind"] == "contact":
                con.execute("UPDATE contacts SET review_status=? WHERE id=?", (decision, r["new_value"]))
                prior = con.execute("SELECT adoption_status FROM contact_decisions WHERE contact_id=?", (r["new_value"],)).fetchone()
                adoption = "accepted" if decision == "accept" else "excluded"
                con.execute("INSERT OR REPLACE INTO contact_decisions VALUES(?,?,?)", (r["new_value"], adoption, now()))
                con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                            (uid(), r["company_id"], "contact_adoption", prior[0] if prior else "automatic",
                             encode({"contact_id":r["new_value"],"adoption_status":adoption}), now(), "人工联系人采用决定"))
            elif decision == "accept":
                field = r["field"]
                if field not in {"name", "phone", "website", "address", "business_types"}:
                    raise ValueError("不支持的字段")
                current = con.execute(f"SELECT {field} FROM companies WHERE id=?", (r["company_id"],)).fetchone()[0]
                if current != r["old_value"]:
                    raise Conflict("当前值已变化，请重新核对")
                con.execute(f"UPDATE companies SET {field}=?,revision=revision+1 WHERE id=?", (r["new_value"], r["company_id"]))
                overrides = json.loads(con.execute("SELECT overrides FROM companies WHERE id=?", (r["company_id"],)).fetchone()[0])
                con.execute("UPDATE companies SET overrides=? WHERE id=?", (encode(sorted(set(overrides) | {field})), r["company_id"]))
                con.execute("INSERT OR REPLACE INTO company_manual_checks VALUES(?,?)", (r["company_id"], now()))
                con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)",
                            (uid(), r["company_id"], field, current, r["new_value"], now(), "人工接受来源变更"))
            con.execute("UPDATE reviews SET status=? WHERE id=?", (decision, id))
            return self._close_same_value_reviews(con, r, decision) if r["kind"] == "field_update" else 0

    def changes(self, company_id=None):
        with self.connect() as con:
            sql = """SELECT h.*,c.name company_name FROM changes h JOIN companies c ON c.id=h.company_id"""
            params = ()
            if company_id:
                sql += " WHERE company_id=?"
                params = (company_id,)
            return [dict(r) for r in con.execute(sql + " ORDER BY created_at DESC LIMIT 500", params)]

    def create_task(self, payload):
        id = uid()
        with self.connect() as con:
            con.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (id, payload["name"], payload["country"], payload["state"], payload["city"],
                         encode(payload["keywords"]), encode(payload["company_types"]), payload["limit"],
                         int(payload["auto_enrich"]), now()))
        return self.task(id)

    def task(self, id):
        with self.connect() as con:
            task = self.row(con.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone())
            last = con.execute("SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (id,)).fetchone()
            task["latest_run"] = self.row(last) if last else None
            metadata = con.execute("SELECT created_by_user_id FROM task_metadata WHERE task_id=?", (id,)).fetchone()
            task["created_by_user_id"] = metadata[0] if metadata else None
            task.update(self._task_social_options(con, id))
            geography = con.execute("SELECT payload FROM task_geography WHERE task_id=?", (id,)).fetchone()
            if geography:
                task["geography"] = json.loads(geography["payload"])
                task["county"] = task["geography"].get("county", "")
            return self._market_task_details(con, task)

    def tasks(self):
        with self.connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM tasks ORDER BY created_at DESC")]
        return [self.task(i) for i in ids]

    def _start(self, con, *, task=None, company_id=None):
        kind = "discovery" if task else "refresh"
        task_id = task["id"] if task else None
        existing = con.execute("""SELECT * FROM runs WHERE kind=? AND COALESCE(task_id,'')=?
            AND COALESCE(company_id,'')=? AND status IN ('queued','running','paused')""",
            (kind, task_id or "", company_id or "")).fetchone()
        if existing:
            return self.row(existing)
        id = uid()
        market_mode = task is not None and task["limit"] == 0
        targets = ([task_id] if market_mode else
                   [f"{k} in {task['city']}, {task['state']}, {task['country']}" for k in task["keywords"]]) if task else [company_id]
        snapshot = {k: v for k, v in task.items() if k != "latest_run"} if task else {"company_id": company_id}
        progress = {"done": 0, "total": len(targets), "discovered": 0,
                    "enrichment_done": 0, "enrichment_total": 0 if task else len(targets)}
        if market_mode:
            progress.update(new=0, existing=0, changed=0, discovery_done=0, discovery_total=0,
                            market_total=self._market_stats(con, task_id)["total"])
        con.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (id, task_id, kind, company_id, "queued", encode(progress),
                     "等待本地 worker", None, now(), None, encode(snapshot)))
        if market_mode:
            con.execute("INSERT INTO market_runs(run_id,market_id) VALUES(?,?)", (id, task_id))
        for target in dict.fromkeys(targets):
            con.execute("INSERT INTO work_items VALUES(?,?,?,?,?,?)",
                        (uid(), id, "planning" if market_mode else kind, target, "pending", None))
        if task and snapshot.get("auto_social"):
            self._market_social_run(con, dict(id=id, status="queued", snapshot=snapshot))
        return self.row(con.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone())

    def start_task(self, id):
        task = self.task(id)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._start(con, task=task)

    def refresh(self, id):
        company = self.company(id)
        if company["status"] == "do_not_contact":
            raise Conflict("此企业已标记拒绝联系")
        if not company["website"]:
            raise ValueError("企业没有可核验官网")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._start(con, company_id=id)

    def runs(self):
        with self.connect() as con:
            rows = [self.row(r) for r in con.execute("SELECT r.*, c.name AS company_name FROM runs r LEFT JOIN companies c ON c.id=r.company_id ORDER BY r.created_at DESC LIMIT 200")]
            for row in rows:
                if self._market_run(con, row["id"]):
                    row["coverage"] = self._market_coverage(con, row["id"])
            return rows

    def active_runs(self):
        with self.connect() as con:
            return [self.row(r) for r in con.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY created_at")]

    def run_source_keys(self, run_id):
        with self.connect() as con:
            return {r[0] for r in con.execute("""SELECT DISTINCT l.source_key FROM listings l
                JOIN observations o ON o.company_id=l.company_id WHERE o.run_id=? AND o.method='maps'""", (run_id,))}

    def run(self, id):
        with self.connect() as con:
            result = self.row(con.execute("SELECT r.*, c.name AS company_name FROM runs r LEFT JOIN companies c ON c.id=r.company_id WHERE r.id=?", (id,)).fetchone())
            if self._market_run(con, id):
                result["coverage"] = self._market_coverage(con, id)
            return result

    def control(self, id, action):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            run = self.row(con.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone())
            state = run["status"]
            if action == "pause" and state in {"queued", "running"}:
                target = "paused"
            elif action == "resume" and state in {"paused", "blocked", "partial", "failed"}:
                target = "queued"
                market = self._market_run(con, id)
                retryable = self.market_retryable_items(con, id) if market else None
                social_retry=self._market_social_retryable(con,id)
                if market and not retryable and not social_retry and self._market_exhausted(con, id):
                    raise Conflict("当前检索策略已达到覆盖能力边界，没有可继续的工作项；重复继续不会补齐剩余企业")
                retry_discovery = con.execute("""SELECT 1 FROM work_items WHERE run_id=?
                    AND stage IN ('planning','discovery') AND state!='succeeded' LIMIT 1""", (id,)).fetchone()
                if market:
                    con.executemany("UPDATE work_items SET state='pending' WHERE id=?", [(item,) for item in retryable])
                else:
                    con.execute("UPDATE work_items SET state='pending' WHERE run_id=? AND state!='succeeded'", (id,))
                from .maps_retry import reset_cycle
                for item in con.execute("SELECT id,result FROM work_items WHERE run_id=? AND stage='discovery' AND state='pending'", (id,)).fetchall():
                    payload = reset_cycle(json.loads(item["result"] or "{}"))
                    con.execute("UPDATE work_items SET result=? WHERE id=?", (encode(payload),item["id"]))
                for row in con.execute("SELECT id,result FROM work_items WHERE run_id=? AND stage='refresh' AND state='pending'", (id,)).fetchall():
                    payload = json.loads(row["result"] or "{}")
                    checkpoint = payload.get("checkpoint")
                    if checkpoint and not payload.get("has_more"):
                        from .website_policy import resume as resume_website
                        try:
                            resume_website(con, checkpoint)
                        except ValueError as exc:
                            raise Conflict(str(exc)) from exc
                        failures = checkpoint.get("failures", {})
                        checkpoint.setdefault("queue", {}).update(failures)
                        checkpoint["attempted"] = [u for u in checkpoint.get("attempted", []) if u not in failures]
                        checkpoint["failures"] = {}
                        if failures or checkpoint.get("queue"):
                            failure_warnings = checkpoint.get("failure_warnings", {})
                            retry_warnings = {failure_warnings[url] for url in failures if url in failure_warnings}
                            checkpoint["warnings"] = [warning for warning in checkpoint.get("warnings", [])
                                if warning != checkpoint.get("budget_warning") and warning not in retry_warnings]
                            checkpoint["failure_warnings"] = {}
                            checkpoint["budget_warning"] = None
                        con.execute("UPDATE work_items SET result=? WHERE id=?", (encode(payload), row["id"]))
                if retry_discovery:
                    from .maps_policy import resume
                    try:
                        resume(con)
                    except ValueError as exc:
                        raise Conflict(str(exc)) from exc
                    con.execute("UPDATE market_runs SET unresolved='[]' WHERE run_id=?", (id,))
            elif action == "cancel" and state in {"queued", "running", "paused", "blocked"}:
                target = "cancelled"
            else:
                raise Conflict("当前状态不允许此操作")
            con.execute("UPDATE runs SET status=?,message=?,finished_at=?,error=NULL WHERE id=?",
                        (target, {"paused": "已暂停", "queued": "等待继续", "cancelled": "已取消"}[target],
                         now() if target == "cancelled" else None, id))
            self._control_market_social(con, id, action)
        return self.run(id)

    def claim_item(self, run_id, *, pace_maps=False, stages=None):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            run = self.row(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if run["status"] not in {"queued", "running"}:
                return None
            skip_maps = False
            from .maps_policy import time as maps_time
            retry_clock = maps_time.time()
            if pace_maps:
                from .maps_policy import status
                access = status(con)
                skip_maps = not access['allowed']
                if skip_maps:
                    con.execute("UPDATE runs SET message=? WHERE id=?", (access['message'], run_id))
            stage_sql = " AND stage IN (" + ",".join("?" for _ in stages) + ")" if stages else ""
            row = con.execute("""SELECT * FROM work_items WHERE run_id=? AND state='pending'
                AND (stage!='discovery' OR ?=0)
                AND (stage!='discovery' OR COALESCE(json_extract(result,'$.maps.next_attempt_at'),0)<=?)
                """ + stage_sql + """ ORDER BY CASE stage WHEN 'planning' THEN 0 WHEN 'discovery' THEN 1 ELSE 2 END,COALESCE(json_extract(result,'$.checkpoint.batches'),0),rowid LIMIT 1""",
                              (run_id, int(skip_maps),retry_clock, *(stages or []))).fetchone()
            if not row:
                return None
            payload = json.loads(row["result"] or "{}")
            if row["stage"] == "discovery" and pace_maps:
                from .maps_retry import claimed
                payload = claimed(payload)
            con.execute("UPDATE work_items SET state='running',result=? WHERE id=?", (encode(payload),row["id"]))
            message = f"处理中：{row['target']}"
            if self._market_run(con, run_id):
                if row["stage"] == "planning":
                    message = "规划目标市场检索范围"
                elif row["stage"] == "discovery":
                    try:
                        job = json.loads(row["target"])
                    except ValueError:
                        job = {}
                    message = "检索市场：" + str(job.get("query", "") if isinstance(job, dict) else job)[:240]
            con.execute("UPDATE runs SET status='running',message=? WHERE id=?", (message, run_id))
            result = dict(row)
            result["state"] = "running"
            result["result"] = encode(payload)
            return result

    def finish_item(self, id, status, result):
        with self.connect() as con:
            con.execute("UPDATE work_items SET state=?,result=? WHERE id=?", (status, encode(result), id))

    def add_enrichment(self, run_id, company_id):
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO work_items VALUES(?,?,?,?,?,?)", (uid(), run_id, "refresh", company_id, "pending", None))

    def progress(self, run_id):
        with self.connect() as con:
            items = list(con.execute("SELECT stage,state,result FROM work_items WHERE run_id=?", (run_id,)))
            discovered = con.execute("SELECT count(DISTINCT company_id) FROM observations WHERE run_id=? AND method='maps'", (run_id,)).fetchone()[0]
            progress = {"done": sum(r["state"] in {"succeeded", "partial", "failed", "blocked"} for r in items),
                        "total": len(items), "discovered": discovered}
            enrichment = [r for r in items if r["stage"] == "refresh"]
            progress["enrichment_total"] = len(enrichment)
            progress["enrichment_done"] = sum(r["state"] in {"succeeded", "partial", "failed", "blocked"} for r in enrichment)
            summaries = [json.loads(r["result"] or "{}").get("summary", {}) for r in enrichment]
            progress["website_pages_checked"] = sum(s.get("pages_checked", 0) for s in summaries)
            progress["website_links_remaining"] = sum(s.get("remaining", 0) for s in summaries)
            from .market_social import social_workflow
            social_stage, social = social_workflow(con, {"id": run_id, "status": "running"})
            if social_stage:
                progress.update(social_total=social_stage["total"], social_done=social_stage["processed"],
                                social_accounts=social["accounts_found"])
                if con.execute("SELECT kind FROM runs WHERE id=?", (run_id,)).fetchone()[0]=="social":
                    progress.update(total=social_stage["total"],done=social_stage["processed"])
            market = self._market_run(con, run_id)
            if market:
                stats = self._market_stats(con, market["market_id"], run_id)
                progress.update({k: stats[k] for k in ("new", "existing", "changed")})
                progress["market_total"] = stats["total"]
                progress["discovered"] = stats["new"] + stats["existing"]
                discovery = [r for r in items if r["stage"] == "discovery"]
                progress["discovery_total"] = len(discovery)
                progress["discovery_done"] = sum(r["state"] in {"succeeded", "partial", "failed", "blocked"} for r in discovery)
            con.execute("UPDATE runs SET progress=? WHERE id=?", (encode(progress), run_id))
        return progress

    def finalize(self, id):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            run = self.row(con.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone())
            if run["status"] not in {"queued", "running"}:
                return
            rows = list(con.execute("SELECT stage,state,result FROM work_items WHERE run_id=?", (id,)))
            if any(r["state"] in {"running", "pending"} for r in rows):
                return
            linked = con.execute("SELECT social_run_id FROM market_social_runs WHERE market_run_id=?", (id,)).fetchone()
            if linked:
                self._finish_social_run(con, linked[0])
                social_items = list(con.execute("SELECT stage,state,result FROM social_work WHERE run_id=?", (linked[0],)))
                if any(r["state"] in {"pending", "running"} for r in social_items):
                    return
            else:
                social_items = []
            failed = [r for r in rows if r["state"] != "succeeded"]
            failed += [r for r in social_items if r["state"] not in {"succeeded", "no_results", "skipped"}
                       or json.loads(r["result"] or "{}").get("partial")]
            has_success = any(r["state"] in {"succeeded", "partial", "no_results"} for r in [*rows, *social_items])
            state = "completed" if not failed else ("partial" if has_success else "blocked" if any(r["state"] == "blocked" for r in failed) else "failed")
            messages = [json.loads(r["result"]).get("message", "") for r in failed if r["result"]]
            message = "计划内查询结束，不代表地区全量" if state == "completed" else "部分任务未完成，请查看原因"
            summaries = [json.loads(r["result"] or "{}").get("summary") for r in rows]
            summaries = [s for s in summaries if s]
            if summaries:
                pages = sum(s.get("pages_checked", 0) for s in summaries)
                contacts = sum(s.get("contacts_found", 0) for s in summaries)
                skipped = sum(s.get("pages_skipped", 0) for s in summaries)
                message = f"本次官网扫描读取 {pages} 页，提取 {contacts} 条联系记录"
                if skipped:
                    message += f"；按扫描规则跳过 {skipped} 个无关页面"
                files = sum(s.get("files_unparsed", 0) for s in summaries)
                if files:
                    message += f"；另有 {files} 个PDF文件未解析（不计为网页访问失败）"
                if state != "completed":
                    message += "；扫描未全部完成，已取得的信息已保留"
                if run["kind"] == "discovery":
                    message = "企业发现查询已结束；" + message
            market = self._market_run(con, id)
            if market:
                coverage = self._market_coverage(con, id)
                if coverage["unresolved"] and state == "completed":
                    state = "partial"
                stats = self._market_stats(con, market["market_id"], id)
                message = (f"本市场本轮新增 {stats['new']} 家，存量 {stats['existing']} 家，"
                           f"资料变化 {stats['changed']} 家；市场库共 {stats['total']} 家"
                           f"；发现检索 {coverage['done']}/{coverage['planned']} 项")
                message += "；计划内检索结束，不代表地区全量" if state == "completed" else "；检索尚未全部完成"
                messages.extend(coverage["unresolved"])
                progress = dict(run["progress"])
                progress.update({k: stats[k] for k in ("new", "existing", "changed")})
                progress.update(market_total=stats["total"], discovery_done=sum(r["stage"] == "discovery" for r in rows),
                                discovery_total=coverage["planned"], done=len(rows), total=len(rows),
                                discovered=stats["new"] + stats["existing"])
                con.execute("UPDATE runs SET progress=? WHERE id=?", (encode(progress), id))
            if linked:
                from .market_social import social_workflow
                social_stage, social = social_workflow(con, run)
                message += f"；社媒已处理 {social_stage['processed']}/{social_stage['total']} 家，候选账号 {social['accounts_found']} 个"
            if run["kind"] == "social":
                message = f"社媒补全已结束：处理 {social_stage['processed']}/{social_stage['total']} 家，候选账号 {social['accounts_found']} 个"
            con.execute("UPDATE runs SET status=?,message=?,error=?,finished_at=? WHERE id=?",
                        (state, message,
                         "\n".join(messages) or None, now(), id))

    def recover(self):
        # Caller must hold the exclusive worker lock: no other worker owns these items.
        with self.connect() as con:
            # No old process still owns a running query, including one cancelled
            # immediately before the process exited. The run itself stays cancelled.
            con.execute("UPDATE work_items SET state='pending' WHERE stage='discovery' AND state='running'")
            con.execute("""UPDATE work_items SET state='pending' WHERE state='running' AND run_id IN
                (SELECT id FROM runs WHERE status IN ('queued','running','paused'))""")
            con.execute("UPDATE runs SET status='queued',message='恢复中断任务' WHERE status='running'")

    def create_schedule(self, *, task_id=None, company_id=None, kind, interval_months=3, timezone="Asia/Shanghai", enabled=True):
        if bool(task_id) == bool(company_id) or kind not in {"discovery", "refresh"}:
            raise ValueError("计划必须指定唯一目标")
        if not 1 <= interval_months <= 24 or (kind == "discovery") != bool(task_id):
            raise ValueError("计划类型或周期错误")
        if task_id:
            self.task(task_id)
        else:
            self.company(company_id)
        anchor = datetime.now(UTC)
        next_due = calendar_next(anchor, anchor, interval_months, timezone).isoformat()
        id = uid()
        with self.connect() as con:
            con.execute("INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?)",
                        (id, task_id, company_id, kind, interval_months, timezone, next_due, int(enabled), anchor.isoformat()))
        return next(s for s in self.schedules() if s["id"] == id)

    def schedules(self):
        with self.connect() as con:
            result = [self.row(r) for r in con.execute("SELECT * FROM schedules ORDER BY next_due_at")]
            for schedule in result:
                paused = con.execute("""SELECT id FROM runs WHERE status='paused' AND
                    COALESCE(task_id,'')=? AND COALESCE(company_id,'')=? LIMIT 1""",
                    (schedule["task_id"] or "", schedule["company_id"] or "")).fetchone()
                schedule["waiting_reason"] = "旧任务已暂停，本期更新将延后；继续或取消旧任务后再调度" if paused and schedule["enabled"] and schedule["next_due_at"] <= now() else ""
            return result

    def dispatch_due(self):
        timestamp = now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for row in list(con.execute("SELECT * FROM schedules WHERE enabled=1 AND next_due_at<=?", (timestamp,))):
                s = self.row(row)
                if s["company_id"]:
                    c = con.execute("SELECT status FROM companies WHERE id=?", (s["company_id"],)).fetchone()
                    if not c or c[0] == "do_not_contact":
                        con.execute("UPDATE schedules SET enabled=0 WHERE id=?", (s["id"],))
                        continue
                prior = con.execute("SELECT 1 FROM occurrences WHERE schedule_id=? AND due_at=?", (s["id"], s["next_due_at"])).fetchone()
                if not prior:
                    task = self.row(con.execute("SELECT * FROM tasks WHERE id=?", (s["task_id"],)).fetchone()) if s["task_id"] else None
                    run = self._start(con, task=task, company_id=s["company_id"])
                    if run["status"] == "paused":
                        continue  # Still owed; a paused run has not executed this occurrence.
                    con.execute("INSERT INTO occurrences VALUES(?,?,?)", (s["id"], s["next_due_at"], run["id"]))
                due = calendar_next(datetime.fromisoformat(s["anchor"]), datetime.fromisoformat(timestamp),
                                    s["interval_months"], s["timezone"])
                con.execute("UPDATE schedules SET next_due_at=? WHERE id=?", (due.isoformat(), s["id"]))

    def backup(self):
        folder = self.directory / "backups"
        folder.mkdir(exist_ok=True)
        name = f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uid()[:8]}.sqlite3"
        target = folder / name
        try:
            with self.connect() as source, closing(sqlite3.connect(target)) as dest:
                source.backup(dest)
                # Rollback-journal header: a lone file opens without -wal/-shm on every SQLite build.
                dest.execute("PRAGMA journal_mode=DELETE")
                dest.commit()
        except (sqlite3.Error, OSError):
            log.exception("backup failed: %s", name)
            _remove_database_files(target)
            raise
        # SQLite 3.51.0 leaves an empty -shm behind even in DELETE mode; the connection is closed now.
        try:
            _remove_database_files(target, include_main=False)
        except OSError:
            log.warning("could not remove backup sidecar files for %s", name, exc_info=True)
        target.chmod(0o600)
        return {"name": name, "created_at": now()}

    def restore(self, name):
        if Path(name).name != name or not name.endswith(".sqlite3"):
            raise ValueError("无效备份名称")
        file = self.directory / "backups" / name
        if not file.is_file() or file.is_symlink():
            raise ValueError("备份不存在")
        # Validate a private copy: the original backup is only ever read, never opened for writing.
        tmp = file.parent / f".restore-{uid()}.tmp"
        try:
            try:
                shutil.copyfile(file, tmp)
                tmp.chmod(0o600)
            except OSError as exc:
                log.warning("could not copy backup %s for restore", name, exc_info=True)
                raise ValueError("无法读取备份文件，请检查磁盘空间或文件权限") from exc
            with closing(sqlite3.connect(tmp)) as source:
                try:
                    source.execute("PRAGMA journal_mode=DELETE")
                    version = source.execute("PRAGMA user_version").fetchone()[0]
                    if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or version not in {1, 2}:
                        raise ValueError("备份损坏或版本不兼容")
                    source.execute("SELECT id FROM companies LIMIT 1")
                    source.execute("SELECT id FROM tasks LIMIT 1")
                    if version == 2:
                        source.execute("SELECT run_id FROM market_runs LIMIT 1")
                        source.execute("SELECT company_id FROM market_memberships LIMIT 1")
                        source.execute("SELECT company_id FROM run_companies LIMIT 1")
                except sqlite3.DatabaseError as exc:
                    raise ValueError("备份损坏或版本不兼容，请选择其他备份") from exc
                self.backup()
                with self.connect() as dest:
                    source.backup(dest)
                    if version == 1:
                        self._migrate_market(dest)
                    self._migrate_audit(dest)
                    self._migrate_social(dest)
                    self._migrate_market_social(dest)
        finally:
            _remove_database_files(tmp)
        self.recover()
        self.recover_social()
        self.resolve_duplicate_reviews()
