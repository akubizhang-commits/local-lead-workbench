import pytest
from leadworkbench.social import classify_link
from leadworkbench.db import Store

@pytest.mark.parametrize('url,platform,kind,canonical', [
 ('https://www.instagram.com/Brand/?utm_source=a','instagram','account','https://www.instagram.com/brand/'),
 ('https://www.instagram.com/p/ABC/?igsh=x','instagram','post','https://www.instagram.com/p/ABC/'),
 ('https://twitter.com/Brand/status/123?s=20','x','post','https://x.com/brand/status/123'),
 ('https://m.facebook.com/profile.php?id=123&ref=a','facebook','account','https://www.facebook.com/profile.php?id=123'),
 ('https://youtu.be/ABC?t=4','youtube','post','https://www.youtube.com/watch?v=ABC'),
 ('https://www.linkedin.com/company/test/?trk=x','linkedin','account','https://www.linkedin.com/company/test/'),
 ('https://www.tiktok.com/@Brand/video/123?_t=x','tiktok','post','https://www.tiktok.com/@brand/video/123'),
])
def test_normalization(url,platform,kind,canonical):
    r=classify_link(url)
    assert (r['platform'],r['kind'],r['url']) == (platform,kind,canonical)

@pytest.mark.parametrize('url', ['https://help.instagram.com/123','https://instagram.com/accounts/login/',
 'https://facebook.com/sharer/sharer.php?u=x','https://youtube.com/results?search_query=x',
 'https://instagram.com.evil.test/brand','javascript:alert(1)','https://evil@instagram.com/brand',
 'https://instagram.com:999/brand','https://instagram.com/explore/','https://linkedin.com/help'])
def test_noise_not_account(url):
    assert classify_link(url)['kind']=='excluded'

def seed(store, cid, links):
    import json
    with store.connect() as con:
        con.execute('UPDATE companies SET social_links=? WHERE id=?',(json.dumps(links),cid))
    return store.sync_social(cid)

def test_shared_accounts_decision_protection_and_post_separation(tmp_path):
    s=Store(tmp_path)
    a=s.manual_company('A','https://a.example')['id']; b=s.manual_company('B','https://b.example')['id']
    seed(s,a,['https://instagram.com/Brand/','https://instagram.com/p/123/','https://help.instagram.com/'])
    seed(s,b,['https://instagram.com/brand/?x=1'])
    view=s.social_detail(a)
    assert len(view['accounts'])==len(view['posts'])==len(view['ignored_links'])==1
    acc=view['accounts'][0]
    assert acc['status']=='candidate' and acc['linked_company_count']==2
    s.decide_social(a,acc['id'],'excluded','unknown')
    seed(s,a,['https://instagram.com/brand/'])
    assert s.social_detail(a)['excluded'][0]['status']=='excluded'
    assert s.social_detail(b)['accounts'][0]['status']=='candidate'
    assert not s.social_detail(a)['accounts']

def test_outreach_requires_confirmed_association_and_respects_refusal(tmp_path):
    s=Store(tmp_path); a=s.manual_company('A','https://a.example')['id']; b=s.manual_company('B','https://b.example')['id']
    r=s.add_social(a,'https://instagram.com/brand/', '用户发现')
    eid=r['id']
    with pytest.raises(ValueError): s.record_social_outreach(a,eid,'contacted','hello')
    s.decide_social(a,eid,'confirmed','brand')
    s.add_social(b,'https://instagram.com/brand/', '')
    s.decide_social(b,eid,'confirmed','brand')
    s.record_social_outreach(a,eid,'draft','hello')
    assert s.social_detail(b)['outreach'][0]['state']=='draft'
    s.record_social_outreach(a,eid,'contacted','用户确认已发送')
    with s.connect() as con: con.execute("UPDATE companies SET status='do_not_contact' WHERE id=?",(a,))
    with pytest.raises(ValueError): s.record_social_outreach(a,eid,'contacted','again')
    with pytest.raises(KeyError): s.decide_social(a,'missing','confirmed','brand')

