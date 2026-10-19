"""Durable social discovery jobs; cancelled work cannot apply late results."""
import json
import uuid
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlencode
from .social import search_queries
from .sources.social_discovery import validate_endpoint,PLATFORMS
from .social_run_policy import execution_signature
from .social_projection import project_account, social_target as classify_link
from .errors import Invalid, NotFound


def timestamp():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex


SOCIAL_DISCOVERY_DDL = '''
        CREATE TABLE IF NOT EXISTS social_config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS social_runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,
            finished_at TEXT,company_ids TEXT NOT NULL,settings TEXT NOT NULL,sample TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS social_work(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES social_runs(id),
            company_id TEXT NOT NULL REFERENCES companies(id),stage TEXT NOT NULL,target TEXT NOT NULL,
            state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,finished_at TEXT,
            UNIQUE(run_id,company_id,stage,target));
        CREATE TABLE IF NOT EXISTS social_page_observations(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL REFERENCES social_entities(id),
            run_id TEXT NOT NULL REFERENCES social_runs(id),payload TEXT NOT NULL,observed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS social_work_state ON social_work(state,run_id);
'''


class SocialDiscoveryStoreMixin:
    def _social_discovery_ddl(self,con):
        """0008."""
        from .migrations import statements
        for statement in statements(SOCIAL_DISCOVERY_DDL):
            con.execute(statement)
    def _social_work_missing_website(self,con):
        """R04."""
        con.execute('''UPDATE social_work SET state='skipped',result=json_set(result,'$.status','skipped','$.reason','missing_website')
            WHERE stage='website' AND target='' AND state='no_results' ''')
    def social_settings(self):
        with self.connect() as con:row=con.execute("SELECT value FROM social_config WHERE key='searxng_url'").fetchone()
        value=row['value'] if row else ''
        return {'searxng_url':value,'configured':bool(value),'platforms':PLATFORMS,
                'message':'已配置本机搜索服务，连接状态需测试' if value else '未配置自动搜索；官网发现和公开读取仍可运行'}

    def set_social_settings(self,value):
        value=validate_endpoint(value)
        with self.connect() as con:con.execute("INSERT INTO social_config VALUES('searxng_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(value,))
        return self.social_settings()

    def social_pilot_sample(self,con):
        rows=[dict(r) for r in con.execute("SELECT c.id,c.name,c.website,c.social_links,EXISTS(SELECT 1 FROM company_social cs WHERE cs.company_id=c.id AND cs.status!='excluded') has_social FROM companies c WHERE c.merged_into IS NULL AND c.status!='do_not_contact' ORDER BY c.id")]
        pools={'existing_links':[r for r in rows if r['has_social']],
               'website_only':[r for r in rows if r['website'] and not r['has_social']],
               'no_website':[r for r in rows if not r['website'] and not r['has_social']]}
        sample=[]
        for group,size in [('existing_links',8),('website_only',8),('no_website',4)]:
            sample.extend({'id':r['id'],'name':r['name'],'stratum':group} for r in pools[group][:size])
        picked={r['id'] for r in sample}
        for r in rows:
            if len(sample)>=20:break
            if r['id'] not in picked:sample.append({'id':r['id'],'name':r['name'],'stratum':'replacement'})
        return sample

    def _social_item(self,con,rid,cid,stage,target):
        con.execute('INSERT OR IGNORE INTO social_work(id,run_id,company_id,stage,target,state,created_at) VALUES(?,?,?,?,?,\'pending\',?)',
                    (uid(),rid,cid,stage,target,timestamp()))

    def create_social_run(self,company_ids=None,pilot=False,read_only=False,platform_search=False,profile_only=False,*,_retry_from=None):
        if profile_only and platform_search:raise Invalid('补充已有账号资料与站内搜索不能同时选择')
        if profile_only:read_only=True
        if read_only and platform_search:raise Invalid('读取已有账号与站内搜索不能同时选择')
        settings=self.social_settings()
        from .social_connections import load_connections
        settings['connections']=load_connections(self.directory)
        settings['read_only']=read_only
        settings['platform_search']=platform_search
        settings['profile_only']=profile_only
        with self.connect() as con:
            self._begin(con)
            sample=self.social_pilot_sample(con) if pilot else []
            ids=[r['id'] for r in sample] if pilot else list(dict.fromkeys(company_ids or []))
            if not ids or len(ids)>20:raise Invalid('请选择1–20家企业；试点自动分层选取最多20家')
            companies=[]
            for cid in ids:
                r=con.execute('SELECT * FROM companies WHERE id=? AND merged_into IS NULL',(cid,)).fetchone()
                if not r:raise NotFound("企业不存在或已合并")
                if r['status']=='do_not_contact':raise Invalid('拒绝联系的企业不加入社媒开发任务')
                companies.append(dict(r))
            if read_only and not any(con.execute("""SELECT 1 FROM company_social cs JOIN social_entities e ON cs.entity_id=e.id
                WHERE cs.company_id=? AND cs.status!='excluded' AND e.kind='account' LIMIT 1""",(c['id'],)).fetchone() for c in companies):
                raise Invalid('尚无可读取账号，请先添加候选账号链接或运行发现')
            if _retry_from:
                self._resume_social_website_hosts(con,_retry_from,ids)
            encoded=json.dumps(sorted(ids))
            signature = execution_signature(settings)
            existing = next((
                row for row in con.execute(
                    """SELECT id,settings FROM social_runs WHERE company_ids=? AND status IN ('queued','running')
                    AND NOT EXISTS(SELECT 1 FROM market_social_runs m WHERE m.social_run_id=social_runs.id)""",
                    (encoded,),
                )
                if execution_signature(json.loads(row["settings"])) == signature
            ), None)
            if existing:rid=existing['id']
            else:
                rid=uid();con.execute('INSERT INTO social_runs VALUES(?,\'queued\',?,NULL,?,?,?)',
                    (rid,timestamp(),encoded,json.dumps(settings),json.dumps(sample,ensure_ascii=False)))
                for c in companies:
                    if platform_search:
                        for platform in ('facebook','instagram','x'):
                            self._social_item(con,rid,c['id'],'platform_search',platform)
                        continue
                    if not read_only:self._social_item(con,rid,c['id'],'website',c['website'])
                    # Domain, company+city, public phone variants per platform, all recorded individually.
                    queries=[q for q in search_queries(c) if q['platform'] in {'Instagram','Facebook','TikTok'}]
                    if not read_only:
                        for q in queries:self._social_item(con,rid,c['id'],'search',q['query'])
                    # Existing links available even when search cannot run.
                    for r in con.execute('''SELECT e.url,e.platform FROM company_social cs JOIN social_entities e ON e.id=cs.entity_id
                            WHERE cs.company_id=? AND cs.status!='excluded' AND e.kind='account' ORDER BY e.platform,e.url''',(c['id'],)).fetchall():
                        if r['platform'] in PLATFORMS:self._social_item(con,rid,c['id'],'profile' if profile_only else 'read',r['url'])
        return self.social_run(rid)

    def social_run(self,rid):
        with self.connect() as con:
            run=con.execute('SELECT * FROM social_runs WHERE id=?',(rid,)).fetchone()
            if not run:raise NotFound("社媒记录不存在")
            out=dict(run);out.pop('settings')
            parent=self._social_parent(con,rid)
            if parent:out.update(parent_run_id=parent['id'],task_id=parent['task_id'])
            out['company_ids']=json.loads(out['company_ids']);out['sample']=json.loads(out['sample'])
            out['items']=[dict(r) for r in con.execute('''SELECT w.*,c.name company_name FROM social_work w
                    JOIN companies c ON c.id=w.company_id WHERE run_id=? ORDER BY w.rowid''',(rid,))]
            summary={k:0 for k in ['total','pending','running','succeeded','no_results','blocked','failed','cancelled','skipped','new_links']}
            summary['total']=len(out['items'])
            from . import social_policy
            out['platform_gates']={}
            for item in out['items']:
                item['result']=json.loads(item['result']);summary[item['state']]+=1
                summary['new_links']+=item['result'].get('new_links',0)
                item['result'].pop('claim_token',None)
                platform=social_policy.platform_for(item)
                if platform:
                    out['platform_gates'][platform]=social_policy.public_gate(con,platform)
            out['summary']=summary
            return out

    def social_runs(self,company_id=''):
        with self.connect() as con:
            if company_id:rows=con.execute('''SELECT DISTINCT r.id,r.created_at FROM social_runs r JOIN social_work w ON w.run_id=r.id
                WHERE w.company_id=? ORDER BY r.created_at DESC LIMIT 30''',(company_id,)).fetchall()
            else:rows=con.execute('SELECT id FROM social_runs ORDER BY created_at DESC LIMIT 30').fetchall()
        return [{k:v for k,v in self.social_run(r['id']).items() if k!='items'} for r in rows]

    def _finish_social_run(self,con,rid):
        parent=self._social_parent(con,rid)
        if parent and (parent['status'] in {'paused','cancelled'} or self._social_upstream_open(con,rid)):return
        states=[r[0] for r in con.execute('SELECT state FROM social_work WHERE run_id=?',(rid,))]
        if any(s in {'pending','running'} for s in states):return
        incomplete=con.execute("SELECT 1 FROM social_work WHERE run_id=? AND json_extract(result,'$.partial')=1 LIMIT 1",(rid,)).fetchone()
        status='partial' if incomplete or any(s in {'blocked','failed','cancelled'} for s in states) else 'completed'
        con.execute("UPDATE social_runs SET status=?,finished_at=? WHERE id=? AND status!='cancelled'",(status,timestamp(),rid))

    def _cached_social_result(self,con,item):
        if item['stage'] not in {'read','profile'}:
            return None
        # A newer partial/error attempt must not be hidden by an older success.
        previous=con.execute("""SELECT state,result FROM social_work WHERE run_id=? AND stage=? AND target=?
            AND id!=? AND json_extract(result,'$.status') IS NOT NULL
            AND COALESCE(json_extract(result,'$.cache_reused'),0)=0
            ORDER BY finished_at DESC,rowid DESC LIMIT 1""",
            (item['run_id'],item['stage'],item['target'],item['id'])).fetchone()
        if not previous or previous['state']!='succeeded':
            return None
        result=json.loads(previous['result'])
        if result.get('status')!='preview_read' or result.get('partial') or result.get('reason'):
            return None
        return {**result,'cache_reused':True}

    def claim_social_item(self):
        with self.connect() as con:
            self._begin(con)
            # Respect refusal decided after the job was queued.
            con.execute('''UPDATE social_work SET state='cancelled',result='{"message":"企业已拒绝联系或合并"}'
                WHERE state='pending' AND company_id IN (SELECT id FROM companies WHERE status='do_not_contact' OR merged_into IS NOT NULL)''')
            con.execute('''UPDATE social_work SET state='cancelled',result='{"message":"账号关联已不采用"}'
                WHERE state='pending' AND stage IN ('read','profile') AND EXISTS(
                    SELECT 1 FROM company_social cs JOIN social_entities e ON e.id=cs.entity_id
                    WHERE cs.company_id=social_work.company_id AND e.url=social_work.target AND cs.status='excluded')''')
            for r in con.execute("SELECT id FROM social_runs WHERE status IN ('queued','running')").fetchall():self._finish_social_run(con,r['id'])
            rows=con.execute('''SELECT w.*,r.settings FROM social_work w JOIN social_runs r ON r.id=w.run_id
                WHERE w.state='pending' AND r.status IN ('queued','running')
                ORDER BY (SELECT max(sw.finished_at) FROM social_work sw WHERE sw.run_id=r.id),
                    r.created_at,CASE w.stage WHEN 'website' THEN 0 WHEN 'search' THEN 1 ELSE 2 END,w.rowid''').fetchall()
            from . import social_policy
            row=None
            for candidate in rows:
                parent=self._social_parent(con,candidate['run_id'])
                if parent and parent['status'] not in {'queued','running'}:continue
                cached=self._cached_social_result(con,candidate)
                platform=social_policy.platform_for(candidate)
                if platform and not cached and not social_policy.allowed(con,platform):continue
                row=candidate
                break
            if not row:return None
            token=uid()
            metadata=json.loads(row['result'])
            metadata.update(claim_token=token,attempts=metadata.get('attempts',0)+1)
            con.execute("UPDATE social_work SET state='running',result=?,finished_at=NULL WHERE id=?",(json.dumps(metadata),row['id']))
            con.execute("UPDATE social_runs SET status='running' WHERE id=?",(row['run_id'],))
            if parent:
                con.execute("UPDATE runs SET status='running' WHERE id=? AND status='queued'",(parent['id'],))
            return {**dict(row),'settings':json.loads(row['settings']),'claim_token':token,'cached_result':cached}

    def social_item_active(self,item):
        with self.connect() as con:
            row=con.execute('SELECT state,result FROM social_work WHERE id=?',(item['id'],)).fetchone()
            run=con.execute('SELECT status FROM social_runs WHERE id=?',(item['run_id'],)).fetchone()
            parent=self._social_parent(con,item['run_id'])
            if self._social_target_excluded(con,item):
                return False
            return bool(row and row['state']=='running' and run['status']=='running'
                and json.loads(row['result']).get('claim_token')==item.get('claim_token')
                and (not parent or parent['status'] in {'queued','running'}))

    def _social_target_excluded(self,con,item):
        return item["stage"] in {"read","profile"} and con.execute("""SELECT 1 FROM company_social cs
            JOIN social_entities e ON e.id=cs.entity_id WHERE cs.company_id=? AND e.url=? AND cs.status='excluded'""",
            (item["company_id"],item["target"])).fetchone() is not None

    def release_social_item(self,item):
        with self.connect() as con:
            con.execute("""UPDATE social_work SET state='pending'
                WHERE id=? AND state='running' AND json_extract(result,'$.claim_token')=?""",
                (item['id'],item.get('claim_token')))

    def finish_social_item(self,item_id,result,claim_token=None):
        with self.connect() as con:
            self._begin(con)
            item=con.execute('SELECT * FROM social_work WHERE id=?',(item_id,)).fetchone()
            if not item:raise NotFound("社媒记录不存在")
            run=con.execute('SELECT status,settings FROM social_runs WHERE id=?',(item['run_id'],)).fetchone()
            company=con.execute('SELECT status,merged_into FROM companies WHERE id=?',(item['company_id'],)).fetchone()
            parent=self._social_parent(con,item['run_id'])
            metadata=json.loads(item['result'])
            if item['state']!='running' or run['status'] not in {'queued','running'}:return False
            if claim_token is not None and metadata.get('claim_token')!=claim_token:return False
            if parent and parent['status'] not in {'queued','running'}:return False
            if self._social_target_excluded(con,item):
                con.execute("UPDATE social_work SET state='cancelled',result=?,finished_at=? WHERE id=?",
                    (json.dumps(dict(message="账号关联已不采用，已丢弃本次读取结果")),timestamp(),item_id))
                self._finish_social_run(con,item["run_id"])
                return False
            if company['status']=='do_not_contact' or company['merged_into']:
                con.execute("UPDATE social_work SET state='cancelled',result=? WHERE id=?",(json.dumps({'message':'企业已拒绝联系或合并'}),item_id))
                self._finish_social_run(con,item['run_id']);return False
            result=dict(result); added=0
            result.update({k:metadata[k] for k in ('claim_token','attempts') if k in metadata})
            from . import social_policy
            needs_retry=social_policy.observe(con,item,result)
            # Manual runs keep their explicit retry/report semantics; their
            # outcomes still pace or pause every run that uses this platform.
            retry=bool(parent and needs_retry)
            if parent:
                if retry and result.get('status') not in {'preview_read','succeeded'}:
                    con.execute("UPDATE social_work SET state='pending',result=?,finished_at=? WHERE id=?",
                        (json.dumps(result,ensure_ascii=False),timestamp(),item_id))
                    return True
            links=list(result.get('links',[]))
            if item['stage']=='read':
                for post in result.get('posts',[])[:5]:
                    candidate=classify_link(post.get('url',''))
                    if candidate['kind']=='post' and candidate['platform']==classify_link(item['target'])['platform']:
                        links.append({'url':candidate['url'],'source_url':item['target'],
                            'evidence':post.get('text','')[:3000],'method':result.get('provider','browser')})
            if item['stage']=='search':
                provider=json.loads(run['settings']).get('searxng_url','')
                source=provider+'?'+urlencode({'q':item['target'],'format':'json','categories':'general'}) if provider else ''
                links=[{'url':r['url'],'source_url':source,'evidence':r['title']+' | '+r['description'],
                        'method':'search_snippet'} for r in result.get('results',[]) if classify_link(r['url'])['kind']!='excluded']
            for link in links:
                r=classify_link(link['url'])
                if r['kind']=='excluded':continue
                existed=con.execute('''SELECT 1 FROM company_social cs JOIN social_entities e ON e.id=cs.entity_id
                    WHERE cs.company_id=? AND e.url=?''',(item['company_id'],r['url'])).fetchone()
                hint=link.get('association_hint','')
                evidence=link.get('evidence','')
                if hint=='third_party_possible':evidence='可能为第三方/建站服务商链接；'+evidence
                if item['stage']=='search':evidence='搜索摘要，未读取平台原文。查询：'+item['target']+'；'+evidence
                saved=self._save_social(con,item['company_id'],r['url'],link.get('source_url',''),evidence,
                                      link.get('method','website'),timestamp())
                if not existed:added+=1
                association=con.execute('SELECT status FROM company_social WHERE company_id=? AND entity_id=?',(item['company_id'],saved['id'])).fetchone()
                if item['stage']=='platform_search' and association['status']!='excluded' and r['kind']=='account' and r['platform'] in PLATFORMS:
                    if parent:self._market_profile_item(con,item['run_id'],item['company_id'],r['url'],json.loads(run['settings'])['market_options'])
                    else:self._social_item(con,item['run_id'],item['company_id'],'profile',r['url'])
                if item['stage'] not in {'read','profile','platform_search'} and association['status']!='excluded' and hint!='third_party_possible' and r['kind'] in {'account','post'} and r['platform'] in PLATFORMS:
                    n=con.execute("SELECT count(*) FROM social_work WHERE run_id=? AND company_id=? AND stage='read'",(item['run_id'],item['company_id'])).fetchone()[0]
                    if n<8:self._social_item(con,item['run_id'],item['company_id'],'read',r['url'])
            result['new_links']=added
            if item['stage'] in {'read','profile'}:
                entity=con.execute('SELECT id FROM social_entities WHERE url=?',(item['target'],)).fetchone()
                if entity and not result.get('cache_reused'):
                    con.execute('INSERT INTO social_page_observations VALUES(?,?,?,?,?)',(uid(),entity['id'],item['run_id'],json.dumps(result,ensure_ascii=False),timestamp()))
            state={'preview_read':'succeeded','unreadable':'blocked','auth_required':'blocked','challenge':'blocked'}.get(result.get('status'),result.get('status','failed'))
            if state not in {'succeeded','no_results','blocked','failed','skipped'}:state='failed'
            if parent and not retry and item['stage']=='profile' and state=='succeeded' and json.loads(run['settings'])['market_options']['social_read_posts']:
                self._social_item(con,item['run_id'],item['company_id'],'read',item['target'])
            if retry:state='pending'
            con.execute('UPDATE social_work SET state=?,result=?,finished_at=? WHERE id=?',
                        (state,json.dumps(result,ensure_ascii=False),timestamp(),item_id))
            # Stop only the failed source, preserving unrelated website and platform work.
            reason=result.get('reason')
            if item['stage']=='search' and state=='blocked' and reason in {'not_configured','provider_unavailable'}:
                con.execute("UPDATE social_work SET state='blocked',result=?,finished_at=? WHERE run_id=? AND stage='search' AND state='pending'",
                    (json.dumps({'status':'blocked','reason':reason,'message':'搜索源已暂停：'+result.get('message','')}),timestamp(),item['run_id']))
            if not parent and item['stage'] in {'read','profile'} and (reason in {'rate_limited','network_error','access_denied'} or result.get('status') in {'auth_required','challenge'}):
                host=urlsplit(item['target']).hostname
                for pending in con.execute("SELECT id,target FROM social_work WHERE run_id=? AND stage IN ('read','profile') AND state='pending'",(item['run_id'],)).fetchall():
                    if urlsplit(pending['target']).hostname==host:
                        con.execute("UPDATE social_work SET state='blocked',result=?,finished_at=? WHERE id=?",(json.dumps({'status':'blocked','reason':'source_paused','message':'同平台读取暂缓：'+result.get('message','')}),timestamp(),pending['id']))
            self._finish_social_run(con,item['run_id'])
            return True

    def cancel_social_run(self,rid):
        with self.connect() as con:
            self._begin(con)
            if not con.execute('SELECT 1 FROM social_runs WHERE id=?',(rid,)).fetchone():raise NotFound("社媒记录不存在")
            if self._social_parent(con,rid):raise Invalid('请在市场任务中取消本轮，统一控制官网和社媒工作')
            con.execute("UPDATE social_runs SET status='cancelled',finished_at=? WHERE id=? AND status IN ('queued','running')",(timestamp(),rid))
            con.execute("UPDATE social_work SET state='cancelled',finished_at=? WHERE run_id=? AND state IN ('pending','running')",(timestamp(),rid))
        return {'ok':True}

    def retry_social_run(self,rid):
        run=self.social_run(rid)
        if run['status'] in {'queued','running'}:raise Invalid('当前任务尚未结束')
        with self.connect() as con:
            if self._social_parent(con,rid):raise Invalid('请在市场任务中继续本轮，或新建社媒补充轮次')
            settings=json.loads(con.execute('SELECT settings FROM social_runs WHERE id=?',(rid,)).fetchone()[0])
        return self.create_social_run(run['company_ids'],read_only=settings.get('read_only',False),platform_search=settings.get('platform_search',False),profile_only=settings.get('profile_only',False),_retry_from=rid)

    def _resume_social_website_hosts(self,con,rid,company_ids):
        from .website_policy import host_key, resume
        original=con.execute('SELECT status,company_ids FROM social_runs WHERE id=?',(rid,)).fetchone()
        if not original:raise NotFound("社媒记录不存在")
        if original['status'] in {'queued','running'}:raise Invalid('当前任务尚未结束')
        if self._social_parent(con,rid):raise Invalid('请在市场任务中继续本轮，或新建社媒补充轮次')
        if set(json.loads(original['company_ids']))!=set(company_ids):raise Invalid('重试企业范围已变化')
        gates={}
        for item in con.execute("""SELECT target,result FROM social_work WHERE run_id=?
            AND stage='website' AND state IN ('blocked','failed')""",(rid,)):
            access=json.loads(item['result']).get('website_access') or {}
            # A redirect may have encountered the gate on another host.
            # Only source-recorded access evidence belongs to this retry;
            # successful items, other platforms and unrelated hosts stay gated.
            host=access.get('host') or host_key(item['target'])
            row=con.execute("SELECT gate FROM website_host_gates WHERE host=? AND gate!='{}'",(host,)).fetchone()
            if row:gates[host]=json.loads(row['gate'])
        # Runs and gate releases commit together, after company validation.
        resume(con,{'host_gates':gates})

    def recover_social(self):
        with self.connect() as con:con.execute("UPDATE social_work SET state='pending' WHERE state='running'")

    def social_pages(self,cid):
        with self.connect() as con:
            from .sources.social_profile import profile_match
            company=dict(con.execute('SELECT website,phone FROM companies WHERE id=?',(cid,)).fetchone())
            rows=con.execute('''SELECT p.* FROM social_page_observations p JOIN company_social cs ON cs.entity_id=p.entity_id
                WHERE cs.company_id=? AND cs.status!='excluded' AND p.rowid=(SELECT max(p2.rowid) FROM social_page_observations p2 WHERE p2.entity_id=p.entity_id)''',(cid,)).fetchall()
            pages=[]
            for r in rows:
                observations = [
                    (json.loads(row["payload"]), row["observed_at"])
                    for row in con.execute(
                        "SELECT payload,observed_at FROM social_page_observations WHERE entity_id=? ORDER BY rowid DESC",
                        (r["entity_id"],),
                    )
                ]
                payload = project_account(observations)
                pages.append({**payload,'entity_id':r['entity_id'],
                              'match_signals':profile_match(company,payload.get('fields',[]))})
        return pages
