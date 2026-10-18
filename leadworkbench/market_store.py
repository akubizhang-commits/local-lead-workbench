"""Transactional market membership, run accounting and paginated library queries."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit, unquote
import uuid
from .company_comparison import distinct_branches


def stamp():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def market_name(payload):
    """A display label only; identity and queries continue to use structured fields."""
    location = " / ".join(str(payload.get(key) or "").strip()
                          for key in ("city", "state", "country"))
    labels = {"wholesale": "批发商", "retail": "零售商", "import": "进口贸易商"}
    types = "、".join(labels[key] for key in labels if key in payload.get("company_types", []))
    keywords = sorted({normalized(value) for value in payload.get("keywords", []) if normalized(value)})
    # Keep the list readable; full keywords remain available in task conditions.
    shown = [value if len(value) <= 40 else value[:39] + "…" for value in keywords[:2]]
    keyword_label = "、".join(shown)
    if len(keywords) > 2:
        keyword_label += f" 等{len(keywords)}个关键词"
    return " · ".join(value for value in (location, types, keyword_label) if value)


USABLE_FROM = "contact_facets f JOIN contacts ct ON ct.id=f.contact_id"
# Best entry of a company without usable contacts: 7 company phone, 8 none (design §6.1).
COMPANY_RANK_SQL = "CASE WHEN COALESCE(c.phone,'')<>'' THEN 7 ELSE 8 END"


def usable_contact_sql(alias="ct", facet="f"):
    """A contact that counts, exports and ranks; pure SQL over contact_facets (design §1.2)."""
    return (f"{facet}.channel_ok=1 AND {facet}.category!='irrelevant' AND "
            f"{alias}.review_status NOT IN ('pending','reject','invalid','superseded') AND NOT EXISTS "
            f"(SELECT 1 FROM contact_decisions d WHERE d.contact_id={alias}.id "
            "AND d.adoption_status='excluded')")


def source_identity(method, url):
    parts = urlsplit(str(url or ""))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
    if method == "maps":
        cid = next((v for k,v in query if k.lower() in {"cid","ludocid"} and v.isdigit()), None)
        match = re.search(r"0x[0-9a-fA-F]+:(0x[0-9a-fA-F]+)",unquote(str(url)))
        if cid or match:
            return "maps:cid", str(int(cid) if cid else int(match.group(1),16))
        query = [(k, v) for k, v in query if k.lower() in {"cid", "place_id", "query_place_id"}
                 or k.lower() == "q" and ("place_id:" in v or "cid:" in v)]
    host = parts.netloc.casefold().removeprefix("www.").removesuffix(":443").removesuffix(":80")
    return host, parts.path.rstrip("/") or "/", tuple(sorted(query))


def semantic_value(field, value):
    if field == "phone":
        from .company_comparison import phone_format_key
        return phone_format_key(value) or normalized(value)
    if field == "business_types":
        try:
            values = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            values = [value]
        if not isinstance(values, list):
            values = [values]
        return tuple(sorted({normalized(v) for v in values if normalized(v)}))
    if field == "website" and value:
        return source_identity("website", value if "://" in value else "https://" + value)
    return normalized(value)


def market_key(payload):
    values = [normalized(payload.get(k)) for k in ("country", "state", "city")]
    # A cross-county place remains one market whichever county selected it.
    if payload.get("place_id"):
        values = [normalized(payload.get("country")), payload["place_id"]]
    values += [sorted({normalized(v) for v in payload.get(k, []) if normalized(v)})
               for k in ("company_types", "keywords")]
    return hashlib.sha256(canonical(values).encode()).hexdigest()


MIGRATION = """
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


