import pytest
from leadworkbench.sources.social_discovery import validate_endpoint, parse_search, parse_public_profile
from leadworkbench.db import Store

@pytest.mark.parametrize('value',['http://127.0.0.1:18888','http://localhost:8888/'])
def test_local_search_endpoint(value):
    assert validate_endpoint(value).endswith('/search')

@pytest.mark.parametrize('value',['http://169.254.169.254','http://192.168.1.2','file:///tmp/a','http://user:pass@localhost:8888','http://localhost:8888/admin','http://localhost:8888/?x=y'])
def test_endpoint_does_not_probe_arbitrary_services(value):
    with pytest.raises(ValueError): validate_endpoint(value)


def test_search_partial_failures_are_not_empty_success():
    assert parse_search({'results':[], 'unresponsive_engines':[['google','timeout']]})['status']=='blocked'
    assert parse_search({'results':[]})['status']=='no_results'
    assert parse_search({'results':[{'url':'https://example.org','title':'other'}]})['status']=='succeeded'
    with pytest.raises(ValueError): parse_search({'html':'login'})


def test_preview_is_not_a_person_or_full_post():
    html='<meta property="og:title" content="Brand (@brand)"><meta property="og:description" content="Contact info@brand.example for business"><p>commenter@other.example</p>'
    r=parse_public_profile(html,'https://www.instagram.com/brand/')
    assert r['status']=='preview_read'
    assert r['fields']==[{'key':'email','value':'info@brand.example','evidence':'Contact info@brand.example for business'}]
    assert not r.get('contacts')


def test_login_and_generic_shell_never_count_readable():
    assert parse_public_profile('<title>Login • Instagram</title><p>Log in to continue</p>','https://www.instagram.com/brand/')['status']=='auth_required'
    assert parse_public_profile('<title>Instagram</title><div id="root"></div>','https://www.instagram.com/brand/')['status']=='unreadable'


def test_jobs_retry_cancel_and_refusal(tmp_path):
    s=Store(tmp_path); cid=s.manual_company('Brand','https://brand.example')['id']
    run=s.create_social_run([cid])
    assert run['status']=='queued'
    assert s.create_social_run([cid])['id']==run['id']
    item=s.claim_social_item(); assert item['stage']=='website'
    s.cancel_social_run(run['id'])
    assert not s.finish_social_item(item['id'], {'status':'succeeded','links':[{'url':'https://instagram.com/should_not_save/'}]})
    assert not s.social_detail(cid)['accounts']
    new=s.retry_social_run(run['id']); assert new['id']!=run['id']
    with s.connect() as c:c.execute("UPDATE companies SET status='do_not_contact' WHERE id=?",(cid,))
    assert s.claim_social_item() is None

@pytest.mark.asyncio
async def test_worker_imports_candidates_preserves_exclusion_and_stores_previews(tmp_path,monkeypatch):
    from leadworkbench.social_worker import process_social_item
    from leadworkbench import social_policy
    clock=[1000.]
    monkeypatch.setattr(social_policy.time,'time',lambda:clock[0])
    s=Store(tmp_path); cid=s.manual_company('Brand','https://brand.example')['id']
    excluded=s.add_social(cid,'https://instagram.com/not_ours/','wrong match')
    s.decide_social(cid,excluded['id'],'excluded','third_party')
    s.set_social_settings('http://127.0.0.1:18888')
    run=s.create_social_run([cid]);calls=[];query_times=[]
    async def read(url,stage):
        calls.append((url,stage))
        if stage=='website':return {'status':'succeeded','links':[{'url':'https://instagram.com/brand/','source_url':url,'evidence':'follow us'}]}
        return {'status':'preview_read','title':'Brand','description':'contact info@brand.example','fields':[],'source_url':url}
    async def search(endpoint,query):
        query_times.append(clock[0])
        return {'status':'succeeded','results':[{'url':'https://instagram.com/not_ours/','title':'Not ours','description':'wrong'},
            {'url':'https://facebook.com/brand/','title':'Brand','description':'search snippet'}]}
    for _ in range(50):
        if await process_social_item(s,read=read,search=search):continue
        if s.social_run(run['id'])['status']=='completed':break
        # A temporarily empty claim now means a source may be cooling down.
        with s.connect() as con:
            deadlines=[row[0] for row in con.execute(
                "SELECT not_before FROM social_platform_gates WHERE not_before>?",(clock[0],))]
        assert deadlines
        clock[0]=min(deadlines)
    else:pytest.fail('Social fixture did not drain within its bounded number of turns')
    assert len(query_times)>1
    assert all(b-a>=15 for a,b in zip(query_times,query_times[1:]))
    r=s.social_run(run['id']); assert r['status']=='completed' and r['summary']['new_links']==2
    assert all('not_ours' not in u for u,stage in calls)
    d=s.social_detail(cid);assert len(d['pages'])==2 and len(d['excluded'])==1
    assert any('搜索摘要' in e['evidence'] for a in d['accounts'] for e in a['evidence'])
    assert not s.detail(cid)['contacts']

