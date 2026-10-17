import pytest
from fastapi.testclient import TestClient
from leadworkbench.api import create_app

def test_connection_settings_secret_and_validation(tmp_path):
 with TestClient(create_app(tmp_path,allowed_hosts={'testserver'})) as c:
  c.headers['X-Local-Token']=c.get('/api/session').json()['token']
  r=c.put('/api/social/connections',json={'reader':'browser','instagram_reader':'browser','proxy_url':'http://127.0.0.1:7890','max_posts':3,'youtube_api_key':'qa-secret'})
  assert r.status_code==200
  assert 'qa-secret' not in r.text
  assert r.json()['youtube_key_configured']
  assert (tmp_path/'social-secrets.json').stat().st_mode & 0o777==0o600
  assert c.put('/api/social/connections',json={'proxy_url':'http://evil.example:80'}).status_code==422
  assert c.post('/api/social/browser/evil/open').status_code==422

def test_restore_blocks_social_queue(tmp_path):
 with TestClient(create_app(tmp_path,allowed_hosts={'testserver'})) as c:
  c.headers['X-Local-Token']=c.get('/api/session').json()['token']
  a=c.post('/api/companies',json={'name':'QA','website':'https://qa.example'}).json()
  b=c.post('/api/backups').json()
  r=c.post('/api/social/runs',json={'company_ids':[a['id']]}).json()
  assert c.post('/api/backups/restore',json={'name':b['name']}).status_code==409
  c.post('/api/social/runs/'+r['id']+'/cancel')
  assert c.post('/api/backups/restore',json={'name':b['name']}).status_code==200

def test_browser_parser_rejects_login_and_retains_post_provenance():
 from leadworkbench.sources.social_browser import parse_browser_document
 assert parse_browser_document('<title>Log in • Instagram</title>','https://www.instagram.com/accounts/login/')['status']=='auth_required'
 r=parse_browser_document('<title>Example</title><meta property="og:description" content="Wholesale email hello@example.com"><article><time datetime="2026-01-01"></time><p>New collection</p><a href="/p/ABC/">Post</a></article>','https://www.instagram.com/example/')
 assert r['status']=='preview_read'
 assert r['posts'][0]['url']=='https://www.instagram.com/p/ABC/'
 assert r['fields'][0]['value']=='hello@example.com'

def test_known_account_run_skips_search_and_deduplicates_posts(tmp_path,monkeypatch):
 from leadworkbench.db import Store
 from leadworkbench import social_policy
 clock=[1000.0]
 monkeypatch.setattr(social_policy.time,'time',lambda:clock[0])
 s=Store(tmp_path);a=s.manual_company('QA','https://qa.example')
 s.add_social(a['id'],'https://www.instagram.com/qa_brand/','manual')
 run=s.create_social_run([a['id']],read_only=True)
 assert [i['stage'] for i in run['items']]==['read']
 item=s.claim_social_item()
 result={'status':'preview_read','provider':'browser','posts':[{'url':'https://www.instagram.com/p/ABC/','text':'New collection','published_at':''}]}
 s.finish_social_item(item['id'],result)
 assert len(s.social_detail(a['id'])['posts'])==1
 assert len(s.social_run(run['id'])['items'])==1
 run=s.create_social_run([a['id']],read_only=True)
 # A fresh manual run observes the same platform cooldown as market work.
 assert s.claim_social_item() is None
 assert s.social_run(run['id'])['summary']['pending']==1
 with s.connect() as con:
  deadline=social_policy.gate(con,'instagram')['not_before']
 clock[0]=deadline-0.001
 assert s.claim_social_item() is None
 clock[0]=deadline
 item=s.claim_social_item()
 assert item is not None and item['run_id']==run['id'] and item['stage']=='read'
 assert s.finish_social_item(item['id'],result)
 assert s.social_run(run['id'])['summary']['succeeded']==1
 assert len(s.social_detail(a['id'])['posts'])==1
 with s.connect() as con:
  assert con.execute('SELECT count(*) FROM social_page_observations').fetchone()[0]==2

@pytest.mark.asyncio
async def test_selected_reader_routes_without_exposing_secrets(tmp_path,monkeypatch):
 from leadworkbench.sources import social_browser,social_optional,social_combination
 calls=[]
 async def browser(url,directory,settings):calls.append('browser');return {'status':'auth_required'}
 async def insta(url,directory,settings):calls.append('instaloader');return {'status':'blocked'}
 async def youtube(url,directory,settings):calls.append('youtube');return {'status':'preview_read'}
 monkeypatch.setattr(social_browser,'read_browser',browser);monkeypatch.setattr(social_optional,'read_instaloader',insta);monkeypatch.setattr(social_optional,'read_youtube',youtube)
 await social_combination.read_combined('https://www.instagram.com/brand/',tmp_path,{'reader':'browser'})
 await social_combination.read_combined('https://www.instagram.com/brand/',tmp_path,{'instagram_reader':'instaloader'})
 (tmp_path/'social-secrets.json').write_text('{"youtube_api_key":"secret"}')
 await social_combination.read_combined('https://www.youtube.com/@brand',tmp_path,{})
 assert calls==['browser','instaloader','youtube']

def test_profile_lock_prevents_concurrent_login_and_read(tmp_path):
 from leadworkbench.sources.social_browser import profile_lock
 with profile_lock(tmp_path,'instagram') as p:
  assert p.stat().st_mode & 0o777==0o700
  with pytest.raises(RuntimeError):
   with profile_lock(tmp_path,'instagram'):pass
 with profile_lock(tmp_path,'instagram'):pass

def test_partial_profile_success_keeps_run_partial(tmp_path):
 from leadworkbench.db import Store
 s=Store(tmp_path);a=s.manual_company('QA','https://qa.example');s.add_social(a['id'],'https://www.instagram.com/qa_brand/','manual')
 r=s.create_social_run([a['id']],read_only=True);i=s.claim_social_item()
 s.finish_social_item(i['id'],{'status':'preview_read','partial':True,'title':'QA','description':'Profile','posts':[],'message':'Posts incomplete'})
 assert s.social_run(r['id'])['status']=='partial'
