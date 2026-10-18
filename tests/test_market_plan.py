import asyncio
import copy
import json

import pytest
from leadworkbench import market_plan as market


@pytest.fixture(autouse=True)
def no_catalogue_fallback(monkeypatch):
    """These tests cover the geocoder path; the P0-5 catalogue fallback is tested in test_market_relocate.py."""
    monkeypatch.setattr(market, "_catalogue_fallback", lambda *args, **kwargs: None)

CONFIG = dict(city='Los Angeles', state='California', country='US',
              company_types=['wholesale'], keywords=['custom garments'])
CITY = dict(lat='34.1', lon='-118.3', boundingbox=['33.7', '34.5', '-118.7', '-118.1'],
            addresstype='city', category='boundary', type='administrative',
            display_name='Los Angeles, California, United States', address={'country_code': 'us'})


@pytest.fixture
def geocoder(tmp_path, monkeypatch):
    calls = []
    async def fetch(endpoint, params):
        calls.append((endpoint, params))
        return [copy.deepcopy(CITY)]
    monkeypatch.setattr(market, '_fetch_city', fetch)
    monkeypatch.setattr(market, 'RATE_LIMIT_FILE', tmp_path / 'rate.lock')
    monkeypatch.setattr(market, 'REQUEST_SPACING', 0)
    return calls


async def test_city_grid_cache_and_no_company_cap(tmp_path, geocoder):
    plan = await market.plan_market({**CONFIG, 'limit': 1}, tmp_path)
    assert plan['status'] == 'completed'
    assert plan['coverage']['bbox'] == [33.7, 34.5, -118.7, -118.1]
    assert 'OpenStreetMap' in plan['coverage']['attribution']
    assert plan['coverage']['scope_label'] == 'Los Angeles, California, US · 城市参考范围分区检索'
    roots = [j for j in plan['jobs'] if j['level'] == 0]
    cells = [j for j in plan['jobs'] if j['level'] == 1]
    assert len(roots) == 1  # Keywords given: only the keyword is searched.
    assert len(cells) == len(roots) * 16
    assert all(j['zoom'] == 12 for j in roots)
    assert all(j['zoom'] == 14 for j in cells)
    assert len({j['key'] for j in plan['jobs']}) == len(plan['jobs'])
    assert all(j['depth'] == 10 for j in plan['jobs'])
    assert json.loads(json.dumps(plan)) == plan
    again = await market.plan_market({**CONFIG, 'limit': 10000}, tmp_path)
    assert again == plan
    assert len(geocoder) == 1
    params = geocoder[0][1]
    assert params['city'] == 'Los Angeles' and params['state'] == 'California'
    assert params['countrycodes'] == 'us' and params['featureType'] == 'city'
    assert not {'q', 'amenity', 'street'} & params.keys()
    for job in cells:
        s, n, w, e = job['bbox']
        lat, lon = job['geo']
        assert s <= lat <= n and w <= lon <= e
    boxes = {tuple(j['bbox']) for j in cells}
    area = sum((n-s)*(e-w) for s,n,w,e in boxes)
    assert area == pytest.approx((34.5-33.7)*(-118.1+118.7))


async def test_keywords_only_when_keywords_given_stable_dedup(tmp_path, geocoder):
    first = await market.plan_market({**CONFIG, 'company_types': ['retail', 'wholesale', 'import'],
                                      'keywords': ['  Custom   garments ', 'CUSTOM garments']}, tmp_path)
    second = await market.plan_market({**CONFIG, 'company_types': ['import', 'retail', 'wholesale', 'retail'],
                                       'keywords': ['custom garments']}, tmp_path)
    assert first['jobs'] == second['jobs']
    roots = [j['query'] for j in first['jobs'] if j['level'] == 0]
    assert roots == ['custom garments in Los Angeles, California, US']
    assert not any(t in q for q in roots for t in ('wholesaler', 'retailer', 'importer'))
    assert first['coverage']['query_basis'] == 'keywords'
    assert first['coverage']['query_terms'] == ['custom garments']
async def test_types_only_still_use_default_queries(tmp_path, geocoder):
    plan = await market.plan_market({**CONFIG, 'company_types': ['retail', 'wholesale', 'import'],
                                     'keywords': []}, tmp_path)
    roots = [j['query'] for j in plan['jobs'] if j['level'] == 0]
    assert any('wholesaler' in q for q in roots)
    assert any('retailer' in q for q in roots)
    assert any('importer' in q for q in roots)
    assert plan['coverage']['query_basis'] == 'company_types'
    assert 'clothing wholesaler' in plan['coverage']['query_terms']
async def test_wholesale_with_metal_roof_keyword_has_no_apparel_queries(tmp_path, geocoder):
    plan = await market.plan_market({**CONFIG, 'company_types': ['wholesale'], 'keywords': ['metal roof']}, tmp_path)
    assert plan['jobs']
    assert all(j['query'].startswith('metal roof in ') for j in plan['jobs'])
    assert not any('apparel' in j['query'] or 'clothing' in j['query'] for j in plan['jobs'])
    assert plan['coverage']['query_basis'] == 'keywords'
    assert plan['warnings'] == []
