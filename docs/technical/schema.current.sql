-- Generated 2026-10-20 from a temporary Store; no production DB opened.
-- Startup migrations (unified registry: 0001–0011, P0 ledger items, R01–R08, P1 batch 2 p1b_20261019_*); schema only, no data.
-- Not an upgrade migration; use only to inspect/reconstruct an empty schema.
-- SHA256 leadworkbench/api.py: 50b1f8004d47b0bf195f625143662230948cada17cccc6dcbd4bce7e9a75e124
-- SHA256 leadworkbench/db.py: e8635fc71304e465dbd4128dc2c6918c1025a4ef48b622fcf76951e6596f2d42
-- SHA256 leadworkbench/migrations.py: 70f862acfc5c996fb4adec545b799560f37f8074d7e31933585ae4cb039dec03
-- SHA256 leadworkbench/market_store.py: 08c6470af6b39d06e43783baff63954fa38762690e2bb2f68648312703f6494a
-- SHA256 leadworkbench/social_store.py: fed32f249f81063764318afe445fa596da5adf731017f936a7ed23738a2aa2f4
-- SHA256 leadworkbench/social_discovery_store.py: 4f2319d01f227a9a8779e2507a4e63319abe6e246e7fd25b45bf7e33672cf8d5
-- SHA256 leadworkbench/maps_policy.py: 3cefc797ace75cb7d59af5d0e23984bc95ef843b965fb3d83358ed15e2bdf3ac
-- SHA256 leadworkbench/website_policy.py: 56383c243026023dcab9562f90a8b107069ec4c468495bdaf61273bb4f304d78
-- SHA256 leadworkbench/market_social.py: c0ff78fce1001514c1778f6a6ceb6f00fc0289aa078d4f269fbb17ab4b40dca5
-- SHA256 leadworkbench/social_policy.py: ce6efa902e053d9d5f87916ee735d5ca849fbcb16a30c659fb6a51acd25a033f
-- SHA256 leadworkbench/p0_migrations.py: 8041c759b99663d2e231630855276175e70cf440cd8f32cd1f6d571fa39eb806
-- SHA256 leadworkbench/p1b_migrations.py: 74d25d1e98af1c820d077c71bbd557e4112f54267a5d21af5abb843614891f4e
-- SHA256 leadworkbench/market_admin.py: f195dc4d01d0ad5b4108cea21438499dc84b2d885636849c64b2257da1f4117a
-- SHA256 leadworkbench/review_cleanup.py: 4754c2e7d500a59ae65f3ffe77b9f4628452c36c9123c2b94ea207064553048e
-- SHA256 leadworkbench/company_cities.py: 104dd8c630bbd12a958ca56f7d1ded7d2e335825bee793c7b070a97098ece284
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE schema_migrations(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
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
CREATE TABLE website_host_gates(
 host TEXT PRIMARY KEY, gate TEXT NOT NULL DEFAULT '{}',
 last_request REAL NOT NULL DEFAULT 0, min_interval REAL NOT NULL DEFAULT 2);
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
CREATE TABLE review_evidence(
                review_id TEXT NOT NULL REFERENCES reviews(id),
                fingerprint TEXT NOT NULL UNIQUE,
                source_url TEXT,evidence TEXT,observed_at TEXT NOT NULL);
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
CREATE TABLE market_exhausted_items(
                item_id TEXT PRIMARY KEY REFERENCES work_items(id),reason TEXT NOT NULL);
CREATE TABLE contact_facets(
        contact_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, channel_ok INTEGER NOT NULL,
        category TEXT NOT NULL, tier TEXT NOT NULL, tier_rank INTEGER NOT NULL,
        identity_key TEXT NOT NULL, email_key TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL);
CREATE TABLE contact_facets_dirty(contact_id TEXT PRIMARY KEY);
CREATE TABLE manual_contacts(
        contact_id TEXT PRIMARY KEY REFERENCES contacts(id),
        phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE company_categories(
        company_id TEXT NOT NULL REFERENCES companies(id), category_key TEXT NOT NULL, category TEXT NOT NULL,
        source_url TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        PRIMARY KEY(company_id, category_key));
CREATE TABLE company_type_hints(
        company_id TEXT NOT NULL REFERENCES companies(id), business_type TEXT NOT NULL,
        market_id TEXT NOT NULL REFERENCES tasks(id), created_at TEXT NOT NULL,
        PRIMARY KEY(company_id, business_type, market_id));
CREATE TABLE exclusion_rules(
        id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('global','market')),
        market_id TEXT REFERENCES tasks(id), kind TEXT NOT NULL CHECK(kind IN ('category','keyword')),
        value TEXT NOT NULL, value_key TEXT NOT NULL, created_at TEXT NOT NULL,
        CHECK((scope='global' AND market_id IS NULL) OR (scope='market' AND market_id IS NOT NULL)));
CREATE TABLE contact_change_proposals(
        review_id TEXT PRIMARY KEY REFERENCES reviews(id),
        contact_id TEXT NOT NULL REFERENCES contacts(id),
        proposal TEXT NOT NULL, proposal_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','superseded')),
        origin TEXT NOT NULL DEFAULT 'proposal' CHECK(origin IN ('proposal','legacy','repair')),
        created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE task_state(
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), archived_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE market_changes(
        id TEXT PRIMARY KEY, market_id TEXT NOT NULL, field TEXT NOT NULL,
        old_value TEXT, new_value TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE market_recheck_runs(
        run_id TEXT PRIMARY KEY REFERENCES runs(id), market_id TEXT NOT NULL REFERENCES tasks(id),
        requested INTEGER NOT NULL, skipped TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE TABLE market_round_baselines(
        run_id TEXT NOT NULL REFERENCES runs(id), company_id TEXT NOT NULL REFERENCES companies(id),
        rank INTEGER NOT NULL, PRIMARY KEY(run_id, company_id));
CREATE TABLE market_round_summaries(
        run_id TEXT PRIMARY KEY REFERENCES runs(id), market_id TEXT NOT NULL REFERENCES tasks(id),
        payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE review_resolutions(
        review_id TEXT PRIMARY KEY REFERENCES reviews(id), previous_status TEXT NOT NULL,
        status TEXT NOT NULL, rule TEXT NOT NULL, batch_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE company_cities(
        company_id TEXT PRIMARY KEY REFERENCES companies(id), city TEXT NOT NULL, city_key TEXT NOT NULL,
        target_keys TEXT NOT NULL, target_scope TEXT NOT NULL, nearby_global INTEGER NOT NULL,
        parsed INTEGER NOT NULL);
CREATE TABLE company_cities_dirty(company_id TEXT PRIMARY KEY);
CREATE INDEX observations_company ON observations(company_id,observed_at);
CREATE INDEX work_items_run ON work_items(run_id,state);
CREATE INDEX market_membership_company ON market_memberships(company_id,market_id);
CREATE INDEX run_companies_market ON run_companies(market_id,run_id,segment,changed);
CREATE INDEX market_runs_market ON market_runs(market_id,run_id);
CREATE INDEX listing_alias_source ON listing_aliases(source_key);
CREATE INDEX review_evidence_review ON review_evidence(review_id);
CREATE INDEX social_evidence_company ON social_evidence(company_id,entity_id);
CREATE INDEX social_work_state ON social_work(state,run_id);
CREATE INDEX contacts_company ON contacts(company_id);
CREATE INDEX contacts_company_email ON contacts(company_id, lower(trim(email)));
CREATE INDEX contact_versions_contact ON contact_versions(contact_id, observed_at);
CREATE INDEX reviews_status ON reviews(status, created_at);
CREATE INDEX reviews_company_field ON reviews(company_id, kind, field, status);
CREATE INDEX reviews_contact ON reviews(kind, new_value, status);
CREATE INDEX locations_company ON locations(company_id);
CREATE INDEX changes_company ON changes(company_id, created_at);
CREATE INDEX observations_company_field ON observations(company_id, field, observed_at);
CREATE INDEX companies_newest ON companies(created_at DESC, id);
CREATE INDEX companies_status ON companies(status);
CREATE INDEX listings_company ON listings(company_id);
CREATE INDEX work_items_target ON work_items(target, stage);
CREATE INDEX contact_facets_company ON contact_facets(company_id, category, tier_rank);
CREATE INDEX contact_facets_company_email ON contact_facets(company_id, email_key);
CREATE INDEX company_categories_key ON company_categories(category_key, company_id);
CREATE UNIQUE INDEX exclusion_rules_unique
        ON exclusion_rules(scope, COALESCE(market_id,''), kind, value_key);
CREATE INDEX contact_proposals_contact ON contact_change_proposals(contact_id, proposal_key, status);
CREATE INDEX market_changes_market ON market_changes(market_id, created_at);
CREATE INDEX market_recheck_market ON market_recheck_runs(market_id, created_at);
CREATE UNIQUE INDEX schedules_one_enabled_market
    ON schedules(task_id) WHERE enabled=1 AND task_id IS NOT NULL;
CREATE INDEX market_round_summaries_market ON market_round_summaries(market_id, created_at);
CREATE INDEX company_cities_key ON company_cities(city_key, company_id);
CREATE TRIGGER contacts_facets_insert AFTER INSERT ON contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty(contact_id) VALUES(new.id); END;
CREATE TRIGGER contacts_facets_update
        AFTER UPDATE OF company_id,name,role,email,url,kind,role_status,association_status,evidence ON contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty(contact_id) VALUES(new.id); END;
CREATE TRIGGER contacts_facets_delete AFTER DELETE ON contacts
        BEGIN DELETE FROM contact_facets WHERE contact_id=old.id;
              DELETE FROM contact_facets_dirty WHERE contact_id=old.id; END;
CREATE TRIGGER manual_contacts_facets_insert AFTER INSERT ON manual_contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty(contact_id) VALUES(new.contact_id); END;
CREATE TRIGGER manual_contacts_facets_update AFTER UPDATE ON manual_contacts
        BEGIN INSERT OR IGNORE INTO contact_facets_dirty(contact_id) VALUES(new.contact_id); END;
CREATE TRIGGER companies_city_insert AFTER INSERT ON companies
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.id); END;
CREATE TRIGGER companies_city_update AFTER UPDATE OF address,country,city ON companies
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.id); END;
CREATE TRIGGER memberships_city_insert AFTER INSERT ON market_memberships
        BEGIN INSERT OR IGNORE INTO company_cities_dirty(company_id) VALUES(new.company_id); END;
PRAGMA user_version=2;
COMMIT;
