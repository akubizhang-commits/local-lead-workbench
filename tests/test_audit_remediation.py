"""Cross-flow audit regressions, using only temporary databases."""
import json
import pytest
from leadworkbench.db import Store, Conflict
from leadworkbench.api import create_app
from fastapi.testclient import TestClient

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)

def person(**changes):
    return dict(name='Alex Chen',role='Buyer',email='alex@example.com',kind='procurement',
                role_status='explicit',association_status='same_block',source_url='https://example.com/team',
                evidence='Alex Chen Buyer alex@example.com',**changes)

def apply(store, cid, p):
    store.apply_enrichment(cid, {'status':'completed','contacts':[p]})

def test_exclusion_survives_new_evidence(store):
    c=store.manual_company('Example','https://example.com')
    p=person(); apply(store,c['id'],p)
    p.update(role='Purchasing Manager'); apply(store,c['id'],p)
    store.review_decision(store.reviews()[0]['id'],'reject')
    p.update(role='Procurement Director'); apply(store,c['id'],p)
    contact=store.detail(c['id'])['contacts'][0]
    assert contact['adoption_status']=='excluded'
    assert store.company(c['id'])['contact_count']==0
    assert store.company_page(contact='procurement')['total']==0

def test_email_name_change_is_version_not_second_person(store):
    c=store.manual_company('Example','https://example.com')
    p=person(); apply(store,c['id'],p)
    p.update(name='Bob Chen',evidence='Bob Chen Buyer alex@example.com'); apply(store,c['id'],p)
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM contacts').fetchone()[0]==1
    r=store.reviews()[0]
    assert r['previous_contact']['name']=='Alex Chen'
    assert r['contact']['name']=='Bob Chen'
    assert store.company_page(contact='procurement')['total']==0
    store.review_decision(r['id'],'accept')
    assert store.company_page(contact='procurement')['total']==1

def test_status_and_business_types_auditable(store):
    c=store.manual_company('Example','https://example.com')
    c=store.update_company(c['id'],c['revision'],{},status='do_not_contact',business_types=['wholesale'])
    c=store.update_company(c['id'],c['revision'],{},status='candidate')
    assert c['manual_checked_at']
    changes=store.changes(c['id'])
    assert any(r['field']=='status' and r['old_value']=='do_not_contact' and r['new_value']=='candidate' for r in changes)
    assert any(r['field']=='business_types' for r in changes)

def test_due_schedule_waits_for_paused_run(store):
    c=store.manual_company('Example','https://example.com')
    r=store.refresh(c['id']);store.control(r['id'],'pause')
    s=store.create_schedule(company_id=c['id'],kind='refresh')
    with store.connect() as con:
        con.execute("UPDATE schedules SET next_due_at='2026-01-01T00:00:00+00:00'")
    store.dispatch_due()
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM occurrences').fetchone()[0]==0
        assert con.execute('SELECT next_due_at FROM schedules').fetchone()[0]=='2026-01-01T00:00:00+00:00'
    store.control(r['id'],'cancel');store.dispatch_due()
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM occurrences').fetchone()[0]==1
        assert con.execute('SELECT run_id FROM occurrences').fetchone()[0]!=r['id']

