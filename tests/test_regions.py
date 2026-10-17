from fastapi.testclient import TestClient
import pytest
from leadworkbench.api import create_app
from leadworkbench.regions import regions, validate_selection, catalogue, index
from leadworkbench.db import Store


def test_us_catalogue_complete_and_counties_non_tree():
    data=catalogue()
    states=regions('US')
    assert len(states)==51
    assert len([s for s in states if s['subtype']=='state'])==50
    assert len({r['id'] for r in data['regions']})==len(data['regions'])
    assert data['metadata']['counts']['places']>30000
    assert data['metadata']['counts']['counties']>3000
    for r in data['regions']:
        if r['kind']=='place':
            assert r['county_ids']
            assert all(index()[c]['state_id']==r['state_id'] for c in r['county_ids'])
    ny=next(p for p in regions('US-NY',kind='place') if p['official_name']=='New York city')
    assert len(ny['county_ids'])==5
    assert len([p for p in regions('US-NY',kind='place') if p['id']==ny['id']])==1
    for county in ny['county_ids']:
        assert ny in regions('US-NY',kind='place',county_id=county)


def selection():
    p=next(p for p in regions('US-NY',kind='place') if p['official_name']=='New York city')
    c=index()[p['county_ids'][0]]
    return dict(name='NY market',country='US',state='New York',city=p['name'],state_id='US-NY',
                county_id=c['id'],county=c['name'],place_id=p['id'],company_types=['wholesale'],keywords=[])


def test_county_filter_does_not_duplicate_whole_city_market(tmp_path):
    store=Store(tmp_path);payload=selection();a=store.create_market(payload)
    p=index()[payload['place_id']];other=index()[p['county_ids'][1]]
    b=store.create_market({**payload,'county_id':other['id'],'county':other['name']})
    assert a['id']==b['id']
    assert a['geography']['place_id']==payload['place_id']
    assert len(a['geography']['county_ids'])==5
    assert a['geography']['scope']=='whole_place'
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM task_geography').fetchone()[0]==1


@pytest.mark.parametrize('patch',[{'country':'CA'},{'state':'California'},{'county_id':'US-COUNTY-06037'},
                                  {'place_id':'US-PLACE-INVALID'},{'city':'Los Angeles'}, {'place_id':None}])
def test_mismatched_ids_rejected(patch):
    with pytest.raises(ValueError):validate_selection({**selection(),**patch})


def test_region_api_filters_and_invalid_parent(tmp_path):
    with TestClient(create_app(tmp_path,allowed_hosts={'testserver'})) as client:
        client.headers['X-Local-Token']=client.get('/api/session').json()['token']
        assert len(client.get('/api/regions?parent_id=US').json())==51
        counties=client.get('/api/regions?parent_id=US-CA&kind=county').json()
        assert len(counties)==58
        response=client.get('/api/regions?parent_id=US-NY&kind=place&county_id=US-COUNTY-06037')
        assert response.status_code==400
        assert client.get('/api/regions?parent_id=unknown&kind=place').json()==[]
        task=client.post('/api/tasks',json=selection())
        assert task.status_code==200,task.text
        assert task.json()['geography']['verified_selection'] is True


def test_manual_other_country_supported():
    payload={'country':'CA','state':'Ontario','city':'Toronto'}
    assert validate_selection(payload)==payload


@pytest.mark.parametrize('catalogue_first',[False,True])
def test_known_city_manual_and_catalogue_creation_reuse_market(tmp_path,catalogue_first):
    store=Store(tmp_path);payload=selection()
    manual={k:v for k,v in payload.items() if k not in ('state_id','place_id','county_id','county')}
    a=store.create_market(payload if catalogue_first else manual)
    b=store.create_market(manual if catalogue_first else payload)
    assert a['id']==b['id']
    assert b['geography']['place_id']==payload['place_id']


def test_official_city_suffix_reuses_legacy_manual_market(tmp_path):
    store=Store(tmp_path);payload=selection()
    legacy={k:v for k,v in payload.items() if k not in ('state_id','place_id','county_id','county')}
    legacy['city']='New York City'
    old=store.create_market(legacy)
    new=store.create_market(payload)
    assert old['id']==new['id']


def test_market_geography_survives_backup_restore(tmp_path):
    store=Store(tmp_path);task=store.create_market(selection());backup=store.backup()
    with store.connect() as con:con.execute('DELETE FROM task_geography')
    store.restore(backup['name'])
    assert store.task(task['id'])['geography']==task['geography']


def test_independent_city_and_dc_are_not_forced_into_neighbor_counties():
    baltimore=next(p for p in regions('US-MD',kind='place') if p['official_name']=='Baltimore city')
    assert baltimore['county_ids']==['US-COUNTY-24510']
    assert baltimore not in regions('US-MD',kind='place',county_id='US-COUNTY-24005')
    washington=next(p for p in regions('US-DC',kind='place') if p['official_name']=='Washington city')
    assert washington['county_ids']==['US-COUNTY-11001']
    assert index()['US-DC']['subtype']=='federal_district'