@pytest.mark.asyncio
async def test_source_failure_preserves_coverage_and_other_stages(tmp_path):
    from leadworkbench.social_worker import process_social_item
    s=Store(tmp_path); ids=[s.manual_company(n,'https://brand.example')['id'] for n in ['A','B']]
    for cid in ids:s.add_social(cid,'https://instagram.com/brand/','same brand')
    run=s.create_social_run(ids); searches=[];reads=[]
    async def search(endpoint,q):searches.append(q);return {'status':'blocked','reason':'provider_unavailable','message':'timeout'}
    async def read(url,stage):
        reads.append((url,stage))
        return {'status':'failed','reason':'network_error','message':'timeout'} if stage=='read' else {'status':'succeeded','links':[]}
    while await process_social_item(s,search=search,read=read):pass
    assert len(searches)==1
    assert len([r for r in reads if r[1]=='read'])==1
    r=s.social_run(run['id']); assert r['status']=='partial'
    assert r['summary']['no_results']==0 and r['summary']['blocked']>0


def test_pilot_excludes_refused_and_records_strata(tmp_path):
    s=Store(tmp_path)
    for n in range(25):
        cid=s.manual_company(str(n),'https://example.org')['id']
        if n<10:
            s.add_social(cid,'https://instagram.com/brand/','known')
            with s.connect() as c:c.execute("UPDATE companies SET social_links='[\"https://instagram.com/brand/\"]' WHERE id=?",(cid,))
    run=s.create_social_run(pilot=True)
    assert len(run['sample'])==20
    assert len([r for r in run['sample'] if r['stratum']=='existing_links'])==8
    assert len([r for r in run['sample'] if r['stratum']=='website_only'])==8
    assert len([r for r in run['sample'] if r['stratum']=='replacement'])==4

@pytest.mark.asyncio
async def test_real_http_search_contract_and_redirect_rejection():
    from aiohttp import web
    from leadworkbench.sources.social_discovery import search_searx
    async def handler(request):
        if request.query['q']=='redirect':raise web.HTTPFound('http://127.0.0.1:1/private')
        assert request.query['format']=='json'
        return web.json_response({'results':[{'url':'https://instagram.com/brand/','title':'Brand','content':'public snippet'}]})
    app=web.Application();app.router.add_get('/search',handler)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    try:
        result=await search_searx(f'http://127.0.0.1:{port}','query')
        assert result['status']=='succeeded' and result['results'][0]['description']=='public snippet'
        assert (await search_searx(f'http://127.0.0.1:{port}','redirect'))['status']=='blocked'
    finally:await runner.cleanup()


def test_failure_retains_last_good_preview_and_source_time(tmp_path):
    import json
    s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id'];r=s.add_social(cid,'https://instagram.com/brand/','candidate')
    run=s.create_social_run([cid])
    with s.connect() as con:
        con.execute('INSERT INTO social_page_observations VALUES(?,?,?,?,?)',('old',r['id'],run['id'],json.dumps({'status':'preview_read','title':'Brand','description':'old evidence','fields':[],'source_url':r['url']}),'2026-09-20T00:00:00+00:00'))
        con.execute('INSERT INTO social_page_observations VALUES(?,?,?,?,?)',('new',r['id'],run['id'],json.dumps({'status':'failed','message':'timeout'}),'2026-09-22T00:00:00+00:00'))
    page=s.social_pages(cid)[0]
    assert page['status']=='failed' and page['description']=='old evidence'
    assert page['content_observed_at']=='2026-09-20T00:00:00+00:00'
    assert page['observed_at']=='2026-09-22T00:00:00+00:00'


def test_new_routes_validate_config_auth_and_cancel(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    with TestClient(create_app(tmp_path,allowed_hosts={'testserver'})) as c:
        assert c.post('/api/social/runs',json={'pilot':True}).status_code==401
        c.headers['X-Local-Token']=c.get('/api/session').json()['token']
        assert c.put('/api/social/settings',json={'searxng_url':'http://169.254.169.254'}).status_code==400
        cid=c.post('/api/companies',json={'name':'Brand','website':'https://brand.example'}).json()['id']
        r=c.post('/api/social/runs',json={'company_ids':[cid]});assert r.status_code==200
        rid=r.json()['id']
        assert c.get('/api/social/runs?company_id='+cid).json()[0]['id']==rid
        assert c.post('/api/social/runs/'+rid+'/cancel').status_code==200
        assert c.get('/api/social/runs/'+rid).json()['status']=='cancelled'
        assert c.post('/api/social/runs/'+rid+'/retry').json()['id']!=rid


def test_worker_recovery_keeps_completed_work(tmp_path):
    s=Store(tmp_path);cid=s.manual_company('Brand','https://brand.example')['id'];r=s.create_social_run([cid]);item=s.claim_social_item()
    s.recover_social();assert s.social_run(r['id'])['summary']['running']==0
    resumed=s.claim_social_item();assert resumed['id']==item['id']
    s.finish_social_item(resumed['id'],{'status':'succeeded','links':[]});s.recover_social()
    assert s.social_run(r['id'])['summary']['succeeded']==1

@pytest.mark.asyncio
async def test_missing_website_is_skipped_not_no_results(tmp_path):
    from leadworkbench.social_worker import process_social_item
    s=Store(tmp_path);cid=s.manual_company('No Site','https://brand.example')['id']
    with s.connect() as con:con.execute("UPDATE companies SET website='' WHERE id=?",(cid,))
    run=s.create_social_run([cid]);await process_social_item(s)
    summary=s.social_run(run['id'])['summary']
    assert summary['skipped']==1 and summary['no_results']==0
