"""Market-owned social pipelines, configured separately from geographic identity."""
import json
import uuid
from datetime import datetime, timezone

from .social import classify_link
from .social_policy import PLATFORMS, DDL as POLICY_DDL, public_gate
from .errors import Invalid, NotFound


def stamp():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def options(payload):
    enabled = payload.get("auto_social", False)
    platforms = list(dict.fromkeys(payload.get("social_platforms", list(PLATFORMS))))
    posts = payload.get("social_read_posts", False)
    if type(enabled) is not bool or type(posts) is not bool:
        raise Invalid("社媒开关必须为布尔值")
    if any(p not in PLATFORMS for p in platforms) or (enabled and not platforms):
        raise Invalid("启用社媒检索时请至少选择一个受支持的平台")
    return dict(auto_social=enabled, social_platforms=platforms, social_read_posts=posts)


MARKET_SOCIAL_DDL = """
        CREATE TABLE IF NOT EXISTS task_social_settings(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS market_social_runs(
            market_run_id TEXT PRIMARY KEY REFERENCES runs(id),
            social_run_id TEXT NOT NULL UNIQUE REFERENCES social_runs(id));
"""


class MarketSocialMixin:
    def _market_social_ddl(self, con):
        """0010: social_policy, task_social_settings, market_social_runs."""
        from .migrations import statements
        for statement in statements(POLICY_DDL + MARKET_SOCIAL_DDL):
            con.execute(statement)

    def _task_social_options(self, con, task_id):
        row = con.execute("SELECT payload FROM task_social_settings WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else options({})

    def set_task_social_settings(self, task_id, payload):
        value = options(payload)
        with self.connect() as con:
            self._begin(con)
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise NotFound("市场不存在")
            con.execute("INSERT INTO task_social_settings VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload",
                        (task_id, json.dumps(value)))
        return self.task(task_id)

    def patch_task_social_settings(self, task_id, changes):
        """Merge partial changes into the stored settings inside one write transaction."""
        with self.connect() as con:
            self._begin(con)
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise NotFound("记录不存在")
            value = options({**self._task_social_options(con, task_id), **changes})
            con.execute("INSERT INTO task_social_settings VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload",
                        (task_id, json.dumps(value)))
        return self.task(task_id)

    def _market_social_run(self, con, parent):
        link = con.execute("SELECT social_run_id FROM market_social_runs WHERE market_run_id=?", (parent["id"],)).fetchone()
        if link:
            return link[0]
        snapshot = parent["snapshot"] if isinstance(parent["snapshot"], dict) else json.loads(parent["snapshot"] or "{}")
        cfg = options(snapshot)
        if not cfg["auto_social"]:
            return None
        from .social_connections import load_connections
        settings = dict(platform_search=True, profile_only=False, read_only=False, searxng_url="",
            connections=load_connections(self.directory), market_options=cfg)
        rid = uid()
        con.execute("INSERT INTO social_runs VALUES(?,?,?,NULL,?,?,?)",
            (rid, "paused" if parent["status"] == "paused" else "queued", stamp(), "[]", json.dumps(settings), "[]"))
        con.execute("INSERT INTO market_social_runs VALUES(?,?)", (parent["id"], rid))
        return rid

    def _enqueue_market_social(self, con, run_id, company_id):
        parent = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        company = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        if not parent or not company or parent["status"] not in {"queued", "running", "paused"}:
            return
        if company["merged_into"] or company["status"] == "do_not_contact":
            return
        rid = self._market_social_run(con, parent)
        if not rid:
            return
        run = con.execute("SELECT company_ids,settings,status FROM social_runs WHERE id=?", (rid,)).fetchone()
        if run["status"] == "cancelled":
            return
        ids = json.loads(run["company_ids"])
        cfg = json.loads(run["settings"])["market_options"]
        if company_id not in ids:
            ids.append(company_id)
            con.execute("UPDATE social_runs SET company_ids=? WHERE id=?", (json.dumps(ids), rid))
            for platform in cfg["social_platforms"]:
                self._social_item(con, rid, company_id, "platform_search", platform)
        for entry in con.execute("""SELECT e.url FROM company_social cs JOIN social_entities e ON e.id=cs.entity_id
            WHERE cs.company_id=? AND cs.status!='excluded' AND e.kind='account' ORDER BY e.created_at,e.id""", (company_id,)):
            self._market_profile_item(con, rid, company_id, entry["url"], cfg)
        con.execute("UPDATE social_runs SET status=?,finished_at=NULL WHERE id=?",
            ("paused" if parent["status"] == "paused" else "queued" if run["status"] != "running" else "running", rid))

    def _market_profile_item(self, con, rid, cid, url, cfg):
        entry = classify_link(url)
        if entry["kind"] != "account" or entry["platform"] not in cfg["social_platforms"]:
            return
        # Bound profile reads per enterprise, retaining discovered links as candidates.
        count = con.execute("SELECT count(*) FROM social_work WHERE run_id=? AND company_id=? AND stage='profile'", (rid, cid)).fetchone()[0]
        if count < 8:
            self._social_item(con, rid, cid, "profile", entry["url"])

    def _sync_market_social_links(self, con, company_id):
        # Website updates may arrive from a separate refresh, or after social search finishes.
        for row in con.execute("""SELECT m.market_run_id FROM market_social_runs m JOIN runs r ON r.id=m.market_run_id
            JOIN social_runs s ON s.id=m.social_run_id
            WHERE r.status IN ('queued','running','paused') AND s.status!='cancelled'
            AND EXISTS(SELECT 1 FROM json_each(s.company_ids) WHERE value=?)""", (company_id,)).fetchall():
            self._enqueue_market_social(con, row[0], company_id)

    def _social_parent(self, con, rid):
        row = con.execute("""SELECT r.* FROM market_social_runs m JOIN runs r ON r.id=m.market_run_id
            WHERE m.social_run_id=?""", (rid,)).fetchone()
        return dict(row) if row else None

    def _social_upstream_open(self, con, rid):
        parent = self._social_parent(con, rid)
        return bool(parent and con.execute("""SELECT 1 FROM work_items WHERE run_id=?
            AND state IN ('pending','running') LIMIT 1""", (parent["id"],)).fetchone())

    def _market_social_retryable(self, con, run_id):
        return bool(con.execute("""SELECT 1 FROM market_social_runs m JOIN social_work w ON w.run_id=m.social_run_id
            WHERE m.market_run_id=? AND (w.state IN ('pending','running','failed','blocked')
            OR json_extract(w.result,'$.partial')=1) LIMIT 1""", (run_id,)).fetchone())

    def _control_market_social(self, con, run_id, action):
        row = con.execute("SELECT social_run_id FROM market_social_runs WHERE market_run_id=?", (run_id,)).fetchone()
        if not row:
            return
        rid = row[0]
        if action == "cancel":
            con.execute("UPDATE social_runs SET status='cancelled',finished_at=? WHERE id=?", (stamp(), rid))
            con.execute("UPDATE social_work SET state='cancelled',finished_at=? WHERE run_id=? AND state IN ('pending','running')", (stamp(), rid))
        elif action == "pause":
            con.execute("UPDATE social_runs SET status='paused' WHERE id=? AND status IN ('queued','running')", (rid,))
            con.execute("UPDATE social_work SET state='pending' WHERE run_id=? AND state='running'", (rid,))
        elif action == "resume":
            con.execute("UPDATE social_runs SET status='queued',finished_at=NULL WHERE id=? AND status!='cancelled'", (rid,))
            con.execute("""UPDATE social_work SET state='pending',finished_at=NULL WHERE run_id=?
                AND (state IN ('blocked','failed') OR (state='succeeded' AND json_extract(result,'$.partial')=1))""", (rid,))

    def resume_social_platform(self, platform):
        from .social_policy import resume
        from .social_connections import load_connections
        from .social import HOSTS
        from .website_policy import resume as resume_hosts
        connections=load_connections(self.directory)
        search_endpoint=self.social_settings()["searxng_url"] if platform=="searxng" else None
        with self.connect() as con:
            self._begin(con)
            resume(con, platform)
            hosts=sorted({host.removeprefix("www.") for host,name in HOSTS.items() if name==platform})
            if hosts:
                gates={row["host"]:json.loads(row["gate"]) for row in con.execute(
                    f"SELECT host,gate FROM website_host_gates WHERE host IN ({','.join('?' for _ in hosts)}) AND gate!='{{}}'",
                    hosts)}
                # Same transaction as the platform reset: any live host
                # deadline rejects the entire operation, preserving both gates.
                resume_hosts(con, {"host_gates":gates})
            # Transport/login settings can be corrected while a round waits.
            # Manual queued work needs the same corrected connection as market
            # work. Keep completed/cancelled manual reports and business scope.
            for row in con.execute("""SELECT s.id,s.settings FROM social_runs s
                LEFT JOIN market_social_runs m ON m.social_run_id=s.id
                LEFT JOIN runs r ON r.id=m.market_run_id
                WHERE s.status!='cancelled' AND (
                    (m.social_run_id IS NULL AND s.status IN ('queued','running'))
                    OR r.status IN ('queued','running','paused'))""").fetchall():
                settings={**json.loads(row["settings"]),"connections":connections}
                if search_endpoint is not None:
                    settings["searxng_url"]=search_endpoint
                con.execute("UPDATE social_runs SET settings=? WHERE id=?",(json.dumps(settings),row["id"]))
        return dict(platform=platform, state="pending", message="已保留成功结果，将继续待执行任务")

    def start_market_social(self, task_id, platforms=None, read_posts=False):
        task = self.task(task_id)
        cfg = options(dict(auto_social=True, social_platforms=list(PLATFORMS) if platforms is None else platforms, social_read_posts=read_posts))
        with self.connect() as con:
            self._begin(con)
            companies = [r[0] for r in con.execute("""SELECT c.id FROM market_memberships m JOIN companies c ON c.id=m.company_id
                WHERE m.market_id=? AND c.merged_into IS NULL AND c.status!='do_not_contact'""", (task_id,))]
            if not companies:
                raise Invalid("当前市场尚无可检索企业，请先进行地图检索")
            row = con.execute("SELECT * FROM runs WHERE task_id=? AND status IN ('queued','running','paused') ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
            if row:
                parent = dict(row)
                linked = con.execute("""SELECT s.settings FROM market_social_runs m JOIN social_runs s ON s.id=m.social_run_id
                    WHERE m.market_run_id=?""", (parent["id"],)).fetchone()
                if linked and json.loads(linked[0])["market_options"] != cfg:
                    raise Invalid("当前轮次已有不同社媒配置，请完成或取消该轮次后再创建")
                snapshot = {**json.loads(parent["snapshot"]), **cfg}
                con.execute("UPDATE runs SET snapshot=? WHERE id=?", (json.dumps(snapshot), parent["id"]))
            else:
                rid = uid()
                snapshot = {**task, **cfg, "auto_enrich": False, "social_only": True}
                snapshot.pop("latest_run", None)
                con.execute("INSERT INTO runs VALUES(?,?,?,NULL,?,?,?,NULL,?,NULL,?)",
                    (rid, task_id, "social", "queued", "{}", "等待补充市场社媒资料", stamp(), json.dumps(snapshot)))
                parent = dict(con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())
            for cid in companies:
                self._enqueue_market_social(con, parent["id"], cid)
        self.progress(parent["id"])
        return self.run(parent["id"])


def social_workflow(con, parent):
    row = con.execute("""SELECT s.* FROM social_runs s JOIN market_social_runs m ON m.social_run_id=s.id
        WHERE m.market_run_id=?""", (parent["id"],)).fetchone()
    if not row:
        return None, None
    cfg = json.loads(row["settings"])["market_options"]
    work = [dict(w) for w in con.execute("""SELECT w.*,c.name FROM social_work w
        JOIN companies c ON c.id=w.company_id WHERE w.run_id=? ORDER BY w.rowid""", (row["id"],))]
    from .social_policy import platform_for

    def count_companies(items):
        counters = dict(total=0, processed=0, pending=0, running=0, succeeded=0, partial=0, failed=0, blocked=0, no_results=0)
        grouped={}
        for w in items:grouped.setdefault(w["company_id"],[]).append(w)
        for company_work in grouped.values():
            states = [w["state"] for w in company_work]
            counters["total"] += 1
            if "running" in states:
                counters["running"] += 1
            elif "pending" in states:
                counters["pending"] += 1
            else:
                counters["processed"] += 1
                errors = any(s in {"failed", "blocked", "cancelled"} for s in states) or any(json.loads(w["result"] or "{}").get("partial") for w in company_work)
                success = any(s in {"succeeded", "no_results"} for s in states)
                field = "partial" if errors and success else "blocked" if "blocked" in states else "failed" if errors else "succeeded"
                counters[field] += 1
                if all(s in {"no_results", "skipped"} for s in states):
                    counters["no_results"] += 1
        return counters

    stage = dict(key="social", title="社媒检索与补全", details=[], **count_companies(work))
    platforms = []
    for platform in cfg["social_platforms"]:
        items = [w for w in work if platform_for(w) == platform]
        counters = count_companies(items)
        value = public_gate(con, platform) if counters["pending"] else dict(state="pending", reason="", next_attempt_at=None, manual_required=False)
        if parent["status"] in {"paused", "cancelled"}:
            value.update(state=parent["status"], next_attempt_at=None, manual_required=False)
        elif counters["running"]:
            value.update(state="running")
        elif not counters["pending"]:
            value.update(state="needs_attention" if counters["partial"]+counters["failed"]+counters["blocked"] else "completed")
        platforms.append(dict(platform=platform, **counters, **value))
    current = next((f"{dict(facebook='Facebook',instagram='Instagram',x='Twitter（X）').get(platform_for(w),platform_for(w))} · {w['name']} · " +
        {"platform_search":"搜索账号", "profile":"补充账号资料", "read":"读取近期帖子"}.get(w["stage"],w["stage"])
        for w in work if w["state"] == "running"), "")
    accounts = {w["target"] for w in work if w["stage"] in {"profile", "read"} and classify_link(w["target"])["kind"] == "account"}
    for w in work:
        for link in json.loads(w["result"] or "{}").get("links",[]):
            entry=classify_link(link.get("url",""))
            if entry["kind"]=="account":accounts.add(entry["url"])
    posts = {p.get("url") for observation in con.execute("SELECT payload FROM social_page_observations WHERE run_id=?",(row["id"],))
        for p in json.loads(observation[0]).get("posts",[]) if p.get("url")}
    stage["details"]=["按企业计数；官网可能继续补充账号。每家企业本轮最多补全8个账号，其余候选保留在企业详情。"]
    return stage, dict(run_id=row["id"], current=current, accounts_found=len(accounts), posts_found=len(posts),
        read_posts=cfg["social_read_posts"], platforms=platforms)
