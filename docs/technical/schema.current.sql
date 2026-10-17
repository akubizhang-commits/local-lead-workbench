-- Generated 2026-09-26 from a temporary Store; no production DB opened.
-- Startup migrations plus source-extracted lazy market_exhausted_items DDL; no business fixtures.
-- Not an upgrade migration; schema only, no data.
-- SHA256 leadworkbench/api.py: 9a2b57d67a21fdc25b5b25e6183d586a88818b1d324e0d591b7449394e8dc99b
-- SHA256 leadworkbench/db.py: 8cd6ada61768a423937a1b1d989bb4a344b4916a450d1de530af01c3039efba8
-- SHA256 leadworkbench/market_store.py: 670f0c446adb9259b82b003f99b5074a32470c6345a76fc3f9a2014fa27c5c42
-- SHA256 leadworkbench/social_store.py: 239b934cfc0981905fc296fcbe102844263219ae0f134e505c4d09857066b883
-- SHA256 leadworkbench/social_discovery_store.py: 68ff3844a4eb744f6c48049758987001bea22e95c750e2a34778c469d7418a65
-- SHA256 leadworkbench/maps_policy.py: cf01759b27f08cb3b879cd34acb93fe09ee8a087846e5349169650b99ad25a02
-- SHA256 leadworkbench/market_social.py: 456f33281748c403eb58977071fd54ee98ab29c3bf5ae9598f74cf2967b9a510
-- SHA256 leadworkbench/social_policy.py: e9e7e023a31702bf067f99d6d56def2dba7b10e12f74b9a393ba4a9ccf56f00b
-- SHA256 leadworkbench/website_policy.py: dab383da6e97df47b5e2d36eec71a0a68b6434798bb35cc081d4ff2af64d3698
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE companies(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,address TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',
 website TEXT NOT NULL DEFAULT '',city TEXT NOT NULL DEFAULT '',country TEXT NOT NULL DEFAULT 'US',
 business_types TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'candidate',notes TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1,source_kind TEXT NOT NULL,created_at TEXT NOT NULL,last_checked_at TEXT,
 overrides TEXT NOT NULL DEFAULT '[]',social_links TEXT NOT NULL DEFAULT '[]',merged_into TEXT
);

CREATE TABLE listings(
 source_key TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_url TEXT NOT NULL,
 first_seen TEXT NOT NULL,last_seen TEXT NOT NULL
);

CREATE TABLE locations(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_key TEXT UNIQUE,
 address TEXT,latitude REAL,longitude REAL,source_url TEXT,observed_at TEXT
);

CREATE TABLE observations(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),field TEXT NOT NULL,value TEXT NOT NULL,
 source_url TEXT NOT NULL,evidence TEXT NOT NULL,observed_at TEXT NOT NULL,method TEXT NOT NULL,
 run_id TEXT,query TEXT,fingerprint TEXT UNIQUE
);

CREATE TABLE contacts(
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),name TEXT,role TEXT,email TEXT,url TEXT,
 kind TEXT,role_status TEXT,association_status TEXT,source_url TEXT,evidence TEXT,observed_at TEXT,
 review_status TEXT NOT NULL DEFAULT 'pending',fingerprint TEXT UNIQUE
);

CREATE TABLE changes(
 id TEXT PRIMARY KEY,company_id TEXT REFERENCES companies(id),field TEXT,old_value TEXT,new_value TEXT,
 created_at TEXT,reason TEXT
);

CREATE TABLE reviews(
 id TEXT PRIMARY KEY,company_id TEXT REFERENCES companies(id),kind TEXT,field TEXT,old_value TEXT,new_value TEXT,
 source_url TEXT,evidence TEXT,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT,fingerprint TEXT UNIQUE
);

CREATE TABLE tasks(
 id TEXT PRIMARY KEY,name TEXT,country TEXT,state TEXT,city TEXT,keywords TEXT,company_types TEXT,
 "limit" INTEGER,auto_enrich INTEGER,created_at TEXT
);

CREATE TABLE runs(
 id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),kind TEXT,company_id TEXT,status TEXT,progress TEXT,
 message TEXT NOT NULL DEFAULT '',error TEXT,created_at TEXT,finished_at TEXT,snapshot TEXT
);

CREATE TABLE work_items(
 id TEXT PRIMARY KEY,run_id TEXT REFERENCES runs(id),stage TEXT,target TEXT,state TEXT,result TEXT,
 UNIQUE(run_id,stage,target)
);

CREATE TABLE schedules(
 id TEXT PRIMARY KEY,task_id TEXT REFERENCES tasks(id),company_id TEXT REFERENCES companies(id),kind TEXT,
 interval_months INTEGER,timezone TEXT,next_due_at TEXT,enabled INTEGER,anchor TEXT
);

