"""Build legacy fixtures older than 9423c53 from frozen DDL text (P1-9, design §3.2).

git has no code older than 9423c53 (16c490d..9423c53 share leadworkbench/), so the
earlier schemas are reproduced from DDL text copied out of db.py (SCHEMA, v1) and
market_store.py (MIGRATION, v2) at 913ee04. The copies below are frozen: later code
changes never affect them. Data is inserted with plain SQL; nothing imports
leadworkbench.

Outputs (committed under tests/fixtures/):
- legacy_v1.sql            SCHEMA only, user_version=1
- legacy_v2_early.sql      v1 + market MIGRATION (4 tables), user_version=2,
                           without audit, social or policy tables
- legacy_v2_9423c53_extra.sql  rows added on top of legacy_v2_9423c53.sql by the
                           upgrade snapshot tests (R02/R03/R05-R08 inputs, social_work)

Every fixture carries rows that R02, R03, R05-R08 and the P0 repair item act on.
Usage: .venv/bin/python tests/fixtures/build_legacy_early.py
"""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Frozen copy of db.SCHEMA at 913ee04.
SCHEMA_V1 = """
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

# Frozen copy of market_store.MIGRATION at 913ee04.
MIGRATION_V2 = """
CREATE TABLE market_keys(identity TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id));
CREATE TABLE market_runs(
 run_id TEXT PRIMARY KEY REFERENCES runs(id),market_id TEXT NOT NULL REFERENCES tasks(id),
 plan TEXT NOT NULL DEFAULT '{}',unresolved TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE market_memberships(
 market_id TEXT NOT NULL REFERENCES tasks(id),company_id TEXT NOT NULL REFERENCES companies(id),
 first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,first_run_id TEXT REFERENCES runs(id),
 last_run_id TEXT REFERENCES runs(id),PRIMARY KEY(market_id,company_id)
);
CREATE TABLE run_companies(
 run_id TEXT NOT NULL REFERENCES runs(id),company_id TEXT NOT NULL REFERENCES companies(id),
 market_id TEXT NOT NULL REFERENCES tasks(id),segment TEXT NOT NULL CHECK(segment IN ('new','existing')),
 changed INTEGER NOT NULL DEFAULT 0,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
 PRIMARY KEY(run_id,company_id)
);
CREATE INDEX market_membership_company ON market_memberships(company_id,market_id);
CREATE INDEX run_companies_market ON run_companies(market_id,run_id,segment,changed);
CREATE INDEX market_runs_market ON market_runs(market_id,run_id);
"""


def q(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def insert(table, *values):
    return f'INSERT INTO "{table}" VALUES({",".join(q(v) for v in values)});'


def material(name, role, kind, email):
    return json.dumps(dict(name=name, role=role, kind=kind, role_status="explicit",
                           association_status="same_block", evidence=f"{name} | {role} | {email}"),
                      ensure_ascii=False)


T0 = "2026-01-0{}T08:00:00+00:00"
MAPS = "https://www.google.com/maps/search/?api=1&query_place_id=EARLY_{}"


def company(n, name, address, phone, website, revision=1):
    return insert("companies", f"c{n}", name, address, phone, website, "San Jose", "US", "[]", "candidate", "",
                  revision, "maps", T0.format(1), None, "[]", "[]", None)


def v1_data():
    rows = [
        insert("tasks", "t1", "San Jose roofing", "US", "California", "San Jose", '["roofing supply"]',
               '["wholesale"]', 50, 0, T0.format(1)),
        insert("tasks", "t2", "San Jose metal", "US", "California", "San Jose", '["metal supplier"]',
               '["retail"]', 20, 1, T0.format(2)),
        insert("runs", "r1", "t1", "discovery", None, "running", '{"done": 1, "total": 2}', "", None,
               T0.format(1), None, '{"limit": 50}'),
        insert("runs", "r2", "t2", "discovery", None, "succeeded", '{"done": 1, "total": 1}', "", None,
               T0.format(2), T0.format(3), '{"limit": 20}'),
        insert("work_items", "w1", "r1", "discovery", "roofing supply in San Jose", "running", None),
        insert("work_items", "w2", "r1", "discovery", "roofing supply near San Jose", "pending", None),
        insert("work_items", "w3", "r2", "discovery", "metal supplier in San Jose", "succeeded", '{"found": 2}'),
        # c1/c2: same name and website, different address and phone (R05 duplicate candidate)
        company(1, "Alpha Supply", "1 First St, San Jose, CA 95112", "+1 408-555-0101", "https://alpha.example", 2),
        company(2, "Alpha Supply", "2 Second St, San Jose, CA 95113", "+1 408-555-0102", "https://alpha.example"),
        company(3, "Gamma Metals", "3 Third St, San Jose, CA 95110", "", "https://gamma.example"),
        company(4, "Delta Building", "4 Fourth St, San Jose, CA 95111", "+1 408-555-0404", "https://delta.example"),
        company(5, "Epsilon Roofing", "5 Old Rd, San Jose, CA 95116", "+1 408-555-0505", ""),
    ]
    for n in range(1, 6):
        rows.append(insert("listings", f"google_maps:place_id:EARLY_{n}", f"c{n}", MAPS.format(n),
                           T0.format(1), T0.format(2)))
        rows.append(insert("locations", f"l{n}", f"c{n}", f"google_maps:place_id:EARLY_{n}", f"{n} Street",
                           37.3 + n / 100, -121.9 - n / 100, MAPS.format(n), T0.format(1)))
        run = "r1" if n <= 3 else "r2"
        rows.append(insert("observations", f"o{n}a", f"c{n}", "name", "x", MAPS.format(n), "Maps listing",
                           T0.format(1), "maps", run, "q", f"fp-o{n}a"))
        rows.append(insert("observations", f"o{n}b", f"c{n}", "category", "Roofing supply store" if n % 2 else
                           "Metal supplier", MAPS.format(n), "Maps category", T0.format(1), "maps", run, "q",
                           f"fp-o{n}b"))
    rows += [
        # k1 accepted, k2 rejected together with a change (P0 repair candidate), k3 pending with
        # an empty-old review (R06), k4 pending legacy conflict.
        insert("contacts", "k1", "c1", "Ann Buyer", "Buyer", "ann@alpha.example", "", "procurement", "explicit",
               "same_block", "https://alpha.example/team", "Ann Buyer | Buyer | ann@alpha.example", T0.format(2),
               "accept", "fp-k1"),
        insert("contacts", "k2", "c1", "Robert Other", "Sales Rep", "bob@alpha.example", "", "general", "explicit",
               "same_block", "https://alpha.example/about", "Robert Other | Sales Rep | bob@alpha.example",
               T0.format(3), "reject", "fp-k2"),
        insert("contacts", "k3", "c2", "", "", "info@alpha2.example", "", "general", "unknown", "company_only",
               "https://alpha.example/contact", "Email: info@alpha2.example", T0.format(2), "pending", "fp-k3"),
        insert("contacts", "k4", "c4", "Carla Other", "Sales Rep", "carl@delta.example", "", "general", "explicit",
               "same_block", "https://delta.example/about", "Carla Other | Sales Rep | carl@delta.example",
               T0.format(3), "pending", "fp-k4"),
        insert("reviews", "rv1", "c1", "contact", "contact", "", "k1", "https://alpha.example/team", "Ann Buyer",
               "accept", T0.format(2), "fp-rv1"),
        insert("reviews", "rv2", "c1", "contact", "contact", material("Bob Buyer", "Buyer", "procurement",
               "bob@alpha.example"), "k2", "https://alpha.example/about", "Robert Other | Sales Rep",
               "reject", T0.format(3), "fp-rv2"),
        # accepted and still current (R03 adds an override)
        insert("reviews", "rv3", "c1", "field_update", "phone", "+1 408-555-0100", "+1 408-555-0101",
               "https://alpha.example/contact", "Phone: +1 408-555-0101", "accept", T0.format(2), "fp-rv3"),
        insert("reviews", "rv4", "c1", "duplicate_candidate", "company", "c1", "c2", MAPS.format(2),
               "同名同官网", "pending", T0.format(2), "fp-rv4"),
        insert("reviews", "rv5", "c3", "field_update", "phone", "", "+1 408-555-0303", "https://gamma.example",
               "Phone: +1 408-555-0303", "pending", T0.format(2), "fp-rv5"),
        insert("reviews", "rv6", "c2", "contact", "contact", "", "k3", "https://alpha.example/contact",
               "Email: info@alpha2.example", "pending", T0.format(2), "fp-rv6"),
        insert("reviews", "rv7", "c4", "field_update", "phone", "+1 408-555-0404", "+14085550404",
               "https://delta.example", "Phone: +14085550404", "pending", T0.format(3), "fp-rv7"),
        insert("reviews", "rv8", "c5", "field_update", "address", "5 Old Rd, San Jose, CA 95116",
               "9 New Rd, San Jose, CA 95116", MAPS.format(5), "Address A", "pending", T0.format(3), "fp-rv8"),
        insert("reviews", "rv9", "c5", "field_update", "address", "5 Old Rd, San Jose, CA 95116",
               "9 New Rd, San Jose, CA 95116", MAPS.format(5), "Address B", "pending", T0.format(4), "fp-rv9"),
        insert("reviews", "rv10", "c4", "field_update", "name", "Delta Building", "Delta Bldg", MAPS.format(4),
               "Name", "reject", T0.format(3), "fp-rv10"),
        insert("reviews", "rv11", "c4", "contact", "contact", material("Carl Buyer", "Buyer", "procurement",
               "carl@delta.example"), "k4", "https://delta.example/about", "Carla Other | Sales Rep",
               "pending", T0.format(3), "fp-rv11"),
        insert("changes", "ch1", "c1", "phone", "+1 408-555-0100", "+1 408-555-0101", T0.format(2), "审核已接受"),
        insert("schedules", "s1", "t1", None, "discovery", 3, "America/Los_Angeles", "2026-04-01T08:00:00+00:00",
               1, T0.format(1)),
        insert("occurrences", "s1", "2026-01-01T08:00:00+00:00", "r1"),
    ]
    return rows


def v2_market_data():
    return [
        'UPDATE "tasks" SET "limit"=0;',
        insert("market_keys", "early-key-t1", "t1"),
        insert("market_keys", "early-key-t2", "t2"),
        insert("market_runs", "r1", "t1", "{}", "[]"),
        insert("market_runs", "r2", "t2", "{}", "[]"),
        insert("market_memberships", "t1", "c1", T0.format(1), T0.format(1), "r1", "r1"),
        insert("market_memberships", "t1", "c2", T0.format(1), T0.format(1), "r1", "r1"),
        insert("market_memberships", "t1", "c3", T0.format(1), T0.format(1), "r1", "r1"),
        insert("market_memberships", "t2", "c4", T0.format(1), T0.format(1), "r2", "r2"),
        insert("market_memberships", "t2", "c5", T0.format(1), T0.format(1), "r2", "r2"),
        insert("run_companies", "r1", "c1", "t1", "new", 0, T0.format(1), T0.format(1)),
        insert("run_companies", "r1", "c2", "t1", "new", 0, T0.format(1), T0.format(1)),
        insert("run_companies", "r1", "c3", "t1", "new", 0, T0.format(1), T0.format(1)),
        insert("run_companies", "r2", "c4", "t2", "new", 0, T0.format(1), T0.format(1)),
        insert("run_companies", "r2", "c5", "t2", "new", 0, T0.format(1), T0.format(1)),
        'DELETE FROM "work_items" WHERE run_id=\'r1\' AND stage=\'discovery\';',
        insert("work_items", "w-plan-r1", "r1", "planning", "t1", "pending", None),
        'UPDATE "runs" SET status=\'queued\',snapshot=\'{"limit": 0, "mode": "market"}\' WHERE id=\'r1\';',
        "PRAGMA user_version=2;",
    ]


# Rows added to legacy_v2_9423c53.sql for the upgrade snapshot (the committed dump stays unchanged).
A1 = "f4b77019f0a14e7db966b7b9e352cfa2"   # Alpha Roofing Supply
B2 = "418c1548456f4c23b91b33f9c71b3ab4"   # Beta Metals
C3 = "b3b9bdd8ebaf4b9db743aee1f802ec01"   # Gamma Sheet Metal
D4 = "56bc44d913e4495fa2d8f408aa124daf"   # Delta Building Supply
E5 = "a73833fca1b4416698cad0c68450ae19"   # Epsilon Roofing Contractor (no phone)
STAMP = "2026-10-18T09:00:0{}+00:00"


def extra_9423c53():
    return [
        insert("companies", "x-alpha-2", "Alpha Roofing Supply", "900 Ninth St, San Jose, CA 95112",
               "+1 408-555-0909", "https://alpha.example", "San Jose", "US", "[]", "candidate", "", 1, "maps",
               STAMP.format(1), None, "[]", "[]", None),
        insert("reviews", "x-dup", A1, "duplicate_candidate", "company", A1, "x-alpha-2", MAPS.format(9),
               "同名同官网", "pending", STAMP.format(1), "x-fp-dup"),
        insert("reviews", "x-accept", B2, "field_update", "phone", "+1 408-555-0100", "+1 408-555-0102",
               "https://beta.example", "Phone", "accept", STAMP.format(1), "x-fp-accept"),
        insert("reviews", "x-empty", E5, "field_update", "phone", "", "+1 408-555-0105", "https://epsilon.example",
               "Phone", "pending", STAMP.format(2), "x-fp-empty"),
        insert("reviews", "x-format", C3, "field_update", "phone", "+1 408-555-0103", "+14085550103",
               "https://gamma.example", "Phone", "pending", STAMP.format(2), "x-fp-format"),
        insert("reviews", "x-same-1", D4, "field_update", "address", "400 Fourth St, San Jose, CA 95110",
               "410 Fourth St, San Jose, CA 95110", MAPS.format(4), "Address A", "pending", STAMP.format(3),
               "x-fp-same-1"),
        insert("reviews", "x-same-2", D4, "field_update", "address", "400 Fourth St, San Jose, CA 95110",
               "410 Fourth St, San Jose, CA 95110", MAPS.format(4), "Address B", "pending", STAMP.format(4),
               "x-fp-same-2"),
        insert("social_runs", "x-social-run", "completed", STAMP.format(1), STAMP.format(2), f'["{E5}"]', "{}",
               "[]"),
        insert("social_work", "x-social-work", "x-social-run", E5, "website", "", "no_results", "{}",
               STAMP.format(1), STAMP.format(2)),
    ]


HEADER = ("-- Generated by tests/fixtures/build_legacy_early.py from DDL frozen at 913ee04. Synthetic data only.\n"
          "-- {note}\n")


def write(name, note, statements):
    text = HEADER.format(note=note) + "\n".join(statements) + "\n"
    (HERE / name).write_text(text, encoding="utf-8")


def main():
    v1 = [SCHEMA_V1.strip()] + v1_data()
    write("legacy_v1.sql", "user_version=1：只有 SCHEMA 的 12 张表", v1)
    v2 = v1 + [s.strip() + ";" for s in MIGRATION_V2.split(";") if s.strip()] + v2_market_data()
    write("legacy_v2_early.sql", "user_version=2：v1 + market 4 张表，无审计、社媒、策略表", v2)
    write("legacy_v2_9423c53_extra.sql", "在 legacy_v2_9423c53.sql 之上追加的行（升级快照测试用）",
          extra_9423c53())


if __name__ == "__main__":
    main()