@pytest.mark.asyncio
async def test_selected_reference_place_disambiguates_same_name_without_geocoder(tmp_path,monkeypatch):
    from leadworkbench import market_plan
    async def forbidden(*args,**kwargs):raise AssertionError('Catalogue places must not be geocoded by ambiguous name')
    monkeypatch.setattr(market_plan,'_geocode',forbidden)
    candidates=[p for p in regions('US-CA',kind='place') if p['official_name']=='Bayview CDP']
    assert len(candidates)>1
    plans=[]
    for p in candidates:
        config=dict(country='US',state='California',city=p['name'],company_types=['wholesale'],keywords=[],geography={'place_id':p['id'],'state_id':'US-CA'})
        plan=await market_plan.plan_market(config,tmp_path)
        assert plan['coverage']['bbox']==p['bbox']
        assert plan['jobs'][0]['geo']==p['geo']
        assert 'Census Bureau' in plan['coverage']['attribution']
        assert not plan['warnings']
        plans.append(plan)
    assert plans[0]['coverage']['bbox']!=plans[1]['coverage']['bbox']


def test_reference_bounds_and_points_are_valid():
    for p in catalogue()['regions']:
        if p['kind']!='place':continue
        s,n,w,e=p['bbox'];lat,lon=p['geo']
        assert -90<=s<n<=90 and -180<=w<e<=180,p['id']
        assert s<=lat<=n and w<=lon<=e,p['id']


def test_country_configs_and_south_africa_hierarchy():
    from leadworkbench.regions import country_configs
    configs = {c['country']: c for c in country_configs()}
    assert configs['US']['mode'] == 'county_filter'
    assert configs['ZA']['mode'] == 'municipal_branch'
    provinces = regions('ZA')
    assert len(provinces) == 9
    districts = [r for r in catalogue('ZA')['regions'] if r['kind'] == 'county']
    places = [r for r in catalogue('ZA')['regions'] if r['kind'] == 'place']
    assert len(districts) == 44
    assert sum(p['subtype'] == 'metropolitan_municipality' for p in places) == 8
    assert sum(p['subtype'] == 'local_municipality' for p in places) == 205
    assert len({r['id'] for r in catalogue('ZA')['regions']}) == len(catalogue('ZA')['regions'])
    for place in places:
        assert index()[place['state_id']]['parent_id'] == 'ZA'
        assert len(place['county_ids']) == (1 if place['subtype'] == 'local_municipality' else 0)
        for county_id in place['county_ids']:
            assert index()[county_id]['state_id'] == place['state_id']
            assert place in regions(place['state_id'], kind='place', county_id=county_id)


def za_selection(subtype):
    p = next(r for r in catalogue('ZA')['regions'] if r.get('subtype') == subtype)
    c = index()[p['county_ids'][0]] if p['county_ids'] else None
    return dict(country='ZA',state=index()[p['state_id']]['name'],city=p['name'],
                state_id=p['state_id'], place_id=p['id'], county_id=c['id'] if c else None,
                county=c['name'] if c else '', keywords=[], company_types=['wholesale'])


@pytest.mark.parametrize('subtype',['metropolitan_municipality','local_municipality'])
def test_south_africa_creates_deduplicated_task_and_retains_scope(tmp_path, subtype):
    store = Store(tmp_path)
    payload = za_selection(subtype)
    task = store.create_market(payload)
    assert task['geography']['scope'] == 'whole_municipality'
    assert task['geography']['verified_selection']
    assert task['geography']['country'] == 'ZA'
    assert payload['city'] in task['name']
    assert task['id'] == store.create_market(payload)['id']
    assert store.task(task['id'])['geography'] == task['geography']
    with pytest.raises(ValueError):
        validate_selection({**payload,'country':'US'})
    with pytest.raises(ValueError):
        validate_selection({**payload,'state_id':'US-CA'})


def test_south_africa_local_requires_district_and_metro_cannot_be_nested():
    local = za_selection('local_municipality')
    metro = za_selection('metropolitan_municipality')
    with pytest.raises(ValueError):
        validate_selection({**local,'county_id':None,'county':''})
    with pytest.raises(ValueError):
        validate_selection({**metro,'county_id':local['county_id'],'county':local['county']})


def test_region_configuration_api(tmp_path):
    with TestClient(create_app(tmp_path,allowed_hosts={'testserver'})) as client:
        client.headers['X-Local-Token'] = client.get('/api/session').json()['token']
        configs = client.get('/api/region-configs').json()
        assert {c['country'] for c in configs} == {'US','ZA'}
        assert all('dataset' not in c for c in configs)
        assert len(client.get('/api/regions?parent_id=ZA').json()) == 9
        payload = za_selection('metropolitan_municipality')
        response = client.post('/api/tasks',json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['geography']['scope'] == 'whole_municipality'


@pytest.mark.asyncio
async def test_za_without_reference_bounds_completes_with_text_fallback(tmp_path,monkeypatch):
    from leadworkbench import market_plan
    payload = validate_selection(za_selection('metropolitan_municipality'))
    p = index()[payload['place_id']]
    monkeypatch.delitem(p, 'bbox', raising=False)
    monkeypatch.delitem(p, 'geo', raising=False)
    async def forbidden(*args,**kwargs):
        raise AssertionError('Must not mistake a municipality for a city geocoder result')
    monkeypatch.setattr(market_plan,'_geocode',forbidden)
    plan = await market_plan.plan_market(payload,tmp_path)
    assert plan['status'] == 'completed'
    assert plan['mode'] == 'text_only'
    assert '自治市文字检索' in plan['coverage']['scope_label']
    assert all(payload['city'] in job['query'] for job in plan['jobs'])
    assert plan['warnings'] and plan['coverage']['bbox'] is None