def test_source_provenance_and_legacy_migration_are_idempotent(tmp_path):
    s=Store(tmp_path); a=s.manual_company('A','https://a.example')['id']
    with s.connect() as con:
        s._observe(con,a,'social_links',['https://instagram.com/brand/'],'https://a.example/contact',
                   'Follow us','2026-09-22T00:00:00+00:00','website')
    seed(s,a,['https://instagram.com/brand/'])
    s.sync_social(a)
    r=s.social_detail(a)['accounts'][0]
    assert len(r['evidence'])==1
    assert r['evidence'][0]['source_url']=='https://a.example/contact'
    s.decide_social(a,r['id'],'confirmed','brand')
    reopened=Store(tmp_path)
    assert reopened.social_detail(a)['accounts'][0]['status']=='confirmed'
    backup=s.backup()
    s.add_social(a,'https://facebook.com/newbrand','manual')
    s.restore(backup['name'])
    assert len(s.social_detail(a)['accounts'])==1


def test_api_social_workflow_and_invalid_inputs(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    app=create_app(tmp_path,allowed_hosts={'testserver'})
    with TestClient(app) as c:
        assert c.get('/api/companies/missing/social').status_code==401
        c.headers['X-Local-Token']=c.get('/api/session').json()['token']
        cid=c.post('/api/companies',json={'name':'Test','website':'https://brand.example'}).json()['id']
        base=f'/api/companies/{cid}/social'
        assert c.get(base).json()['accounts']==[]
        assert c.post(base+'/links',json={'url':'https://help.instagram.com/'}).status_code==400
        entry=c.post(base+'/links',json={'url':'https://instagram.com/brand/','evidence':'footer'}).json()
        data=c.get(base).json(); assert data['accounts'][0]['status']=='candidate'
        eid=entry['id']
        decision={'status':'confirmed','scope':'brand','revision':1}
        assert c.post(f'{base}/{eid}/decision',json=decision).status_code==200
        assert c.post(f'{base}/{eid}/decision',json=decision).status_code==409
        assert c.post(base+'/outreach',json={'entity_id':eid,'state':'opened','note':'x'}).status_code==422
        assert c.post(base+'/outreach',json={'entity_id':eid,'state':'draft','note':'Hello'}).status_code==200
        assert c.get(base).json()['outreach'][0]['state']=='draft'
        assert c.get('/api/companies/missing/social').status_code==404
        assert c.post(base+'/links',json={'url':'https://instagram.com/test/'},headers={'Origin':'https://evil.example'}).status_code==403


def test_website_ingestion_syncs_without_confirmation(tmp_path):
    s=Store(tmp_path); a=s.manual_company('A','https://a.example')['id']
    s.apply_enrichment(a, {'status':'completed','social_links':['https://instagram.com/brand/'], 'observations':[], 'contacts':[]})
    assert s.social_detail(a)['accounts'][0]['status']=='candidate'

def test_organization_sameas_requires_own_site_and_excludes_person():
    from leadworkbench.sources.website import parse_page
    html='''<script type="application/ld+json">{"@graph":[
        {"@type":"Organization","url":"https://example.org/","sameAs":["https://instagram.com/brand/","https://facebook.com/brand/"]},
        {"@type":"Organization","url":"https://other.example/","sameAs":"https://instagram.com/other/"},
        {"@type":"Person","url":"https://example.org/","sameAs":"https://instagram.com/employee/"}
    ]}</script>'''
    r=parse_page(html,'https://example.org/about')
    assert set(r['social_links'])=={'https://www.instagram.com/brand/','https://www.facebook.com/brand/'}
    assert all(o['source_url']=='https://example.org/about' and 'sameAs' in o['evidence'] for o in r['observations'])


def test_search_does_not_quote_company_and_city_as_one_phrase():
    from leadworkbench.social import search_queries
    queries=search_queries({'name':'Test Apparel','city':'New York','website':'https://example.org','phone':'+1 (212) 555-0100'})
    assert any('"Test Apparel" "New York" site:' in q['query'] for q in queries)
    assert any('"12125550100"' in q['query'] for q in queries)