async def test_subdivision_and_unresolved_leaf(tmp_path, geocoder):
    plan = await market.plan_market(CONFIG, tmp_path)
    cell = next(j for j in plan['jobs'] if j['level'] == 1)
    saved = copy.deepcopy(cell)
    children = market.split_job(cell)
    assert cell == saved and len(children) == 4
    assert children == market.split_job(cell)
    assert all(j['level'] == 2 for j in children)
    leaves = market.split_job(children[0])
    assert len(leaves) == 4 and all(j['level'] == 3 for j in leaves)
    assert market.split_job(leaves[0]) == []
    assert market.split_job({**cell, 'bbox': [34, 34.0001, -118, -117.9999]}) == []
    assert market.split_job({**cell, 'bbox': None}) == []


@pytest.mark.parametrize('patch', [
    {'lat': 'nan'}, {'lon': '181'}, {'boundingbox': ['34', '33', '-118', '-117']},
    {'boundingbox': ['33', '34', '179', '-179']}, {'lat': '60'},
    {'addresstype': 'shop'}, {'address': {'country_code': 'ca'}},
])
async def test_invalid_city_falls_back_without_geometry(tmp_path, monkeypatch, geocoder, patch):
    async def fetch(*args):
        return [{**CITY, **patch}]
    monkeypatch.setattr(market, '_fetch_city', fetch)
    plan = await market.plan_market(CONFIG, tmp_path)
    assert plan['status'] == 'completed' and plan['warnings']
    assert plan['coverage']['bbox'] is None
    assert plan['coverage']['scope_label'] == 'Los Angeles, California, US · 城市文字检索（范围定位未完成）'
    assert all(j['geo'] is None and j['bbox'] is None and j['level'] == 0 for j in plan['jobs'])


async def test_failed_geocode_is_cached_no_immediate_retry(tmp_path, monkeypatch, geocoder):
    calls = 0
    async def fail(*args):
        nonlocal calls
        calls += 1
        raise TimeoutError('fixture timeout')
    monkeypatch.setattr(market, '_fetch_city', fail)
    first = await market.plan_market(CONFIG, tmp_path)
    second = await market.plan_market(CONFIG, tmp_path)
    assert first == second and calls == 1 and first['status'] == 'completed'


async def test_simultaneous_same_city_single_request(tmp_path, geocoder):
    a, b = await asyncio.gather(market.plan_market(CONFIG, tmp_path), market.plan_market(CONFIG, tmp_path))
    assert a == b and len(geocoder) == 1


async def test_private_endpoint_no_request(tmp_path, monkeypatch, geocoder):
    monkeypatch.setenv('NOMINATIM_ENDPOINT', 'http://127.0.0.1/search')
    result = await market.plan_market(CONFIG, tmp_path)
    assert result['status'] == 'completed' and not geocoder


async def test_rate_reservation_shared_across_data_dirs(tmp_path, monkeypatch, geocoder):
    monkeypatch.setattr(market, 'REQUEST_SPACING', 15)
    clock = [1000.0]
    sleeps = []
    monkeypatch.setattr(market.time, 'time', lambda: clock[0])
    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(market.asyncio, 'sleep', sleep)
    await market.plan_market(CONFIG, tmp_path / 'one')
    await market.plan_market({**CONFIG, 'city': 'Other City'}, tmp_path / 'two')
    assert sum(sleeps) >= 15 and len(geocoder) == 2


@pytest.mark.parametrize('config', [{**CONFIG, 'city': 'a\nb'}, {**CONFIG, 'country': 'United States'},
                                    {**CONFIG, 'keywords': ['x#!#id']}])
async def test_invalid_input(config, tmp_path):
    with pytest.raises(ValueError):
        await market.plan_market(config, tmp_path)


@pytest.mark.parametrize('status,declared,chunks,ok', [
    (200, None, [json.dumps([CITY]).encode()], True),
    (302, None, [b'[]'], False),
    (429, None, [b'[]'], False),
    (200, 256001, [], False),
    (200, None, [b'x' * 160000, b'x' * 160000], False),
    (200, None, [b'not-json'], False),
])
async def test_safe_bounded_http(monkeypatch, status, declared, chunks, ok):
    captured = {}
    class Content:
        async def iter_chunked(self, size):
            for chunk in chunks:
                yield chunk
    class Response:
        content_length = declared
        content = Content()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    response = Response()
    response.status = status
    class Session:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, endpoint, **kwargs):
            captured['request'] = kwargs
            return response
    def connector(**kwargs):
        captured['connector_options'] = kwargs
        return object()
    monkeypatch.setattr(market.aiohttp, 'TCPConnector', connector)
    monkeypatch.setattr(market.aiohttp, 'ClientSession', Session)
    if ok:
        assert await market._fetch_city('https://nominatim.openstreetmap.org/search', {'city': 'fixture'}) == [CITY]
    else:
        with pytest.raises(ValueError):
            await market._fetch_city('https://nominatim.openstreetmap.org/search', {'city': 'fixture'})
    assert captured['request']['allow_redirects'] is False
    assert captured['trust_env'] is False
    assert captured['timeout'].total == 20
    assert captured['headers']['User-Agent'].startswith('LocalLeadWorkbench/')
    assert isinstance(captured['connector_options']['resolver'], market.PublicResolver)
    assert captured['connector_options']['use_dns_cache'] is False


