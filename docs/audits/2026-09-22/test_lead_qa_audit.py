"""Isolated product QA. Run with repository .venv Python; NEVER opens real data.
Expected-behavior defect tests intentionally fail on the audited version.
All Store construction is create_app(tmp_path); HTTP is in-process, no browser/network.
"""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path('/Users/zhangnu/outputs/local-lead-workbench')
sys.path.insert(0, str(ROOT))
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from leadworkbench.api import create_app

@pytest.fixture
def qa(tmp_path):
    assert tmp_path.resolve() != (ROOT / 'data').resolve()
    app = create_app(tmp_path, allowed_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Local-Token'] = client.get('/api/session').json()['token']
        yield client, app.state.store

def company(qa, name='QA 企业', **extra):
    c, _ = qa
    response = c.post('/api/companies', json=dict(name=name, website='https://qa.example', **extra))
    assert response.status_code == 200, response.text
    return response.json()

def patch(qa, id, **changes):
    c, s = qa
    response = c.patch(f'/api/companies/{id}', json={'revision': s.company(id)['revision'], **changes})
    assert response.status_code == 200, response.text
    return response.json()

def contact(**extra):
    return dict(name='Alex', email='alex@qa.example', role='Sales', kind='other',
                role_status='explicit', association_status='same_block',
                source_url='https://qa.example/team', evidence='Alex Sales alex@qa.example', **extra)

def enrich(qa, id, contacts=None, observations=None, status='completed'):
    qa[1].apply_enrichment(id, dict(status=status, contacts=contacts or [], observations=observations or []))

def observation(field, value):
    return dict(field=field, value=value, source_url='https://qa.example/contact', evidence=f'{field}: {value}')

def reviews(qa):
    return qa[0].get('/api/reviews').json()

def decide(qa, review, decision):
    return qa[0].post(f"/api/reviews/{review['id']}/decision", json={'decision': decision})

def exported(qa, fmt, **filters):
    response = qa[0].get('/api/exports', params={'format': fmt, **filters})
    assert response.status_code == 200, response.text
    assert f'leads.{fmt}' in response.headers['content-disposition']
    if fmt == 'csv':
        return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    book = load_workbook(io.BytesIO(response.content))
    return {ws.title: [dict(zip(next(ws.values), row)) for row in list(ws.values)[1:]] for ws in book}

@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_api_filter_pagination_export_all_rows(qa, fmt):
    c, s = qa
    expected = []
    for n in range(63):
        item = company(qa, f'中文 QA {n:03}', city='Shanghai' if n % 2 == 0 else 'Boston')
        enrich(qa, item['id'], observations=[observation('business_types', ['wholesaler'])])
        patch(qa, item['id'], status='do_not_contact' if n == 62 else 'verified')
        if n % 2 == 0 and n != 62:
            expected.append(item['id'])
    filters = dict(q='Shanghai', status='verified', type='wholesale')
    ids = []
    for page in range(1, 6):
        response = c.get('/api/company-page', params={**filters, 'page': page, 'page_size': 7})
        assert response.status_code == 200
        result = response.json()
        assert result['total'] == 31
        ids += [row['id'] for row in result['items']]
    assert len(ids) == len(set(ids)) == 31
    assert set(ids) == set(expected)
    rows = exported(qa, fmt, **filters)
    if fmt == 'xlsx': rows = rows['Companies']
    assert {row['id'] for row in rows} == set(expected)
    assert c.get('/api/company-page', params={**filters, 'page': 100}).json()['items'] == []
    assert len(c.get('/api/companies', params=filters).json()) == 31

@pytest.mark.parametrize('params', [{'page': 0}, {'page': -1}, {'page_size': 0}, {'page_size': 201}, {'page': 'abc'}, {'segment': 'invalid'}])
def test_api_pagination_validation(qa, params):
    assert qa[0].get('/api/company-page', params=params).status_code == 422

@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_export_refusal_unicode_formula_text_and_relations(qa, fmt):
    item = company(qa, '=2+3 中文')
    enrich(qa, item['id'], contacts=[contact()])
    patch(qa, item['id'], fields={'phone': '+001234567890'}, notes='中文,备注\n第二行')
    blocked = company(qa, 'Blocked')
    enrich(qa, blocked['id'], contacts=[contact()])
    patch(qa, blocked['id'], status='do_not_contact')
    rows = exported(qa, fmt)
    if fmt == 'xlsx':
        assert set(rows) == {'Companies', 'Contacts', 'Locations', 'Sources'}
        assert {r['company_id'] for r in rows['Contacts']} == {item['id']}
        assert {r['company_id'] for r in rows['Sources']} == {item['id']}
        rows = rows['Companies']
    assert len(rows) == 1
    assert rows[0]['name'] == "'=2+3 中文"
    assert rows[0]['phone'] == "'+001234567890"
    assert rows[0]['notes'] == '中文,备注\n第二行'
    only_blocked = exported(qa, fmt, status='do_not_contact')
    assert (only_blocked if fmt == 'csv' else only_blocked['Companies']) == []

@pytest.mark.parametrize('field,value', [('phone', '00123456'), ('address', '上海市测试路 9 号'), ('business_types', ['importer'])])
def test_blank_fields_fill_without_review_then_conflict(qa, field, value):
    item = company(qa)
    enrich(qa, item['id'], observations=[observation(field, value)])
    assert qa[1].company(item['id'])[field] == value
    assert reviews(qa) == []
    changed = ['retailer'] if isinstance(value, list) else 'different value'
    enrich(qa, item['id'], observations=[observation(field, changed)])
    assert qa[1].company(item['id'])[field] == value
    assert len(reviews(qa)) == 1
    assert decide(qa, reviews(qa)[0], 'reject').status_code == 200
    assert qa[1].company(item['id'])[field] == value

@pytest.mark.parametrize('status', ['failed', 'blocked', 'completed', 'partial'])
def test_missing_or_failed_observations_do_not_clear_fields(qa, status):
    item = company(qa)
    patch(qa, item['id'], fields={'phone': '12345678'}, notes='保留备注', status='verified')
    enrich(qa, item['id'], observations=[observation('phone', '')], status=status)
    row = qa[1].company(item['id'])
    assert (row['phone'], row['notes'], row['status']) == ('12345678', '保留备注', 'verified')
    assert not reviews(qa)

def test_manually_cleared_phone_can_auto_fill(qa):
    item = company(qa)
    patch(qa, item['id'], fields={'phone': '12345678'})
    patch(qa, item['id'], fields={'phone': ''})
    enrich(qa, item['id'], observations=[observation('phone', '87654321')])
    assert qa[1].company(item['id'])['phone'] == '87654321'
    assert not reviews(qa)

@pytest.mark.parametrize('decision', ['accept', 'reject'])
def test_contact_role_identity_decision_contract(qa, decision):
    item = company(qa)
    before = {**contact(), 'role': ''}
    enrich(qa, item['id'], contacts=[before])
    enrich(qa, item['id'], contacts=[contact()])
    assert reviews(qa) == []
    changed = {**contact(), 'role': 'Buyer', 'kind': 'procurement', 'evidence': 'Alex Buyer alex@qa.example'}
    enrich(qa, item['id'], contacts=[changed])
    review = reviews(qa)[0]
    assert review['previous_contact']['role'] == 'Sales'
    assert review['contact']['role'] == 'Buyer'
    assert review['contact']['review_status'] == 'pending'
    assert decide(qa, review, decision).status_code == 200
    assert reviews(qa) == []
    row = qa[0].get(f"/api/companies/{item['id']}").json()['contacts'][0]
    assert row['review_status'] == decision
    assert decide(qa, review, decision).status_code == 409
    assert len(exported(qa, 'xlsx')['Contacts']) == (1 if decision == 'accept' else 0)

@pytest.mark.parametrize('status', ['candidate', 'verified', 'not_fit', 'do_not_contact'])
def test_manual_status_semantics_and_revision(qa, status):
    item = company(qa)
    updated = patch(qa, item['id'], status=status, notes='人工业务判断')
    assert updated['status'] == status
    assert updated['revision'] == item['revision'] + 1
    assert updated['last_checked_at'] is None  # manual verification is not a crawl timestamp
    assert updated['operating_status'] == 'unknown'
    assert qa[0].get('/api/company-page', params={'status': status}).json()['total'] == 1
    assert len(exported(qa, 'csv')) == (0 if status == 'do_not_contact' else 1)
    stale = qa[0].patch(f"/api/companies/{item['id']}", json={'revision': item['revision'], 'status': 'candidate'})
    assert stale.status_code == 409
    enrich(qa, item['id'], observations=[observation('phone', '12345678')])
    current = qa[1].company(item['id'])
    assert (current['status'], current['notes']) == (status, '人工业务判断')
    if status == 'do_not_contact':
        assert qa[0].post(f"/api/companies/{item['id']}/refresh").status_code == 409

@pytest.fixture
def market(qa):
    c, s = qa
    m = c.post('/api/tasks', json=dict(name='QA 市场', country='US', state='California', city='Los Angeles', company_types=['wholesale'], auto_enrich=False)).json()
    run = c.post(f"/api/tasks/{m['id']}/runs").json()
    record = dict(source_key='qa:market:1', source_url='https://maps.example/qa1', name='Market 企业',
                  address='10 QA Street, Los Angeles, CA 90001', phone='12125550000', website='https://market-qa.example', business_types=['wholesaler'])
    item = s.ingest_market(record, run['id'], 'QA synthetic', m['id'])
    return m, run, item

@pytest.mark.parametrize('segment,total', [('new', 1), ('existing', 0), ('changed', 0), ('not_seen', 0)])
def test_market_page_export_segments(qa, market, segment, total):
    m, run, item = market
    filters = dict(market_id=m['id'], run_id=run['id'], segment=segment)
    response = qa[0].get('/api/company-page', params=filters)
    assert response.status_code == 200 and response.json()['total'] == total
    for fmt in ['csv', 'xlsx']:
        rows = exported(qa, fmt, **filters)
        rows = rows if fmt == 'csv' else rows['Companies']
        assert len(rows) == total
        if rows: assert rows[0]['market_segment'] == 'new'

@pytest.mark.parametrize('route', ['/api/company-page', '/api/exports'])
@pytest.mark.parametrize('filters,expected', [({'market_id':'missing'},404), ({'run_id':'missing'},404), ({'segment':'new'},400)])
def test_invalid_market_filters(qa, route, filters, expected):
    assert qa[0].get(route, params=filters).status_code == expected

# Defects: plain expected-behavior assertions, NOT xfail; select with -k defect.
def test_defect_1_name_reassignment_requires_identity_review(qa):
    item = company(qa)
    enrich(qa, item['id'], contacts=[contact()])
    enrich(qa, item['id'], contacts=[{**contact(), 'name': 'Bob', 'evidence': 'Bob Sales alex@qa.example'}])
    rows = qa[0].get(f"/api/companies/{item['id']}").json()['contacts']
    print('identity reassignment:', [(r['name'], r['email'], r['review_status']) for r in rows], 'reviews=', reviews(qa))
    assert len(reviews(qa)) == 1, 'Same email/source changed from Alex to Bob but no identity review exists'


def test_defect_2_rejected_identity_not_counted_as_procurement(qa):
    item = company(qa)
    enrich(qa, item['id'], contacts=[contact()])
    enrich(qa, item['id'], contacts=[{**contact(), 'role': 'Buyer', 'kind': 'procurement', 'evidence': 'Alex Buyer'}])
    assert decide(qa, reviews(qa)[0], 'reject').status_code == 200
    rows = qa[0].get(f"/api/companies/{item['id']}").json()['contacts']
    js = "import {groupContacts} from " + json.dumps((ROOT/'frontend/src/groupContacts.ts').as_uri()) + ";console.log(JSON.stringify(groupContacts(" + json.dumps(rows) + ")));"
    result = subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e', js], capture_output=True, text=True, check=True)
    grouped = json.loads(result.stdout)
    print('rejected contact:', rows[0]['role'], rows[0]['review_status'], 'procurement=', len(grouped['procurement']), 'xlsx contacts=', len(exported(qa, 'xlsx')['Contacts']))
    assert grouped['procurement'] == [], 'Rejected identity remains counted/displayed as a procurement contact'


def test_defect_3_manual_status_history(qa):
    item = company(qa)
    for status in ['verified', 'do_not_contact', 'candidate']:
        patch(qa, item['id'], status=status)
    changes = qa[0].get('/api/changes').json()
    detail = qa[0].get(f"/api/companies/{item['id']}").json()
    print('final status=', detail['company']['status'], 'global changes=', changes, 'detail changes=', detail['changes'])
    assert [r['new_value'] for r in changes if r['field'] == 'status'], 'Manual status changes including removal of refusal have no audit history'


def test_defect_4_region_search_ignores_address(qa):
    item = company(qa, city='Los Angeles')
    patch(qa, item['id'], fields={'address': '10 QA Street, California 90001'})
    response = qa[0].get('/api/company-page', params={'q': 'California'})
    print('address=California; page=', response.json(), 'csv=', exported(qa, 'csv', q='California'))
    assert response.json()['total'] == 1, 'UI promises region search but state/address is ignored'

@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
def test_defect_5_run_only_export_loses_market_metadata(qa, market, fmt):
    m, run, item = market
    filters = dict(run_id=run['id'], segment='new')
    page = qa[0].get('/api/company-page', params=filters).json()
    assert page['items'][0]['market_segment'] == 'new'
    rows = exported(qa, fmt, **filters)
    rows = rows if fmt == 'csv' else rows['Companies']
    explicit = exported(qa, fmt, **filters, market_id=m['id'])
    explicit = explicit if fmt == 'csv' else explicit['Companies']
    print('run-only headers=', list(rows[0]), 'explicit headers=', list(explicit[0]))
    assert set(rows[0]) == set(explicit[0]), 'Equivalent run filter loses market_segment/market_changed/first_seen/last_seen columns'

@pytest.mark.parametrize('segment', ['new', 'existing', 'changed', 'not_seen'])
def test_populated_market_segments_and_historical_round(qa, market, segment):
    c, s = qa
    m, first, existing = market
    missing = s.ingest_market(dict(source_key='qa:missing', source_url='https://maps.example/missing', name='Not seen',
        address='20 QA Ave, Los Angeles, CA 90001', website='https://missing.example'), first['id'], 'fixture', m['id'])
    # Seed a completed historical round in the temporary DB; no queue processing tested.
    with s.connect() as con:
        con.execute("UPDATE runs SET status='completed',created_at='2026-01-01T00:00:00+00:00' WHERE id=?", (first['id'],))
    second = c.post(f"/api/tasks/{m['id']}/runs").json()
    s.ingest_market(dict(source_key='qa:market:1', source_url='https://maps.example/qa1', name='Market 企业',
        address=existing['address'], phone=existing['phone'], website=existing['website']), second['id'], 'fixture', m['id'])
    new = s.ingest_market(dict(source_key='qa:new', source_url='https://maps.example/new', name='New company',
        address='30 QA Ave, Los Angeles, CA 90001', website='https://new.example'), second['id'], 'fixture', m['id'])
    with s.connect() as con:
        con.execute('UPDATE run_companies SET changed=1 WHERE company_id=? AND run_id=?', (existing['id'], second['id']))
    expected = {'new':new['id'], 'existing':existing['id'], 'changed':existing['id'], 'not_seen':missing['id']}
    filters = dict(market_id=m['id'], run_id=second['id'], segment=segment)
    page = c.get('/api/company-page', params=filters).json()
    assert page['total'] == 1 and page['items'][0]['id'] == expected[segment]
    for fmt in ['csv', 'xlsx']:
        rows = exported(qa, fmt, **filters)
        rows = rows if fmt == 'csv' else rows['Companies']
        assert [r['id'] for r in rows] == [expected[segment]]
    history = c.get('/api/company-page', params={'market_id':m['id'], 'run_id':first['id']}).json()
    assert {r['id'] for r in history['items']} == {existing['id'], missing['id']}
    assert c.get('/api/company-page', params={'market_id':m['id']}).json()['total'] == 3


def test_xlsx_locations_link_to_filtered_company(qa, market):
    m, run, item = market
    book = exported(qa, 'xlsx', market_id=m['id'])
    assert len(book['Locations']) == 1
    assert book['Locations'][0]['company_id'] == item['id']
    assert book['Locations'][0]['address'] == item['address']
    assert {r['company_id'] for r in book['Sources']} == {item['id']}


def test_manual_field_accept_and_stale_review_guard(qa):
    item = company(qa)
    patch(qa, item['id'], fields={'phone':'11111111'})
    enrich(qa, item['id'], observations=[observation('phone','22222222')])
    assert decide(qa, reviews(qa)[0], 'accept').status_code == 200
    assert qa[1].company(item['id'])['phone'] == '22222222'
    enrich(qa, item['id'], observations=[observation('phone','33333333')])
    review = reviews(qa)[0]
    patch(qa, item['id'], fields={'phone':'44444444'})
    assert decide(qa, review, 'accept').status_code == 409
    assert qa[1].company(item['id'])['phone'] == '44444444'


def test_partial_contact_empty_fields_fill_and_failed_contact_ignored(qa):
    item = company(qa)
    enrich(qa, item['id'], contacts=[{**contact(), 'role':'', 'kind':'', 'role_status':'', 'association_status':''}], status='partial')
    enrich(qa, item['id'], contacts=[contact()], status='partial')
    assert not reviews(qa)
    assert qa[1].detail(item['id'])['contacts'][0]['role'] == 'Sales'
    assert qa[1].company(item['id'])['last_checked_at'] is None
    enrich(qa, item['id'], contacts=[{**contact(), 'role':'Buyer'}], status='failed')
    assert qa[1].detail(item['id'])['contacts'][0]['role'] == 'Sales'
    assert not reviews(qa)


def test_rejected_contact_unchanged_refresh_stays_rejected(qa):
    item = company(qa)
    enrich(qa, item['id'], contacts=[contact()])
    buyer = {**contact(), 'role':'Buyer', 'kind':'procurement', 'evidence':'Alex Buyer'}
    enrich(qa, item['id'], contacts=[buyer])
    assert decide(qa, reviews(qa)[0], 'reject').status_code == 200
    enrich(qa, item['id'], contacts=[buyer])
    assert qa[1].detail(item['id'])['contacts'][0]['review_status'] == 'reject'
    assert not reviews(qa)
    assert not exported(qa, 'xlsx')['Contacts']

@pytest.mark.parametrize('field,value', [('role','Buyer'), ('kind','procurement'), ('role_status','historical'), ('association_status','company_only')])
@pytest.mark.parametrize('decision', ['accept', 'reject'])
def test_each_contact_identity_dimension_requires_decision(qa, field, value, decision):
    item = company(qa)
    old = contact()
    enrich(qa, item['id'], contacts=[old])
    enrich(qa, item['id'], contacts=[{**old, field:value, 'evidence':f'Changed {field} to {value}'}])
    assert len(reviews(qa)) == 1
    review = reviews(qa)[0]
    assert review['previous_contact'][field] == old[field]
    assert review['contact'][field] == value
    assert decide(qa, review, decision).status_code == 200
    assert qa[1].detail(item['id'])['contacts'][0]['review_status'] == decision
    assert not reviews(qa)

@pytest.mark.parametrize('selected,stored', [('wholesale','wholesaler'), ('retail','retailer'), ('import','importer')])
@pytest.mark.parametrize('fmt', ['csv','xlsx'])
def test_type_aliases_through_api_and_export(qa, selected, stored, fmt):
    item = company(qa)
    enrich(qa, item['id'], observations=[observation('business_types',[stored])])
    company(qa, name='No type')
    response = qa[0].get('/api/company-page', params={'type':selected}).json()
    assert response['total'] == 1 and response['items'][0]['id'] == item['id']
    rows = exported(qa, fmt, type=selected)
    rows = rows if fmt == 'csv' else rows['Companies']
    assert [r['id'] for r in rows] == [item['id']]

def test_market_and_run_mismatch_rejected_for_page_and_exports(qa, market):
    m, run, _ = market
    other = qa[0].post('/api/tasks', json=dict(name='Other market', country='US', state='Texas', city='Dallas', company_types=['wholesale'])).json()
    params = {'market_id':other['id'], 'run_id':run['id']}
    for route in ['/api/company-page', '/api/exports']:
        assert qa[0].get(route, params=params).status_code == 400

if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q', '-s', '-p', 'no:cacheprovider', *sys.argv[1:]]))