CREATE TABLE occurrences(
 schedule_id TEXT,due_at TEXT,run_id TEXT,PRIMARY KEY(schedule_id,due_at)
);

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

CREATE TABLE maps_access_policy(
 id INTEGER PRIMARY KEY CHECK(id=1), not_before REAL NOT NULL DEFAULT 0,
 manual_required INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL DEFAULT '');

CREATE TABLE listing_aliases(
            alias TEXT PRIMARY KEY,source_key TEXT NOT NULL REFERENCES listings(source_key));

CREATE TABLE task_metadata(
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                created_by_user_id TEXT DEFAULT NULL);

CREATE TABLE task_geography(
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                payload TEXT NOT NULL);

CREATE TABLE contact_decisions(
                contact_id TEXT PRIMARY KEY REFERENCES contacts(id),
                adoption_status TEXT NOT NULL,decided_at TEXT NOT NULL);

CREATE TABLE contact_versions(
                id TEXT PRIMARY KEY,contact_id TEXT NOT NULL REFERENCES contacts(id),
                payload TEXT NOT NULL,observed_at TEXT NOT NULL);

CREATE TABLE company_manual_checks(
                company_id TEXT PRIMARY KEY REFERENCES companies(id),checked_at TEXT NOT NULL);

CREATE TABLE social_entities(
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE company_social(
            company_id TEXT NOT NULL REFERENCES companies(id), entity_id TEXT NOT NULL REFERENCES social_entities(id),
            status TEXT NOT NULL DEFAULT 'candidate', scope TEXT NOT NULL DEFAULT 'unknown',
            revision INTEGER NOT NULL DEFAULT 1, decided_at TEXT, PRIMARY KEY(company_id,entity_id));

CREATE TABLE social_evidence(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),entity_id TEXT REFERENCES social_entities(id),
            original_url TEXT NOT NULL,source_url TEXT NOT NULL,evidence TEXT NOT NULL,
            method TEXT NOT NULL,observed_at TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '');

CREATE TABLE social_decisions(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL,entity_id TEXT NOT NULL,
            old_status TEXT NOT NULL,new_status TEXT NOT NULL,scope TEXT NOT NULL,decided_at TEXT NOT NULL);

CREATE TABLE social_outreach(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),entity_id TEXT NOT NULL REFERENCES social_entities(id),
            state TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);

CREATE TABLE social_migrations(name TEXT PRIMARY KEY);

CREATE TABLE social_config(key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE social_runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,
            finished_at TEXT,company_ids TEXT NOT NULL,settings TEXT NOT NULL,sample TEXT NOT NULL);

CREATE TABLE social_work(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES social_runs(id),
            company_id TEXT NOT NULL REFERENCES companies(id),stage TEXT NOT NULL,target TEXT NOT NULL,
            state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,finished_at TEXT,
            UNIQUE(run_id,company_id,stage,target));

CREATE TABLE social_page_observations(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL REFERENCES social_entities(id),
            run_id TEXT NOT NULL REFERENCES social_runs(id),payload TEXT NOT NULL,observed_at TEXT NOT NULL);

CREATE TABLE social_platform_gates(
 platform TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'ready',
 not_before REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
 failures INTEGER NOT NULL DEFAULT 0);

CREATE TABLE task_social_settings(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), payload TEXT NOT NULL);

CREATE TABLE market_social_runs(
            market_run_id TEXT PRIMARY KEY REFERENCES runs(id),
            social_run_id TEXT NOT NULL UNIQUE REFERENCES social_runs(id));

-- Website policy added by startup audit migration.
CREATE TABLE website_host_gates(
 host TEXT PRIMARY KEY, gate TEXT NOT NULL DEFAULT '{}',
 last_request REAL NOT NULL DEFAULT 0, min_interval REAL NOT NULL DEFAULT 2);

-- Lazy runtime table, DDL extracted from record_market_exhausted.
CREATE TABLE market_exhausted_items(
                item_id TEXT PRIMARY KEY REFERENCES work_items(id),reason TEXT NOT NULL);

CREATE INDEX observations_company ON observations(company_id,observed_at);

CREATE INDEX work_items_run ON work_items(run_id,state);

CREATE INDEX market_membership_company ON market_memberships(company_id,market_id);

CREATE INDEX run_companies_market ON run_companies(market_id,run_id,segment,changed);

CREATE INDEX market_runs_market ON market_runs(market_id,run_id);

CREATE INDEX listing_alias_source ON listing_aliases(source_key);

CREATE INDEX social_evidence_company ON social_evidence(company_id,entity_id);

CREATE INDEX social_work_state ON social_work(state,run_id);
PRAGMA user_version=2;
COMMIT;