async def test_dns_private_answers_rejected(monkeypatch):
    import socket
    async def private(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', private)
    with pytest.raises(market.UnsafeURL):
        await market.PublicResolver().resolve('public-looking.example', 443)


async def test_failure_ttl_recovers_then_success_never_expires(tmp_path, monkeypatch, geocoder):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(market.time, 'time', lambda: clock[0])
    calls = 0
    async def transient(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError('transient fixture failure')
        return [copy.deepcopy(CITY)]
    monkeypatch.setattr(market, '_fetch_city', transient)
    first = await market.plan_market(CONFIG, tmp_path)
    assert first['status'] == 'completed'
    clock[0] += 899
    assert await market.plan_market(CONFIG, tmp_path) == first
    assert calls == 1
    clock[0] += 1
    recovered, concurrent = await asyncio.gather(
        market.plan_market(CONFIG, tmp_path), market.plan_market(CONFIG, tmp_path))
    assert recovered['status'] == 'completed' and concurrent == recovered and calls == 2
    clock[0] += 10 * 365 * 24 * 3600
    assert await market.plan_market(CONFIG, tmp_path) == recovered
    assert calls == 2


async def test_repeated_failure_renews_ttl(tmp_path, monkeypatch, geocoder):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(market.time, 'time', lambda: clock[0])
    calls = 0
    async def fail(*args):
        nonlocal calls
        calls += 1
        raise TimeoutError('fixture failure')
    monkeypatch.setattr(market, '_fetch_city', fail)
    await market.plan_market(CONFIG, tmp_path)
    clock[0] += 900
    await market.plan_market(CONFIG, tmp_path)
    assert calls == 2
    clock[0] += 899
    await market.plan_market(CONFIG, tmp_path)
    assert calls == 2
    clock[0] += 1
    await market.plan_market(CONFIG, tmp_path)
    assert calls == 3


async def test_legacy_failed_cache_expires_from_observed_at(tmp_path, monkeypatch, geocoder):
    from datetime import datetime, timezone
    clock = [1_800_000_000.0]
    monkeypatch.setattr(market.time, 'time', lambda: clock[0])
    await market.plan_market(CONFIG, tmp_path)
    cache = next((tmp_path / 'geocoding').glob('*.json'))
    saved = json.loads(cache.read_text())
    saved.update(status='partial', warning='legacy failure',
                 observed_at=datetime.fromtimestamp(clock[0], timezone.utc).isoformat())
    saved.pop('failed_at', None)
    cache.write_text(json.dumps(saved))
    clock[0] += 899
    assert (await market.plan_market(CONFIG, tmp_path))['status'] == 'completed'
    assert len(geocoder) == 1
    clock[0] += 1
    assert (await market.plan_market(CONFIG, tmp_path))['status'] == 'completed'
    assert len(geocoder) == 2


@pytest.mark.parametrize('error', [TimeoutError(), OSError(), ValueError()])
async def test_empty_geocode_exception_has_chinese_message_and_type(tmp_path, monkeypatch, geocoder, error):
    calls = 0
    async def fail(*args):
        nonlocal calls
        calls += 1
        raise error
    monkeypatch.setattr(market, '_fetch_city', fail)
    result = await market.plan_market(CONFIG, tmp_path)
    assert result['status'] == 'completed'
    warning = result['warnings'][0]
    assert type(error).__name__ in warning
    assert '城市' in warning and '文字检索' in warning
    if isinstance(error, TimeoutError):
        assert '超时' in warning
    assert (await market.plan_market(CONFIG, tmp_path))['warnings'] == result['warnings']
    assert calls == 1


async def test_legacy_blank_warning_presented_without_lookup_or_guessing_type(tmp_path, geocoder):
    await market.plan_market(CONFIG, tmp_path)
    cache = next((tmp_path / 'geocoding').glob('*.json'))
    saved = json.loads(cache.read_text())
    saved.update(status='partial', failed_at=market.time.time(),
                 warning='City geocoding unavailable: ; root searches only, geometry unverified')
    cache.write_text(json.dumps(saved))
    result = await market.plan_market(CONFIG, tmp_path)
    assert result['status'] == 'completed'
    assert '城市' in result['warnings'][0] and '未记录异常类型' in result['warnings'][0]
    assert 'TimeoutError' not in result['warnings'][0]
    assert result['coverage']['bbox'] is None
    assert len(geocoder) == 1
    assert json.loads(cache.read_text()) == saved