class MarketStoreMixin:
    def _migrate_source_aliases(self, con):
        """Idempotent extension migration; no merging of historical companies."""
        con.execute("""CREATE TABLE IF NOT EXISTS listing_aliases(
            alias TEXT PRIMARY KEY,source_key TEXT NOT NULL REFERENCES listings(source_key))""")
        con.execute("CREATE INDEX IF NOT EXISTS listing_alias_source ON listing_aliases(source_key)")
        from .sources.maps import listing_aliases
        for row in con.execute("""SELECT source_key,source_url FROM listings l WHERE NOT EXISTS
            (SELECT 1 FROM listing_aliases a WHERE a.alias=l.source_key)""").fetchall():
            for alias in listing_aliases(row["source_key"], row["source_url"]):
                con.execute("INSERT OR IGNORE INTO listing_aliases VALUES(?,?)", (alias, row["source_key"]))

    def _migrate_market(self, con):
        """One transaction, including legacy membership backfill and version bump."""
        con.execute("BEGIN IMMEDIATE")
        for statement in MIGRATION.split(";"):
            if statement.strip():
                con.execute(statement)
        con.execute('UPDATE tasks SET "limit"=0')
        for task in con.execute("SELECT * FROM tasks ORDER BY created_at,id").fetchall():
            con.execute("INSERT OR IGNORE INTO market_keys VALUES(?,?)",
                        (market_key(self.row(task)), task["id"]))
        for run in con.execute("SELECT * FROM runs WHERE task_id IS NOT NULL ORDER BY created_at,rowid").fetchall():
            con.execute("INSERT INTO market_runs(run_id,market_id) VALUES(?,?)", (run["id"], run["task_id"]))
            rows = con.execute("""SELECT company_id,min(observed_at) first_seen,max(observed_at) last_seen
                FROM observations WHERE run_id=? GROUP BY company_id""", (run["id"],)).fetchall()
            for row in rows:
                self._market_encounter(con, run["task_id"], run["id"], row["company_id"],
                                       row["first_seen"] or run["created_at"], row["last_seen"] or run["created_at"])
            if run["kind"] == "discovery" and run["status"] in {"queued", "running", "paused", "partial", "blocked", "failed"}:
                task = self.row(con.execute("SELECT * FROM tasks WHERE id=?", (run["task_id"],)).fetchone())
                legacy = json.loads(run["snapshot"] or "{}")
                snapshot = {**task, **legacy, "limit": 0, "mode": "market", "legacy_snapshot": legacy}
                snapshot["legacy_discovery_items"] = [dict(item) for item in con.execute(
                    "SELECT * FROM work_items WHERE run_id=? AND stage='discovery' ORDER BY rowid", (run["id"],))]
                # Archive exact old targets/results before replacing cap-based
                # work. Company records, observations and run classifications stay.
                con.execute("DELETE FROM work_items WHERE run_id=? AND stage='discovery'", (run["id"],))
                con.execute("INSERT OR IGNORE INTO work_items VALUES(?,?,'planning',?,'pending',NULL)",
                            (ident(), run["id"], run["task_id"]))
                progress = json.loads(run["progress"] or "{}")
                stats = self._market_stats(con, run["task_id"], run["id"])
                progress.update(new=stats["new"], existing=stats["existing"], changed=stats["changed"],
                                market_total=stats["total"], discovered=stats["new"] + stats["existing"],
                                discovery_total=0, discovery_done=0)
                progress["total"] = con.execute("SELECT count(*) FROM work_items WHERE run_id=?", (run["id"],)).fetchone()[0]
                progress["done"] = con.execute("""SELECT count(*) FROM work_items WHERE run_id=?
                    AND state IN ('succeeded','partial','failed','blocked')""", (run["id"],)).fetchone()[0]
                con.execute("""UPDATE runs SET snapshot=?,progress=?,status=?,
                    message='历史任务已迁移为市场规划，已保留发现结果' WHERE id=?""",
                    (canonical(snapshot), canonical(progress), "queued" if run["status"] == "running" else run["status"], run["id"]))
        con.execute("PRAGMA user_version=2")

    def create_market(self, payload):
        from .regions import validate_selection
        from .market_social import options
        social_options = options(payload)
        payload = validate_selection(payload)
        key = market_key({**payload, "country": payload.get("country") or "US"})
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT task_id FROM market_keys WHERE identity=?", (key,)).fetchone()
            legacy_keys = []
            # Reuse a pre-catalogue market only if the state's place name is
            # unambiguous. Do not merge a city and CDP just because names match.
            if not prior and payload.get("place_id"):
                from .regions import index, unique_place_id
                place = index()[payload["place_id"]]
                for city_name in {place["name"], place["official_name"]}:
                    if unique_place_id(payload.get("country"), payload.get("state"), city_name) == payload["place_id"]:
                        legacy_keys.append(market_key({**payload, "place_id": None, "city": city_name}))
                matches = [con.execute("SELECT task_id FROM market_keys WHERE identity=?", (alias,)).fetchone()
                           for alias in legacy_keys]
                matches = [row for row in matches if row]
                if len({row[0] for row in matches}) == 1:
                    prior = matches[0]
                    con.execute("INSERT OR IGNORE INTO market_keys VALUES(?,?)", (key, prior[0]))
            if prior:
                task_id = prior[0]
            else:
                task_id = ident()
                con.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    task_id, market_name(payload),
                    str(payload.get("country") or "US").strip(), str(payload.get("state") or "").strip(),
                    str(payload.get("city") or "").strip(),
                    canonical(sorted({normalized(v) for v in payload.get("keywords", []) if normalized(v)})),
                    canonical(sorted({normalized(v) for v in payload.get("company_types", []) if normalized(v)})),
                    0, int(payload.get("auto_enrich", True)), stamp()))
                con.execute("INSERT INTO market_keys VALUES(?,?)", (key, task_id))
                con.execute("INSERT INTO task_social_settings VALUES(?,?)", (task_id, canonical(social_options)))
            con.execute("INSERT OR IGNORE INTO task_metadata(task_id) VALUES(?)", (task_id,))
            for legacy_key in legacy_keys:
                con.execute("INSERT OR IGNORE INTO market_keys VALUES(?,?)", (legacy_key, task_id))
            if payload.get("geography"):
                con.execute("INSERT OR IGNORE INTO task_geography VALUES(?,?)",
                            (task_id, canonical(payload["geography"])))
        return self.task(task_id)

    def _market_run(self, con, run_id):
        return con.execute("SELECT * FROM market_runs WHERE run_id=?", (run_id,)).fetchone() if run_id else None

    def _market_encounter(self, con, market_id, run_id, company_id, first_seen, last_seen=None):
        last_seen = last_seen or first_seen
        prior = con.execute("SELECT 1 FROM market_memberships WHERE market_id=? AND company_id=?",
                            (market_id, company_id)).fetchone()
        con.execute("""INSERT INTO market_memberships VALUES(?,?,?,?,?,?)
            ON CONFLICT(market_id,company_id) DO UPDATE SET
            last_seen=max(last_seen,excluded.last_seen),last_run_id=excluded.last_run_id""",
                    (market_id, company_id, first_seen, last_seen, run_id, run_id))
        con.execute("""INSERT INTO run_companies VALUES(?,?,?,?,0,?,?)
            ON CONFLICT(run_id,company_id) DO UPDATE SET last_seen=max(last_seen,excluded.last_seen)""",
                    (run_id, company_id, market_id, "existing" if prior else "new", first_seen, last_seen))

    def _mark_market_changed(self, con, run_id, company_id):
        if run_id:
            con.execute("UPDATE run_companies SET changed=1 WHERE run_id=? AND company_id=?", (run_id, company_id))

    def _observation_source_identity(self, con, method, url):
        if method == "maps" and con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_aliases'").fetchone():
            from .sources.maps import listing_aliases
            keys = {row[0] for alias in listing_aliases("", url) if alias
                    for row in con.execute("SELECT source_key FROM listing_aliases WHERE alias=?", (alias,))}
            if len(keys) == 1:
                return ("maps:listing", next(iter(keys)))
        return source_identity(method, url)

    def _market_observed_field(self, con, company, field, value, url, evidence, timestamp, method, run_id, query):
        if field == "business_types":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = [value]
            if not isinstance(value, list):
                return
            value = canonical(sorted(set(str(v) for v in value)))
        identity = self._observation_source_identity(con, method, url)
        # Compare the same source's history, not whichever website/Maps page
        # happened to run last. First-seen sources establish their own baseline.
        previous = next((row for row in con.execute("""SELECT value,source_url FROM observations
            WHERE company_id=? AND field=? AND method=? ORDER BY rowid DESC""",
            (company["id"], field, method))
            if self._observation_source_identity(con, method, row["source_url"]) == identity), None)
        old = company[field]
        manual = field in json.loads(company["overrides"]) and bool(semantic_value(field, old))
        material = previous is not None and semantic_value(field, previous["value"]) != semantic_value(field, value)
        run_created = con.execute("SELECT created_at FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        if (previous is None and not manual and company["created_at"] < run_created
                and not semantic_value(field, old) and semantic_value(field, value)):
            material = True  # A historical blank was filled, not initial ingestion.
        self._observe(con, company["id"], field, value, url, evidence, timestamp, method, run_id, query)
        if (semantic_value(field, old) != semantic_value(field, value)
                and (previous is None or material or old in ("", "[]"))):
            outcome = None
            if manual:
                outcome = self._review(con, company["id"], "field_update", field, old, value, url, evidence)
            else:
                con.execute(f"UPDATE companies SET {field}=?,revision=revision+1 WHERE id=?", (value, company["id"]))
            if (not manual or material) and outcome != "merged":
                con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)", (
                    ident(), company["id"], field, old, value, stamp(),
                    "市场来源变化；保留人工修正" if manual else "市场来源资料更新"))
        if material:
            self._mark_market_changed(con, run_id, company["id"])

    @staticmethod
    def _strong_identity(record, candidate, country, city, locations):
        if not normalized(country) or normalized(country) != normalized(candidate["country"]):
            return False
        if record.get("country") and normalized(record["country"]) != normalized(country):
            return False
        if normalized(record.get("name")) != normalized(candidate["name"]):
            return False
        address, prior_address = normalized(record.get("address")), normalized(candidate["address"])
        phone = re.sub(r"\D", "", str(record.get("phone") or ""))
        prior_phone = re.sub(r"\D", "", str(candidate["phone"] or ""))
        def domain(value):
            try:
                return (urlsplit(value if "://" in value else "https://" + value).hostname or "").lower().removeprefix("www.")
            except ValueError:
                return ""
        host = domain(record.get("website") or "")
        prior_host = domain(candidate["website"] or "")
        if phone and prior_phone and phone != prior_phone or host and prior_host and host != prior_host:
            return False
        def point(lat, lon):
            try:
                lat, lon = float(lat), float(lon)
                return (lat, lon) if math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180 else None
            except (TypeError, ValueError):
                return None
        current_point = point(record.get("latitude"), record.get("longitude"))
        known_points = [p for location in locations if (p := point(location["latitude"], location["longitude"])) is not None]
        nearby = False
        if current_point and known_points:
            for other in known_points:
                lat1, lon1, lat2, lon2 = map(math.radians, (*current_point, *other))
                haversine = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
                if 6371 * 2 * math.asin(min(1, math.sqrt(haversine))) > 0.5:
                    return False
            nearby = True
        locality = normalized(city)
        city_in_address = bool(locality and locality == normalized(candidate["city"]) and
            re.search(r"(?<!\w)" + re.escape(locality) + r"(?!\w)", address or prior_address))
        if not nearby and not city_in_address:
            return False
        if address and prior_address:
            return address == prior_address and bool(re.search(r"\d", address)) and len(address.split()) >= 4
        return bool(host and len(phone) >= 7 and host == prior_host and phone == prior_phone)

    def ingest_market(self, record, run_id, query, task_id):
        if not all(record.get(k) for k in ("source_key", "source_url", "name")):
            raise ValueError("自动发现结果缺少来源标识、链接或名称")
        timestamp = record.get("observed_at") or stamp()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            market = self._market_run(con, run_id)
            if not market or market["market_id"] != task_id:
                raise ValueError("市场与轮次不匹配")
            task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_aliases'").fetchone():
                self._migrate_source_aliases(con)
            from .sources.maps import listing_aliases
            aliases = set(listing_aliases(record["source_key"], record["source_url"]))
            aliases.update(record.get("source_aliases") or [])
            keys = {row[0] for alias in aliases for row in con.execute(
                """SELECT source_key FROM listing_aliases WHERE alias=?
                   UNION SELECT source_key FROM listings WHERE source_key=?""", (alias, alias))}
            if len(keys) > 1:
                raise ValueError("来源标识关联多个历史地点，需人工核对，未自动合并")
            listing_key = next(iter(keys), record["source_key"])
            listing = con.execute("SELECT company_id,last_seen FROM listings WHERE source_key=?", (listing_key,)).fetchone()
            if listing:
                known_cids = {row[0] for row in con.execute(
                    "SELECT alias FROM listing_aliases WHERE source_key=? AND alias LIKE 'google_maps:cid:%'",
                    (listing_key,))}
                incoming_cids = {a for a in aliases if a.startswith("google_maps:cid:")}
                if known_cids and incoming_cids and known_cids != incoming_cids:
                    raise ValueError("地点 CID 与已有别名冲突，需人工核对，未自动合并")
                for alias in aliases:
                    con.execute("INSERT OR IGNORE INTO listing_aliases VALUES(?,?)", (alias, listing_key))
            if listing:
                company_id = listing[0]
                seen = set()
                while company_id not in seen:
                    seen.add(company_id)
                    row = con.execute("SELECT merged_into FROM companies WHERE id=?", (company_id,)).fetchone()
                    if not row or not row[0]:
                        break
                    company_id = row[0]
                try:
                    older = datetime.fromisoformat(timestamp) < datetime.fromisoformat(listing["last_seen"])
                except (TypeError,ValueError):
                    older = False
                if older:
                    # Delayed old responses cannot roll a moved shop back to its old address.
                    # Their known identity still belongs to this market/run.
                    self._market_encounter(con, task_id, run_id, company_id, timestamp)
                    outcome = con.execute("SELECT segment,changed FROM run_companies WHERE run_id=? AND company_id=?",
                                          (run_id, company_id)).fetchone()
                    result = self.row(con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
                    result.update(_outcome=outcome["segment"], _changed=bool(outcome["changed"]))
                    self._enqueue_market_social(con, run_id, company_id)
                    return result
                con.execute("UPDATE listings SET last_seen=?,source_url=? WHERE source_key=?",
                            (timestamp, record["source_url"], listing_key))
            else:
                # Candidate search is intentionally conservative; never match on
                # domain alone, and never merge two known different addresses.
                con.create_function("market_normalize", 1, normalized, deterministic=True)
                candidates = con.execute("""SELECT * FROM companies WHERE merged_into IS NULL
                    AND market_normalize(name)=?""", (normalized(record["name"]),)).fetchall()
                matches = [r for r in candidates if self._strong_identity(
                    record, r, task["country"], task["city"], con.execute(
                        "SELECT latitude,longitude FROM locations WHERE company_id=?", (r["id"],)).fetchall())]
                if len(matches) == 1:
                    company_id = matches[0]["id"]
                else:
                    company_id = ident()
                    con.execute("""INSERT INTO companies(id,name,address,phone,website,city,country,source_kind,created_at)
                        VALUES(?,?,?,?,?,?,?,'maps',?)""", (
                        company_id, record["name"], record.get("address") or "", record.get("phone") or "",
                        record.get("website") or "", task["city"], task["country"], stamp()))
                    for candidate in candidates[:10]:
                        if distinct_branches(candidate, record):
                            continue
                        self._review(con, company_id, "duplicate_candidate", "company", candidate["id"],
                                     company_id, record["source_url"], "同名来源证据不足；不自动合并")
                con.execute("INSERT INTO listings VALUES(?,?,?,?,?)",
                            (record["source_key"], company_id, record["source_url"], timestamp, timestamp))
                con.execute("INSERT INTO locations VALUES(?,?,?,?,?,?,?,?)", (
                    ident(), company_id, record["source_key"], record.get("address"), record.get("latitude"),
                    record.get("longitude"), record["source_url"], timestamp))
            for alias in aliases:
                con.execute("INSERT OR IGNORE INTO listing_aliases VALUES(?,?)", (alias, listing_key))
            # A stable listing identity represents this location even after a move.
            con.execute("""UPDATE locations SET address=COALESCE(NULLIF(?,''),address),
                latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),source_url=?,observed_at=?
                WHERE source_key=?""", (record.get("address"),record.get("latitude"),record.get("longitude"),
                record["source_url"],timestamp,listing_key))
            self._market_encounter(con, task_id, run_id, company_id, timestamp)
            operating = record.get("operating_status")
            if operating in {"permanently_closed", "temporarily_closed", "operational"}:
                old_status = con.execute("SELECT value FROM observations WHERE company_id=? AND field='operating_status' ORDER BY observed_at DESC,rowid DESC LIMIT 1", (company_id,)).fetchone()
                self._observe(con, company_id, "operating_status", operating, record["source_url"],
                    record.get("status_evidence") or "Google Maps 明确营业状态", timestamp, "maps", run_id, query)
                if old_status and old_status[0] != operating:
                    self._mark_market_changed(con, run_id, company_id)
                    con.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?)", (ident(),company_id,"operating_status",old_status[0],operating,stamp(),"来源明确营业状态更新"))

            category = record.get("category")
            if category:
                self._observe(con, company_id, "category", category, record["source_url"],
                              f"Google Maps 来源类别：{category}；实际查询：{query}", timestamp, "maps", run_id, query)
                self._write_category(con, company_id, category, record["source_url"], timestamp)
            from .p0_migrations import TYPE_HINTS
            for kind in json.loads(task["company_types"] or "[]"):
                if kind in TYPE_HINTS:
                    con.execute("INSERT OR IGNORE INTO company_type_hints VALUES(?,?,?,?)",
                                (company_id, TYPE_HINTS[kind], task_id, stamp()))
            for field in ("name", "address", "phone", "website", "business_types"):
                value = record.get(field)
                if field == "business_types" and value is None and category:
                    if re.search(r"\bwholesal(?:e|er|ers)\b|批发|批發", str(category), re.I):
                        value = ["wholesaler"]
                    elif re.search(r"\bretail(?:er|ers)?\b|零售", str(category), re.I):
                        value = ["retailer"]
                company = dict(con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
                self._observed_field(con, company, field, value, record["source_url"],
                                     f"Google Maps 实际查询：{query}", timestamp, "maps", run_id, query)
            outcome = con.execute("SELECT segment,changed FROM run_companies WHERE run_id=? AND company_id=?",
                                  (run_id, company_id)).fetchone()
            result = self.row(con.execute("""SELECT c.*,(SELECT count(*) FROM contacts WHERE company_id=c.id)
                contact_count FROM companies c WHERE id=?""", (company_id,)).fetchone())
            result.update(_outcome=outcome["segment"], _changed=bool(outcome["changed"]))
            self._enqueue_market_social(con, run_id, company_id)
        return result

    def _append_market_jobs(self, con, run_id, jobs):
        if not self._market_run(con, run_id):
            raise ValueError("不是市场轮次")
        added = 0
        for job in jobs:
            if not isinstance(job, dict):
                raise ValueError("市场检索任务必须是对象")
            target = canonical(job)
            if job.get("key") and con.execute("""SELECT 1 FROM work_items
                WHERE run_id=? AND stage='discovery' AND
                CASE WHEN json_valid(target) THEN json_extract(target,'$.key') END=?""",
                    (run_id, job["key"])).fetchone():
                continue
            if con.execute("SELECT 1 FROM work_items WHERE run_id=? AND stage='discovery' AND target=?",
                           (run_id, target)).fetchone():
                continue
            count = con.execute("SELECT count(*) FROM work_items WHERE run_id=? AND stage='discovery'", (run_id,)).fetchone()[0]
            if count >= 4096:
                self._market_unresolved(con, run_id, "internal job budget (4096)")
                break
            con.execute("INSERT INTO work_items VALUES(?,?, 'discovery',?,'pending',NULL)", (ident(), run_id, target))
            added += 1
        return added

    def save_market_plan(self, run_id, plan):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if not self._market_run(con, run_id):
                raise ValueError("不是市场轮次")
            con.execute("UPDATE market_runs SET plan=? WHERE run_id=?", (canonical(plan), run_id))
            snapshot = json.loads(con.execute("SELECT snapshot FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            snapshot["market_plan"] = plan
            con.execute("UPDATE runs SET snapshot=? WHERE id=?", (canonical(snapshot), run_id))
            unresolved = plan.get("unresolved", plan.get("coverage", {}).get("unresolved", []))
            for reason in unresolved if isinstance(unresolved, list) else ([unresolved] if unresolved else []):
                self._market_unresolved(con, run_id, reason)
            self._append_market_jobs(con, run_id, plan.get("jobs", []))
        return self.run(run_id)

    def append_market_jobs(self, run_id, jobs):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            return self._append_market_jobs(con, run_id, jobs)

    def count_market_jobs(self, run_id):
        with self.connect() as con:
            return con.execute("SELECT count(*) FROM work_items WHERE run_id=? AND stage='discovery'", (run_id,)).fetchone()[0]

    def _market_unresolved(self, con, run_id, reason):
        row = self._market_run(con, run_id)
        if not row:
            raise ValueError("不是市场轮次")
        reasons = json.loads(row["unresolved"])
        reason = str(reason) if isinstance(reason, str) else canonical(reason)
        if reason and reason not in reasons:
            reasons.append(reason)
        con.execute("UPDATE market_runs SET unresolved=? WHERE run_id=?", (canonical(reasons), run_id))

    def record_market_unresolved(self, run_id, reason):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self._market_unresolved(con, run_id, reason)

    def record_market_exhausted(self, run_id, item_id, reason):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS market_exhausted_items(
                item_id TEXT PRIMARY KEY REFERENCES work_items(id),reason TEXT NOT NULL)""")
            con.execute("INSERT OR REPLACE INTO market_exhausted_items VALUES(?,?)", (item_id, reason))
            self._market_unresolved(con, run_id, reason)

    def _market_exhausted(self, con, run_id):
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_exhausted_items'").fetchone():
            return {}
        return {r["item_id"]: r["reason"] for r in con.execute("""SELECT e.* FROM market_exhausted_items e
            JOIN work_items w ON w.id=e.item_id WHERE w.run_id=?""", (run_id,))}

    def market_retryable_items(self, con, run_id):
        """Parent control must reset only these IDs, keeping exhausted leaves partial."""
        exhausted = self._market_exhausted(con, run_id)
        return [row[0] for row in con.execute(
            "SELECT id FROM work_items WHERE run_id=? AND state!='succeeded'", (run_id,))
            if row[0] not in exhausted]

    def market_resume_allowed(self, con, run_id):
        return bool(self.market_retryable_items(con, run_id)) or self._market_social_retryable(con,run_id)

    def market_item_exhausted(self, run_id, item_id):
        with self.connect() as con:
            return self._market_exhausted(con, run_id).get(item_id)

    def _market_stats(self, con, market_id, run_id=None):
        total = con.execute("""SELECT count(*) FROM market_memberships m JOIN companies c ON c.id=m.company_id
            WHERE m.market_id=? AND c.merged_into IS NULL""", (market_id,)).fetchone()[0]
        stats = {"total": total, "new": 0, "existing": 0, "changed": 0, "not_seen": total}
        if run_id:
            row = con.execute("""SELECT sum(segment='new') n,sum(segment='existing') e,sum(changed) c
                FROM run_companies WHERE run_id=? AND market_id=?""", (run_id, market_id)).fetchone()
            stats.update(new=row["n"] or 0, existing=row["e"] or 0, changed=row["c"] or 0)
            stats["not_seen"] = max(0, total - stats["new"] - stats["existing"])
        return stats

    def _market_coverage(self, con, run_id):
        market = self._market_run(con, run_id)
        if not market:
            return {"scope_label": "", "planned": 0, "done": 0, "unresolved": []}
        plan = json.loads(market["plan"])
        row = con.execute("""SELECT count(*) total,sum(state='succeeded') done,
            sum(state IN ('partial','failed','blocked')) incomplete FROM work_items
            WHERE run_id=? AND stage='discovery'""", (run_id,)).fetchone()
        reasons = json.loads(market["unresolved"])
        exhausted = self._market_exhausted(con, run_id)
        reasons.extend(exhausted.values())
        if plan.get("status") in {"partial", "blocked", "failed"}:
            reasons += plan.get("warnings") or ["Market planning incomplete"]
        if row["incomplete"]:
            reasons = reasons + [f"{row['incomplete']} discovery jobs incomplete"]
        coverage = dict(plan.get("coverage", {}))
        for key in ("attribution", "bbox", "reference_url"):
            if key not in coverage and key in plan:
                coverage[key] = plan[key]
        coverage.update(scope_label=plan.get("scope_label") or coverage.get("scope_label", ""),
                        planned=row["total"], done=row["done"] or 0,
                        capability_exhausted=bool(exhausted),
                        retryable=self.market_resume_allowed(con, run_id),
                        unresolved=list(dict.fromkeys(str(reason) for reason in reasons)))
        return coverage

    def _market_task_details(self, con, task, tier_stats=None):
        if task["limit"] != 0:
            return task
        run_id = task["latest_run"]["id"] if task.get("latest_run") else None
        if (task.get("latest_run") or {}).get("kind")=="social":
            discovery=con.execute("SELECT id FROM runs WHERE task_id=? AND kind='discovery' ORDER BY created_at DESC LIMIT 1",(task["id"],)).fetchone()
            run_id=discovery[0] if discovery else None
        task["mode"] = "market"
        task["market_stats"] = self._market_stats(con, task["id"], run_id)
        if not tier_stats or task["id"] not in tier_stats:
            tier_stats = self._market_tier_stats(con, [task["id"]])
        tiers = tier_stats[task["id"]]
        task["market_stats"]["excluded"] = tiers["excluded"]
        task["tier_stats"] = tiers["tier_stats"]
        task["coverage"] = self._market_coverage(con, run_id)
        if task.get("latest_run") and task["latest_run"]["kind"]!="social":
            task["latest_run"]["progress"].update(
                {k: task["market_stats"][k] for k in ("new", "existing", "changed")})
            task["latest_run"]["progress"]["market_total"] = task["market_stats"]["total"]
            task["latest_run"]["progress"]["discovery_total"] = task["coverage"]["planned"]
            task["latest_run"]["progress"]["discovery_done"] = con.execute("""SELECT count(*) FROM work_items
                WHERE run_id=? AND stage='discovery' AND state IN ('succeeded','partial','failed','blocked')""",
                (run_id,)).fetchone()[0]
            task["latest_run"]["coverage"] = task["coverage"]
        return task

    def _company_query(self, con, q, status, type, market_id, run_id, segment, contact="", category=(),
                       excluded="hide", best_tier=""):
        """Pure-SQL filters shared by the list, the legacy list, exports and category counts.

        Exclusion rules are materialized once into temp.excluded_ids for this connection."""
        if excluded not in {"hide", "only", "include"}:
            raise ValueError("无效排除口径")
        category = [category] if isinstance(category, str) else list(category or ())
        if len(category) > 20 or any(not isinstance(c, str) or not 1 <= len(c) <= 120 for c in category):
            raise ValueError("来源类别筛选最多 20 个，每个 1–120 个字符")
        if segment not in {"", "new", "existing", "changed", "not_seen"}:
            raise ValueError("无效市场分组")
        if contact not in {"", "email", "procurement"}:
            raise ValueError("无效联系方式筛选")
        from .contact_tiers import TIER_RANK
        if best_tier and best_tier not in TIER_RANK:
            raise ValueError("无效入口等级")
        if market_id and not con.execute("SELECT 1 FROM tasks WHERE id=?", (market_id,)).fetchone():
            raise KeyError("市场不存在")
        selected = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone() if run_id else None
        if run_id and not selected:
            raise KeyError("轮次不存在")
        if selected and not market_id:
            market_id = selected["task_id"] or ""
        if market_id and selected and selected["task_id"] != market_id:
            raise ValueError("市场与轮次不匹配")
        if market_id and not selected:
            selected = con.execute("SELECT * FROM runs WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                   (market_id,)).fetchone()
            run_id = selected["id"] if selected else ""
        clauses, params, joins, columns, join_params = ["c.merged_into IS NULL"], [], "", "", []
        if market_id:
            joins = """ JOIN market_memberships m ON m.company_id=c.id
                LEFT JOIN run_companies rc ON rc.company_id=c.id AND rc.run_id=?"""
            join_params.append(run_id or "")
            clauses.append("m.market_id=?")
            params.append(market_id)
            columns = """,COALESCE(rc.segment,'not_seen') market_segment,COALESCE(rc.changed,0) market_changed,
                m.first_seen,m.last_seen"""
            if selected:
                clauses.append("""(m.first_run_id IS NULL OR m.first_run_id IN
                    (SELECT id FROM runs WHERE created_at<=?))""")
                params.append(selected["created_at"])
            if segment == "changed":
                clauses.append("rc.changed=1")
            elif segment:
                clauses.append("COALESCE(rc.segment,'not_seen')=?")
                params.append(segment)
        elif run_id:
            clauses.append("EXISTS(SELECT 1 FROM observations o WHERE o.company_id=c.id AND o.run_id=?)")
            params.append(run_id)
        elif segment:
            raise ValueError("市场分组需要指定市场")
        if q:
            clauses.append("""(c.name LIKE ? OR c.city LIKE ? OR c.website LIKE ?
                OR c.address LIKE ? OR c.country LIKE ? OR EXISTS(
                    SELECT 1 FROM market_memberships region_m JOIN tasks region_t ON region_t.id=region_m.market_id
                    WHERE region_m.company_id=c.id AND region_t.state LIKE ?))""")
            params.extend([f"%{q}%"] * 6)
        if status:
            clauses.append("c.status=?")
            params.append(status)
        if type:
            aliases = next((group for group in (("wholesale", "wholesaler"), ("retail", "retailer"), ("import", "importer"))
                            if normalized(type) in group), (normalized(type),))
            marks = ",".join("?" for _ in aliases)
            # Market-defined types count only while the company has no recorded business type.
            clauses.append(f"""(EXISTS(SELECT 1 FROM json_each(c.business_types) WHERE lower(trim(value)) IN ({marks}))
                OR (c.business_types IN ('[]','') AND EXISTS(SELECT 1 FROM company_type_hints h
                    WHERE h.company_id=c.id AND h.business_type IN ({marks}))))""")
            params.extend(aliases)
            params.extend(aliases)
        keys = sorted({normalized(c) for c in category if normalized(c)})
        if keys:
            clauses.append("EXISTS(SELECT 1 FROM company_categories cc WHERE cc.company_id=c.id AND cc.category_key IN ("
                           + ",".join("?" for _ in keys) + "))")
            params.extend(keys)
        if contact:
            clauses.append(f"""EXISTS(SELECT 1 FROM {USABLE_FROM} WHERE f.company_id=c.id
                AND {usable_contact_sql()} AND {self._outreach_criteria(contact)})""")
        if best_tier:
            clauses.append(f"""COALESCE((SELECT min(f.tier_rank) FROM {USABLE_FROM} WHERE f.company_id=c.id
                AND {usable_contact_sql()}), {COMPANY_RANK_SQL})=?""")
            params.append(TIER_RANK[best_tier])
        scope = market_id or ""
        rules = self._materialize_exclusions(con, [scope])
        exclusion, exclusion_params = "", []
        if rules and excluded != "include":
            exclusion = (" AND " + ("NOT " if excluded == "hide" else "")
                         + "EXISTS(SELECT 1 FROM temp.excluded_ids x WHERE x.market_id=? AND x.company_id=c.id)")
            exclusion_params = [scope]
        return dict(joins=joins, base=" AND ".join(clauses), where=" AND ".join(clauses) + exclusion,
                    columns=columns, join_params=join_params, base_params=params,
                    params=params + exclusion_params, market_id=market_id, scope=scope, rules=rules)

    def _materialize_exclusions(self, con, market_ids):
        """Fill temp.excluded_ids(market_id, company_id, rule_id) for each scope; '' = not by market.

        Category rules win over keyword rules, and within a kind the earliest rule wins."""
        rules = [dict(r) for r in con.execute("SELECT * FROM exclusion_rules ORDER BY created_at,rowid")]
        if not rules:
            return False
        con.execute("""CREATE TEMP TABLE IF NOT EXISTS excluded_ids(market_id TEXT NOT NULL,company_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,PRIMARY KEY(market_id,company_id))""")
        con.execute("DELETE FROM temp.excluded_ids")
        keywords = [r for r in rules if r["kind"] == "keyword"]
        for scope in dict.fromkeys(market_ids):
            if scope:
                con.execute("""INSERT OR IGNORE INTO temp.excluded_ids SELECT ?,cc.company_id,r.id
                    FROM exclusion_rules r JOIN company_categories cc ON cc.category_key=r.value_key
                    WHERE r.kind='category' AND (r.scope='global' OR r.market_id=?)
                    AND EXISTS(SELECT 1 FROM market_memberships m WHERE m.company_id=cc.company_id AND m.market_id=?)
                    ORDER BY r.created_at,r.rowid""", (scope, scope, scope))
            else:
                con.execute("""INSERT OR IGNORE INTO temp.excluded_ids SELECT '',cc.company_id,r.id
                    FROM exclusion_rules r JOIN company_categories cc ON cc.category_key=r.value_key
                    WHERE r.kind='category' AND (r.scope='global' OR EXISTS(SELECT 1 FROM market_memberships m
                        WHERE m.company_id=cc.company_id AND m.market_id=r.market_id))
                    ORDER BY r.created_at,r.rowid""")
            if not keywords:
                continue
            groups = {}
            for rule in keywords:
                if rule["scope"] == "global" or rule["market_id"] == scope or not scope:
                    groups.setdefault("" if rule["scope"] == "global" or scope else rule["market_id"], []).append(rule)
            hits = []
            for group, group_rules in groups.items():
                pattern = re.compile("|".join(re.escape(r["value_key"]) for r in group_rules))
                member = scope or group
                sql = """SELECT c.id,c.name,group_concat(cc.category_key,'|') categories FROM companies c
                    LEFT JOIN company_categories cc ON cc.company_id=c.id WHERE c.merged_into IS NULL"""
                values = ()
                if member:
                    sql += " AND EXISTS(SELECT 1 FROM market_memberships m WHERE m.company_id=c.id AND m.market_id=?)"
                    values = (member,)
                for row in con.execute(sql + " GROUP BY c.id", values):
                    text = normalized(row["name"]) + "|" + (row["categories"] or "")
                    if pattern.search(text):
                        rule = next(r for r in group_rules if r["value_key"] in text)
                        hits.append((rule["created_at"], rule["id"], row["id"]))
            # Earliest rule first so INSERT OR IGNORE keeps it.
            con.executemany("INSERT OR IGNORE INTO temp.excluded_ids VALUES(?,?,?)",
                            [(scope, company_id, rule_id) for _, rule_id, company_id in sorted(hits)])
        return True

    @staticmethod
    def _outreach_criteria(category, facet="f"):
        if category == 'procurement':
            return f"{facet}.category='procurement'"
        return f"""{facet}.category='email' AND NOT EXISTS(
            SELECT 1 FROM contact_facets bf JOIN contacts buyer ON buyer.id=bf.contact_id
            WHERE bf.company_id={facet}.company_id AND bf.email_key={facet}.email_key
            AND bf.category='procurement' AND {usable_contact_sql('buyer', 'bf')})"""

    @staticmethod
    def _contact_stats_sql(where=""):
        """Per-company counts over usable contacts; same rules as the detail page."""
        return f"""SELECT f.company_id company_id,count(DISTINCT f.identity_key) contact_count,
            count(DISTINCT CASE WHEN {MarketStoreMixin._outreach_criteria('email')} THEN f.email_key END) email_count,
            count(DISTINCT CASE WHEN f.category='procurement'
                THEN CASE WHEN f.email_key!='' THEN f.email_key ELSE f.identity_key END END) procurement_count,
            min(f.tier_rank) best_rank,
            count(DISTINCT CASE WHEN f.tier_rank<=3 THEN f.identity_key END) named_count
            FROM {USABLE_FROM} WHERE {usable_contact_sql()} {where} GROUP BY f.company_id"""

    @staticmethod
    def _company_order(sort):
        orders = {
            "newest": ("c.created_at DESC,c.id", False),
            "procurement": ("COALESCE(s.procurement_count,0) DESC,COALESCE(s.email_count,0) DESC,c.created_at DESC,c.id", True),
            "email": ("COALESCE(s.email_count,0) DESC,COALESCE(s.procurement_count,0) DESC,c.created_at DESC,c.id", True),
            "updated": ("COALESCE(c.last_checked_at,c.created_at) DESC,c.created_at DESC,c.id", False),
            "tier": (f"COALESCE(s.best_rank,{COMPANY_RANK_SQL}),COALESCE(s.procurement_count,0) DESC,"
                     "c.created_at DESC,c.id", True),
        }
        if sort not in orders:
            raise ValueError("无效企业排序")
        order, stats = orders[sort]
        return " ORDER BY " + order, stats

    def _company_ids(self, con, query, sort, limit=None, offset=0):
        """Stage 1: ids (plus market columns) in display order, pure SQL."""
        order, needs_stats = self._company_order(sort)
        stats = f" LEFT JOIN ({self._contact_stats_sql()}) s ON s.company_id=c.id" if needs_stats else ""
        sql = ("SELECT c.id" + query["columns"] + " FROM companies c" + query["joins"] + stats
               + " WHERE " + query["where"] + order)
        values = query["join_params"] + query["params"]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            values = values + [limit, offset]
        return [dict(r) for r in con.execute(sql, values)]

    @staticmethod
    def _company_total(con, query):
        return con.execute("SELECT count(*) FROM companies c" + query["joins"] + " WHERE " + query["where"],
                           query["join_params"] + query["params"]).fetchone()[0]

    @staticmethod
    def _excluded_total(con, query):
        """Companies hidden by rules under the other current filters."""
        if not query["rules"]:
            return 0
        return con.execute("SELECT count(*) FROM companies c" + query["joins"] + " WHERE " + query["base"]
                           + " AND EXISTS(SELECT 1 FROM temp.excluded_ids x WHERE x.market_id=? AND x.company_id=c.id)",
                           query["join_params"] + query["base_params"] + [query["scope"]]).fetchone()[0]

    def _company_rows(self, con, keyed, query=None):
        """Stage 2: full rows for at most a page of ids, one batched query per kind of data."""
        result = []
        scope = query["scope"] if query and query["rules"] else None
        names = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM tasks")}
        for start in range(0, len(keyed), 500):
            chunk = keyed[start:start + 500]
            ids = [row["id"] for row in chunk]
            marks = ",".join("?" for _ in ids)
            companies = {r["id"]: self.row(r) for r in con.execute(f"SELECT * FROM companies WHERE id IN ({marks})", ids)}
            stats = {r["company_id"]: dict(r) for r in con.execute(
                self._contact_stats_sql(f"AND f.company_id IN ({marks})"), ids)}
            social = dict(con.execute(f"""SELECT cs.company_id,count(DISTINCT e.id) FROM company_social cs
                JOIN social_entities e ON cs.entity_id=e.id
                WHERE cs.company_id IN ({marks}) AND cs.status!='excluded' AND e.kind='account'
                AND NOT EXISTS(SELECT 1 FROM {USABLE_FROM} WHERE f.company_id=cs.company_id
                    AND {usable_contact_sql()} AND f.category='procurement'
                    AND rtrim(ct.url,'/')=rtrim(e.url,'/')) GROUP BY cs.company_id""", ids).fetchall())
            operating = {r["company_id"]: r for r in con.execute(f"""SELECT company_id,value,source_url,observed_at FROM
                (SELECT company_id,value,source_url,observed_at,ROW_NUMBER() OVER (PARTITION BY company_id
                    ORDER BY observed_at DESC,rowid DESC) rn FROM observations
                 WHERE field='operating_status' AND company_id IN ({marks})) WHERE rn=1""", ids)}
            checked = dict(con.execute(
                f"SELECT company_id,checked_at FROM company_manual_checks WHERE company_id IN ({marks})", ids).fetchall())
            categories = {}
            for r in con.execute(f"""SELECT company_id,category FROM company_categories WHERE company_id IN ({marks})
                    ORDER BY last_seen DESC,category""", ids):
                categories.setdefault(r[0], []).append(r[1])
            hints = {}
            for r in con.execute(f"""SELECT h.company_id,h.business_type,h.market_id FROM company_type_hints h
                    JOIN companies c ON c.id=h.company_id WHERE h.company_id IN ({marks})
                    AND c.business_types IN ('[]','') ORDER BY h.created_at,h.market_id""", ids):
                hints.setdefault(r[0], []).append(dict(business_type=r[1], market_id=r[2], market_name=names.get(r[2], "")))
            exclusions = {}
            if scope is not None:
                exclusions = {r[0]: dict(rule_id=r[1], kind=r[2], value=r[3], scope=r[4]) for r in con.execute(
                    f"""SELECT x.company_id,r.id,r.kind,r.value,r.scope FROM temp.excluded_ids x
                    JOIN exclusion_rules r ON r.id=x.rule_id WHERE x.market_id=? AND x.company_id IN ({marks})""",
                    [scope, *ids])}
            scans = {r["target"]: dict(state=r["state"], result=json.loads(r["result"] or "{}")) for r in con.execute(
                f"""SELECT target,state,result FROM (SELECT w.target,w.state,w.result,ROW_NUMBER() OVER (
                    PARTITION BY w.target ORDER BY r.created_at DESC,w.rowid DESC) rn FROM work_items w
                    JOIN runs r ON r.id=w.run_id WHERE w.stage='refresh' AND w.target IN ({marks})) WHERE rn=1""", ids)}
            for row in chunk:
                item = companies[row["id"]]
                counts = stats.get(row["id"], {})
                self._tier_fields(item, counts, scans.get(row["id"]))
                item.update(contact_count=counts.get("contact_count", 0), email_count=counts.get("email_count", 0),
                            procurement_count=counts.get("procurement_count", 0),
                            social_count=social.get(row["id"], 0))
                item.update({k: v for k, v in row.items() if k != "id"})
                if "market_changed" in item:
                    item["market_changed"] = bool(item["market_changed"])
                status = operating.get(row["id"])
                item["manual_checked_at"] = checked.get(row["id"])
                item["operating_status"] = status["value"] if status else "unknown"
                item["operating_source_url"] = status["source_url"] if status else ""
                item["operating_observed_at"] = status["observed_at"] if status else ""
                item["categories"] = categories.get(row["id"], [])
                item["type_hints"] = hints.get(row["id"], [])
                item["exclusion"] = exclusions.get(row["id"])
                result.append(item)
        return result

    @staticmethod
    def _tier_fields(item, counts, scan):
        from .contact_tiers import RANK_TIER, TIER_LABEL, company_best_rank, gap_reason
        rank = company_best_rank(counts.get("best_rank"), item.get("phone"))
        item.update(best_tier=RANK_TIER[rank], best_tier_label=TIER_LABEL[RANK_TIER[rank]], best_tier_rank=rank,
                    gap=gap_reason({**item, "named_count": counts.get("named_count", 0)}, rank, scan))
        return item

    def _company_tier(self, con, company):
        """best_tier/gap for one company (detail page); same rules as the list."""
        counts = con.execute(self._contact_stats_sql("AND f.company_id=?"), (company["id"],)).fetchone()
        scan = con.execute("""SELECT w.state,w.result FROM work_items w JOIN runs r ON r.id=w.run_id
            WHERE w.target=? AND w.stage='refresh' ORDER BY r.created_at DESC,w.rowid DESC LIMIT 1""",
                           (company["id"],)).fetchone()
        return self._tier_fields(company, dict(counts) if counts else {},
                                 dict(state=scan["state"], result=json.loads(scan["result"] or "{}")) if scan else None)

    def _market_tier_stats(self, con, market_ids):
        """{market_id: {tier_stats, excluded}} with one exclusion materialization and one GROUP BY."""
        from .contact_tiers import RANK_TIER, TIERS
        result = {mid: {"tier_stats": {code: 0 for code, _, _ in TIERS}, "excluded": 0} for mid in market_ids}
        if not market_ids:
            return result
        rules = self._materialize_exclusions(con, market_ids)
        marks = ",".join("?" for _ in market_ids)
        hidden = ("LEFT JOIN temp.excluded_ids x ON x.market_id=m.market_id AND x.company_id=m.company_id"
                  if rules else "")
        flag = "x.company_id IS NOT NULL" if rules else "0"
        for row in con.execute(f"""SELECT m.market_id,{flag} hidden,
                COALESCE(s.best_rank,{COMPANY_RANK_SQL}) rank,count(*) n
                FROM market_memberships m JOIN companies c ON c.id=m.company_id
                LEFT JOIN (SELECT f.company_id,min(f.tier_rank) best_rank FROM {USABLE_FROM}
                    WHERE {usable_contact_sql()} GROUP BY f.company_id) s ON s.company_id=m.company_id
                {hidden} WHERE m.market_id IN ({marks}) AND c.merged_into IS NULL
                GROUP BY m.market_id,hidden,rank""", market_ids):
            if row["hidden"]:
                result[row["market_id"]]["excluded"] += row["n"]
            else:
                result[row["market_id"]]["tier_stats"][RANK_TIER[row["rank"]]] += row["n"]
        return result

    def company_page(self, q="", status="", type="", market_id="", run_id="", segment="", page=1, page_size=50,
                     contact="", sort="newest", category=(), excluded="hide", best_tier=""):
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("无效页码")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise ValueError("每页条数须为1至1000")
        self._company_order(sort)
        self._refresh_facets()
        with self.connect() as con:
            query = self._company_query(con, q, status, type, market_id, run_id, segment, contact, category, excluded,
                                        best_tier)
            total = self._company_total(con, query)
            keyed = self._company_ids(con, query, sort, page_size, (page - 1) * page_size)
            items = self._company_rows(con, keyed, query)
            hidden = self._excluded_total(con, query)
        return {"items": items, "total": total, "page": page, "page_size": page_size, "excluded_total": hidden}

    def companies(self, q="", status="", type="", *, max_rows=None, market_id="", run_id="", segment="",
                  contact="", sort="newest", category=(), excluded="hide", best_tier=""):
        self._company_order(sort)
        self._refresh_facets()
        with self.connect() as con:
            query = self._company_query(con, q, status, type, market_id, run_id, segment, contact, category, excluded,
                                        best_tier)
            keyed = self._company_ids(con, query, sort, max_rows)
            return self._company_rows(con, keyed, query)

    def company_categories(self, market_id="", excluded="hide"):
        """Source categories with company counts under the market/exclusion scope (max 200)."""
        with self.connect() as con:
            query = self._company_query(con, "", "", "", market_id, "", "", "", (), excluded)
            rows = con.execute(f"""SELECT cc.category_key,count(DISTINCT cc.company_id) count,
                (SELECT x.category FROM company_categories x WHERE x.category_key=cc.category_key
                 ORDER BY x.last_seen DESC LIMIT 1) category
                FROM companies c{query["joins"]} JOIN company_categories cc ON cc.company_id=c.id
                WHERE {query["where"]} GROUP BY cc.category_key ORDER BY count DESC,cc.category_key LIMIT 200""",
                               query["join_params"] + query["params"]).fetchall()
        return [dict(category=r["category"], category_key=r["category_key"], count=r["count"]) for r in rows]

    def _rule_matches(self, con, rule):
        member = "" if rule["scope"] == "global" else rule["market_id"]
        membership = ("AND EXISTS(SELECT 1 FROM market_memberships m WHERE m.company_id=c.id AND m.market_id=?)"
                      if member else "")
        values = (member,) if member else ()
        if rule["kind"] == "category":
            return con.execute(f"""SELECT count(DISTINCT c.id) FROM companies c JOIN company_categories cc
                ON cc.company_id=c.id WHERE c.merged_into IS NULL AND cc.category_key=? {membership}""",
                               (rule["value_key"], *values)).fetchone()[0]
        count = 0
        for row in con.execute(f"""SELECT c.name,group_concat(cc.category_key,'|') categories FROM companies c
                LEFT JOIN company_categories cc ON cc.company_id=c.id WHERE c.merged_into IS NULL {membership}
                GROUP BY c.id""", values):
            count += rule["value_key"] in normalized(row["name"]) + "|" + (row["categories"] or "")
        return count

    def exclusion_rules(self, market_id=""):
        with self.connect() as con:
            if market_id and not con.execute("SELECT 1 FROM tasks WHERE id=?", (market_id,)).fetchone():
                raise KeyError("市场不存在")
            sql = "SELECT * FROM exclusion_rules"
            values = ()
            if market_id:
                sql += " WHERE scope='global' OR market_id=?"
                values = (market_id,)
            rules = [dict(r) for r in con.execute(sql + " ORDER BY created_at,rowid", values)]
            for rule in rules:
                rule["matched"] = self._rule_matches(con, rule)
            return rules

    def create_exclusion_rule(self, scope, kind, value, market_id=None):
        value = " ".join(str(value).split())
        key = normalized(value)
        if kind == "keyword" and len(key) < 2:
            raise ValueError("排除关键词至少 2 个字符")
        if not key:
            raise ValueError("排除值不能为空")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if scope == "market" and not con.execute("SELECT 1 FROM tasks WHERE id=?", (market_id,)).fetchone():
                raise KeyError("市场不存在")
            prior = con.execute("""SELECT * FROM exclusion_rules WHERE scope=? AND COALESCE(market_id,'')=?
                AND kind=? AND value_key=?""", (scope, market_id or "", kind, key)).fetchone()
            if prior:
                rule = dict(prior)
                rule.update(reused=True, matched=self._rule_matches(con, rule))
                return rule
            from .db import Conflict
            if con.execute("SELECT count(*) FROM exclusion_rules").fetchone()[0] >= 200:
                raise Conflict("排除规则已达上限 200 条，请先删除不用的规则")
            rule = dict(id=ident(), scope=scope, market_id=market_id if scope == "market" else None, kind=kind,
                        value=value, value_key=key, created_at=stamp())
            con.execute("INSERT INTO exclusion_rules VALUES(?,?,?,?,?,?,?)", tuple(rule.values()))
            rule.update(reused=False, matched=self._rule_matches(con, rule))
            return rule

    def delete_exclusion_rule(self, id):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("DELETE FROM exclusion_rules WHERE id=?", (id,)).rowcount:
                raise KeyError("记录不存在")

    def _write_category(self, con, company_id, category, source_url, timestamp):
        key = normalized(category)
        if key:
            con.execute("""INSERT INTO company_categories VALUES(?,?,?,?,?,?)
                ON CONFLICT(company_id,category_key) DO UPDATE SET
                category=CASE WHEN excluded.last_seen>=last_seen THEN excluded.category ELSE category END,
                source_url=CASE WHEN excluded.last_seen>=last_seen THEN excluded.source_url ELSE source_url END,
                first_seen=min(first_seen,excluded.first_seen),last_seen=max(last_seen,excluded.last_seen)""",
                        (company_id, key, str(category), source_url or "", timestamp, timestamp))
