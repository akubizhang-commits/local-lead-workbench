from leadworkbench.db import Store
from leadworkbench.sources.maps import _record


def test_branches_do_not_need_review_and_same_place_move_updates_history(tmp_path):
    s = Store(tmp_path)
    market=s.create_market(dict(name='Test',country='US',state='NY',city='New York',company_types=['retail'],keywords=[],auto_enrich=False))
    run=s.start_task(market['id'])
    def add(key,address,phone,url=None,**extra):
        return s.ingest_market(dict(source_key=f'google_maps:cid:{key}',source_url=url or f'https://www.google.com/maps?cid={key}',name='Fixture Brand',website='https://brand.example',address=address,phone=phone,**extra),run['id'],'query',market['id'])
    a=add(1,'1 First St New York','12125551111')
    b=add(2,'2 Second St New York','12125552222')
    assert a['id'] != b['id']
    assert not s.reviews()
    moved=add(1,'3 Third St New York','12125553333',url='https://www.google.com/maps/place/new-name?cid=1')
    assert moved['id']==a['id']
    assert s.company(a['id'])['address']=='3 Third St New York'
    assert s.company(b['id'])['address']=='2 Second St New York'
    assert any(c['field']=='address' and c['old_value']=='1 First St New York' for c in s.changes(a['id']))
    with s.connect() as c:
        assert c.execute('select address from locations where company_id=?',(a['id'],)).fetchone()[0]=='3 Third St New York'
    add(1,'3 Third St New York','12125553333',operating_status='permanently_closed')
    assert s.company(a['id'])['operating_status']=='permanently_closed'
    add(1,'3 Third St New York','12125553333')
    assert s.company(a['id'])['operating_status']=='permanently_closed'


def test_only_explicit_closure_not_daily_closed_is_business_failure():
    row=dict(title='Fixture',link='https://www.google.com/maps?cid=1')
    for raw in ('CLOSED','Closed ⋅ Opens 9 am',''):
        assert _record({**row,'status':raw},'now')['operating_status']==''
    assert _record({**row,'status':'Permanently closed'},'now')['operating_status']=='permanently_closed'
    assert _record({**row,'status':'Temporarily closed'},'now')['operating_status']=='temporarily_closed'


def test_existing_branch_reviews_auto_resolve_without_covering_company(tmp_path):
    s=Store(tmp_path)
    a=s.manual_company('Fixture Brand','https://brand.example/stores/one')
    b=s.manual_company('Fixture Brand','https://brand.example/stores/two')
    s.update_company(a['id'],a['revision'],{'address':'1 First St','phone':'12125551111'},'',None)
    s.update_company(b['id'],b['revision'],{'address':'2 Second St','phone':'12125552222'},'',None)
    with s.connect() as c:
        s._review(c,b['id'],'duplicate_candidate','company',a['id'],b['id'],'https://maps.google.com','同名')
    assert s.resolve_branch_reviews()==1
    assert s.reviews()==[]
    assert len(s.companies())==2
    assert s.company(a['id'])['address']=='1 First St'
    assert s.detail(a['id'])['related_locations'][0]['id']==b['id']


def test_older_listing_does_not_restore_stale_address(tmp_path):
    s=Store(tmp_path)
    m=s.create_market(dict(name='Fixture',country='US',state='NY',city='NY',company_types=['retail'],keywords=[],auto_enrich=False))
    run=s.start_task(m['id'])
    record=dict(source_key='google_maps:cid:1',source_url='https://www.google.com/maps?cid=1',name='Fixture',address='New address',website='',phone='',observed_at='2026-09-22T09:00:00+00:00')
    a=s.ingest_market(record,run['id'],'query',m['id'])
    s.ingest_market({**record,'address':'Old address','observed_at':'2026-09-21T09:00:00+00:00'},run['id'],'query',m['id'])
    assert s.company(a['id'])['address']=='New address'
