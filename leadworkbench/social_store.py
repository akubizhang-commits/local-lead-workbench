"""Social evidence and explicit decisions, separate from company source values."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from .social import classify_link, search_queries
from .social_discovery_store import SocialDiscoveryStoreMixin
from .errors import Invalid, NotFound


def stamp():
    return datetime.now(timezone.utc).isoformat()


def ident(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]


class SocialStoreMixin(SocialDiscoveryStoreMixin):
    def _migrate_social(self, con):
        con.executescript('''
        CREATE TABLE IF NOT EXISTS social_entities(
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS company_social(
            company_id TEXT NOT NULL REFERENCES companies(id), entity_id TEXT NOT NULL REFERENCES social_entities(id),
            status TEXT NOT NULL DEFAULT 'candidate', scope TEXT NOT NULL DEFAULT 'unknown',
            revision INTEGER NOT NULL DEFAULT 1, decided_at TEXT, PRIMARY KEY(company_id,entity_id));
        CREATE TABLE IF NOT EXISTS social_evidence(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),entity_id TEXT REFERENCES social_entities(id),
            original_url TEXT NOT NULL,source_url TEXT NOT NULL,evidence TEXT NOT NULL,
            method TEXT NOT NULL,observed_at TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS social_evidence_company ON social_evidence(company_id,entity_id);
        CREATE TABLE IF NOT EXISTS social_decisions(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL,entity_id TEXT NOT NULL,
            old_status TEXT NOT NULL,new_status TEXT NOT NULL,scope TEXT NOT NULL,decided_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS social_outreach(
            id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),entity_id TEXT NOT NULL REFERENCES social_entities(id),
            state TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS social_migrations(name TEXT PRIMARY KEY);
        ''')
        self._migrate_social_discovery(con)
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM social_migrations WHERE name='legacy_links_v1'").fetchone():
            for c in con.execute('SELECT id FROM companies WHERE merged_into IS NULL').fetchall():
                self._sync_social(con,c['id'])
            con.execute("INSERT INTO social_migrations VALUES('legacy_links_v1')")

    def _save_social(self,con,cid,url,source_url,evidence,method,observed_at):
        r=classify_link(url)
        eid=ident(r['url']) if r['kind']!='excluded' else None
        if eid:
            con.execute('INSERT OR IGNORE INTO social_entities VALUES(?,?,?,?,?,?)',
                        (eid,r['platform'],r['kind'],r['url'],r['label'],stamp()))
            con.execute('INSERT OR IGNORE INTO company_social(company_id,entity_id) VALUES(?,?)',(cid,eid))
        fingerprint=ident(json.dumps([cid,url,source_url,evidence,method],ensure_ascii=False))
        con.execute('''INSERT INTO social_evidence VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id)
            DO UPDATE SET observed_at=MAX(observed_at,excluded.observed_at)''',
            (fingerprint,cid,eid,url,source_url,evidence,method,observed_at,r['reason']))
        return {**r,'id':eid}

    def _sync_social(self,con,cid):
        company=con.execute('SELECT * FROM companies WHERE id=?',(cid,)).fetchone()
        if not company: raise NotFound("企业不存在或已合并")
        observed=set()
        for o in con.execute("SELECT * FROM observations WHERE company_id=? AND field='social_links'",(cid,)).fetchall():
            try:
                values=json.loads(o['value'])
                if isinstance(values,str): values=[values]
                if not isinstance(values,list): continue
            except (ValueError,TypeError): continue
            for url in values:
                if not isinstance(url,str): continue
                observed.add(url)
                self._save_social(con,cid,url,o['source_url'],o['evidence'],o['method'],o['observed_at'])
        for url in json.loads(company['social_links']):
            if isinstance(url,str) and url not in observed:
                self._save_social(con,cid,url,'','历史链接；缺少字段级来源，需核对','legacy',company['created_at'])

    def sync_social(self,cid):
        with self.connect() as con: self._sync_social(con,cid)
        return self.social_detail(cid)

    def add_social(self,cid,url,evidence):
        if classify_link(url)['kind']=='excluded': raise Invalid('请输入支持的平台账号或帖子链接；登录、帮助和分享页面不能作为账号')
        with self.connect() as con:
            if not con.execute('SELECT 1 FROM companies WHERE id=? AND merged_into IS NULL',(cid,)).fetchone(): raise NotFound("企业不存在或已合并")
            return self._save_social(con,cid,url,'',evidence,'manual',stamp())

    def decide_social(self,cid,eid,status,scope,revision=None):
        if status not in {'confirmed','candidate','excluded'} or scope not in {'brand','location','employee','third_party','unknown'}:
            raise Invalid('无效的归属决定')
        if status=='confirmed' and scope=='unknown': raise Invalid('确认时请选择品牌、门店、员工或第三方范围')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM company_social WHERE company_id=? AND entity_id=?',(cid,eid)).fetchone()
            if not row: raise NotFound("社媒记录不存在")
            if revision is not None and revision!=row['revision']:
                from .db import Conflict
                raise Conflict('账号决定已变化，请刷新后重试')
            con.execute('UPDATE company_social SET status=?,scope=?,revision=revision+1,decided_at=? WHERE company_id=? AND entity_id=?',
                        (status,scope,stamp(),cid,eid))
            con.execute('INSERT INTO social_decisions VALUES(?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex,cid,eid,row['status'],status,scope,stamp()))
        return {'ok':True}

    def record_social_outreach(self,cid,eid,state,note):
        if state not in {'draft','contacted','replied','closed'}: raise Invalid('无效跟进状态')
        if not note.strip() or len(note)>5000: raise Invalid('请填写草稿或跟进内容（最多5000字）')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('''SELECT cs.status,c.status company_status FROM company_social cs JOIN companies c ON c.id=cs.company_id
                WHERE cs.company_id=? AND cs.entity_id=?''',(cid,eid)).fetchone()
            if not row: raise NotFound("社媒记录不存在")
            if row['company_status']=='do_not_contact': raise Invalid('此企业已标记拒绝联系，不能新增联系记录')
            if row['status']!='confirmed': raise Invalid('请先确认该账号或帖子的归属')
            con.execute('INSERT INTO social_outreach VALUES(?,?,?,?,?,?)',
                        (uuid.uuid4().hex,cid,eid,state,note.strip(),stamp()))
        return {'ok':True}

    def social_detail(self,cid):
        company=self.company(cid)
        with self.connect() as con:
            rows=[dict(r) for r in con.execute('''SELECT e.*,cs.status,cs.scope,cs.revision,cs.decided_at,
                (SELECT count(*) FROM company_social x JOIN companies c ON c.id=x.company_id
                    WHERE x.entity_id=e.id AND x.status!='excluded' AND c.merged_into IS NULL) linked_company_count
                FROM company_social cs JOIN social_entities e ON cs.entity_id=e.id
                WHERE cs.company_id=? ORDER BY e.platform,e.url''',(cid,))]
            evidence=[dict(r) for r in con.execute('SELECT * FROM social_evidence WHERE company_id=? ORDER BY observed_at DESC',(cid,))]
            for r in rows:
                r['evidence']=[e for e in evidence if e['entity_id']==r['id']]
                r['last_observed_at']=max((e['observed_at'] for e in r['evidence']),default=None)
            outreach=[dict(r) for r in con.execute('''SELECT o.*,c.name company_name,e.url,e.label,e.platform FROM social_outreach o
                JOIN companies c ON c.id=o.company_id JOIN social_entities e ON e.id=o.entity_id
                WHERE o.company_id=? OR o.entity_id IN (SELECT entity_id FROM company_social WHERE company_id=? AND status='confirmed')
                ORDER BY o.created_at DESC LIMIT 200''',(cid,cid))]
        return {'accounts':[r for r in rows if r['kind']=='account' and r['status']!='excluded'],
                'posts':[r for r in rows if r['kind'] in {'post','collection'} and r['status']!='excluded'],
                'excluded':[r for r in rows if r['status']=='excluded'],
                'ignored_links':[e for e in evidence if not e['entity_id']],
                'outreach':outreach,'search_queries':search_queries(company),'pages':self.social_pages(cid),
                'do_not_contact':company['status']=='do_not_contact',
                'capabilities':{'account_discovery':'website_search_and_manual','post_content':'public_preview_only','auto_send':False}}