def test_batch_revision_conflict_is_atomic(tmp_path):
    app=create_app(tmp_path,allowed_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Local-Token']=client.get('/api/session').json()['token']
        store=app.state.store
        a=store.manual_company('A','https://example.com'); b=store.manual_company('B','https://example.com')
        body={'companies':[{'id':a['id'],'revision':a['revision']},{'id':b['id'],'revision':999}], 'status':'verified'}
        assert client.post('/api/companies/batch-status',json=body).status_code==409
        assert store.company(a['id'])['status']=='candidate'
        body['companies'][1]['revision']=b['revision']
        assert client.post('/api/companies/batch-status',json=body).json()=={'updated':2}
        assert store.company(a['id'])['status']=='verified'
        overview=client.get('/api/overview').json()
        assert overview['worker']['alive'] is False
        assert overview['queued_runs']==0

def test_accepted_source_value_is_protected_on_next_market_run(store):
    m=store.create_market(dict(name='Test',country='US',state='California',city='Los Angeles',keywords=['wholesale'],company_types=['wholesale'],auto_enrich=False))
    r=store.start_task(m['id'])
    record=dict(source_key='test:1',source_url='https://maps.example/1',name='Example',phone='11111111',address='1 Test Street',website='https://example.com')
    c=store.ingest_market(record,r['id'],'test',m['id'])
    store.update_company(c['id'],c['revision'],{'phone':'22222222'})
    record['phone']='33333333';store.ingest_market(record,r['id'],'test',m['id'])
    review=next(r for r in store.reviews() if r['field']=='phone')
    store.review_decision(review['id'],'accept')
    record['phone']='44444444';store.ingest_market(record,r['id'],'test',m['id'])
    assert store.company(c['id'])['phone']=='33333333'
    assert any(r['new_value']=='44444444' for r in store.reviews())

def test_explicit_accept_of_observed_field_without_prior_manual_edit(store):
    c=store.manual_company('Example','https://example.com')
    for value in ('11111111','22222222'):
        store.apply_enrichment(c['id'],dict(status='completed',observations=[dict(field='phone',value=value,source_url='https://example.com/contact',evidence=value)]))
    store.review_decision(store.reviews()[0]['id'],'accept')
    assert 'phone' in store.company(c['id'])['overrides']

@pytest.mark.asyncio
async def test_121_source_capability_limit_does_not_offer_useless_resume(store):
    from leadworkbench.worker import process_run
    m=store.create_market(dict(name='Dense test',country='US',state='California',city='Los Angeles',keywords=['wholesale'],company_types=['wholesale'],auto_enrich=False))
    run=store.start_task(m['id']);calls=[]
    async def planner(*args):
        return dict(status='completed',jobs=[dict(key='leaf',query='dense',level=3,bbox=[34,35,-119,-118])])
    async def source(*args,**kwargs):
        calls.append(kwargs['limit'])
        records=[dict(source_key=f'dense:{i}',source_url=f'https://maps.example/{i}',name=f'Test {i}',address=f'{i} Example Street',website=f'https://example{i}.com') for i in range(121)]
        return dict(status='completed',records=records[:kwargs['limit']],saturated=True,stop_reason='company_limit')
    await process_run(store,run['id'],planner=planner,discover=source)
    assert len(store.companies())==120
    assert store.run(run['id'])['coverage']['retryable'] is False
    activity = store.task_activity(m['id'])
    assert activity['rounds'][0]['coverage']['retryable'] is False
    assert activity['rounds'][0]['coverage']['capability_exhausted'] is True
    with pytest.raises(Conflict,match='能力边界'):
        Store(store.directory).control(run['id'],'resume')
    assert calls==[120]

def test_source_evidence_pair_remains_correct_across_pages(store):
    c=store.manual_company('Example','https://example.com')
    p=person();p['evidence']='Alex Chen Buyer Phone: 2134543667'
    apply(store,c['id'],p)
    second={**p,'source_url':'https://example.com/contact','evidence':'Alex Chen Buyer Phone: 2134549999'}
    apply(store,c['id'],second)
    contact=store.detail(c['id'])['contacts'][0]
    assert contact['source_url']==second['source_url']
    pairs={(s['source_url'],s['evidence']) for s in contact['sources']}
    assert pairs=={(p['source_url'],p['evidence']),(second['source_url'],second['evidence'])}
    phones={(f['value'],f['source_url']) for f in contact['fields'] if f['key']=='phone'}
    assert phones=={('2134543667',p['source_url']),('2134549999',second['source_url'])}

@pytest.mark.asyncio
async def test_pause_race_keeps_exhausted_work_partial(store):
    from leadworkbench.worker import process_run
    m=store.create_market(dict(name='Pause test',country='US',state='CA',city='LA',keywords=['wholesale'],company_types=['wholesale'],auto_enrich=False))
    run=store.start_task(m['id'])
    async def planner(*args):
        return dict(status='completed',jobs=[dict(key='leaf',query='dense',level=3)])
    async def source(*args,**kwargs):
        store.control(run['id'],'pause')
        return dict(status='completed',records=[],saturated=True,stop_reason='company_limit')
    await process_run(store,run['id'],planner=planner,discover=source)
    with store.connect() as con:
        row=con.execute("SELECT state,result FROM work_items WHERE stage='discovery'").fetchone()
        assert row['state']=='partial'
        assert json.loads(row['result'])['retryable'] is False
