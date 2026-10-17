"""Synthetic website graphs; no external requests."""
import json
from unittest.mock import patch
import pytest
from leadworkbench.sources import website as w
from test_website import Response, Session, website_clock
from leadworkbench.db import Store
from leadworkbench.worker import process_run_item

pytestmark = pytest.mark.usefixtures("website_clock")


@pytest.mark.asyncio
async def test_batches_exhaust_more_than_twenty_pages_without_revisiting():
    root = 'https://example.org/'
    routes = {root+'robots.txt': Response(status=404), root: Response(''.join(
        f'<a href="/team-{n}">Team</a>' for n in range(25)))}
    routes.update({root+f'team-{n}': Response('<p>Team</p>') for n in range(25)})
    first = Session(routes)
    with patch.object(w.aiohttp, 'ClientSession', return_value=first):
        a = await w.enrich_website(root)
    assert a['has_more'] and a['summary']['pages_checked'] == 20
    assert 'Page limit reached' not in a['warnings']
    second = Session(routes)
    with patch.object(w.aiohttp, 'ClientSession', return_value=second):
        b = await w.enrich_website(root, checkpoint=json.loads(json.dumps(a['checkpoint'])))
    assert not b['has_more'] and b['status'] == 'completed'
    assert b['summary']['pages_checked'] == 26
    assert not (set(first.requested) & set(second.requested) - {root+'robots.txt'})


@pytest.mark.asyncio
async def test_query_routes_excluded_but_real_contact_route_kept():
    assert w._excluded_path('https://example.org/index.php?route=account%2Fregister')
    assert w._excluded_path('https://example.org/index.php?route=product%2Fcategory&path=2')
    assert not w._excluded_path('https://example.org/index.php?route=information%2Fcontact')
    assert not w._excluded_path('https://example.org/contact?topic=account')


@pytest.mark.asyncio
async def test_worker_persists_batches_rotates_companies_and_recovers(tmp_path):
    store = Store(tmp_path)
    a = store.manual_company('A', 'https://a.example')['id']
    b = store.manual_company('B', 'https://b.example')['id']
    run = store.refresh(a)
    store.add_enrichment(run['id'], b)
    calls = []
    async def enrich(url, *, max_pages, checkpoint=None):
        calls.append((url, checkpoint))
        more = url.startswith('https://a.') and checkpoint is None
        return dict(status='partial' if more else 'completed', warnings=[], observations=[], contacts=[], social_links=[],
                    has_more=more, checkpoint={'batches':1, 'start':url} if more else {'batches':2,'start':url},
                    summary={'pages_checked':20 if more else 23,'pages_skipped':0,'contacts_found':0,'remaining':3 if more else 0})
    await process_run_item(store, run['id'], enrich=enrich)
    assert store.run(run['id'])['status'] == 'running'
    assert store.run(run['id'])['progress']['enrichment_done'] == 0
    store = Store(tmp_path)
    store.recover()
    await process_run_item(store, run['id'], enrich=enrich)
    await process_run_item(store, run['id'], enrich=enrich)
    assert [x[0] for x in calls] == ['https://a.example', 'https://b.example', 'https://a.example']
    assert calls[-1][1]['batches'] == 1
    assert store.run(run['id'])['status'] == 'completed'

@pytest.mark.asyncio
async def test_redirect_alias_is_not_refetched_and_is_logged():
    root = 'https://example.org/'
    session = Session({root+'robots.txt': Response(status=404),
        root: Response('<a href="/team">Team</a><a href="/alias">Team</a>'),
        root+'team': Response('<p>Team</p>'),
        root+'alias': Response(status=302, headers={'Location':'/team'})})
    with patch.object(w.aiohttp, 'ClientSession', return_value=session):
        result = await w.enrich_website(root)
    assert result['status'] == 'completed'
    assert session.requested.count(root+'team') == 1
    assert result['summary']['duplicates'] == 1
    assert result['checkpoint']['logs'][-1]['final_url'] == root+'team'


@pytest.mark.asyncio
async def test_request_budget_yields_and_retries_inflight_target():
    root = 'https://example.org/'
    routes = {root+'robots.txt':Response(status=404),root:Response('<a href="/team">Team</a>'),
              root+'team':Response('<p>Team</p>')}
    with patch.object(w, 'MAX_REQUESTS', 2), patch.object(w.aiohttp,'ClientSession',return_value=Session(routes)):
        first = await w.enrich_website(root)
    assert first['has_more'] and not first['warnings']
    assert root+'team' in first['checkpoint']['queue']
    with patch.object(w.aiohttp,'ClientSession',return_value=Session(routes)):
        last = await w.enrich_website(root, checkpoint=first['checkpoint'])
    assert last['status'] == 'completed' and last['summary']['pages_checked'] == 2


@pytest.mark.asyncio
async def test_failed_pages_wait_for_manual_retry_and_preserve_successes(tmp_path, website_clock):
    root = 'https://example.org/'
    store = Store(tmp_path)
    company = store.manual_company('A',root)
    run = store.refresh(company['id'])
    routes = {root+'robots.txt':Response(status=404),root:Response('<a href="/team">Team</a>'),
              root+'team':Response(status=503)}
    with patch.object(w.aiohttp,'ClientSession',return_value=Session(routes)):
        await process_run_item(store,run['id'])
    assert store.run(run['id'])['status'] == 'partial'
    with pytest.raises(ValueError, match="冷却"):
        store.control(run['id'],'resume')
    website_clock[0] += 601
    store.control(run['id'],'resume')
    routes[root+'team'] = Response('<p>Team</p>')
    session = Session(routes)
    with patch.object(w.aiohttp,'ClientSession',return_value=session):
        await process_run_item(store,run['id'])
    assert store.run(run['id'])['status'] == 'completed'
    assert root not in session.requested
    assert store.run(run['id'])['progress']['website_pages_checked'] == 2


@pytest.mark.asyncio
async def test_cancellation_keeps_last_committed_checkpoint(tmp_path):
    import asyncio
    store = Store(tmp_path)
    company = store.manual_company('A','https://example.org/')
    run = store.refresh(company['id'])
    async def first(*args, **kwargs):
        return dict(status='partial',warnings=[],contacts=[],observations=[],social_links=[],has_more=True,
                    checkpoint={'batches':1,'queue':{'https://example.org/team':[0,1,1]}},
                    summary={'pages_checked':20,'remaining':1})
    await process_run_item(store,run['id'],enrich=first)
    started = asyncio.Event()
    async def paused(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(process_run_item(store,run['id'],enrich=paused))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with store.connect() as con:
        row = con.execute('select state,result from work_items where run_id=?',(run['id'],)).fetchone()
    assert row['state'] == 'pending'
    assert json.loads(row['result'])['checkpoint']['batches'] == 1

def test_manual_retry_preserves_permanent_scope_warnings(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company('A','https://example.org/')
    run = store.refresh(company['id'])
    item = store.claim_item(run['id'])
    store.finish_item(item['id'],'partial',{'checkpoint':{
        'queue':{},'failures':{'https://example.org/team':[0,1,1]},'failure_warnings':{'https://example.org/team':'https://example.org/team: HTTPError: 503'},'attempted':['https://example.org/team'],
        'warnings':['Link depth limit reached','https://example.org/team: HTTPError: 503']}})
    store.finalize(run['id'])
    store.control(run['id'],'resume')
    with store.connect() as con:
        value = json.loads(con.execute('select result from work_items where id=?',(item['id'],)).fetchone()[0])
    assert value['checkpoint']['warnings'] == ['Link depth limit reached']

@pytest.mark.asyncio
async def test_robot_delay_survives_checkpoint_and_restart():
    import asyncio
    root = 'https://example.org/'
    routes = {root+'robots.txt':Response('User-agent: *\nCrawl-delay: 10'),
              root:Response('<a href="/team">Team</a>'),root+'team':Response('<p>Team</p>')}
    clock = [1000.0]
    waits = []
    async def sleep(delay):
        waits.append(delay)
        clock[0] += delay
    with patch.object(w.time,'time',side_effect=lambda:clock[0]), patch.object(w.asyncio,'sleep',side_effect=sleep):
        with patch.object(w.aiohttp,'ClientSession',return_value=Session(routes)):
            first = await w.enrich_website(root,max_pages=1)
        checkpoint = json.loads(json.dumps(first['checkpoint']))
        before = len(waits)
        with patch.object(w.aiohttp,'ClientSession',return_value=Session(routes)):
            await w.enrich_website(root,max_pages=1,checkpoint=checkpoint)
    assert len(waits) > before and sum(waits[before:]) >= 10


def test_retry_keeps_parse_warning_for_same_failed_url(tmp_path):
    store = Store(tmp_path)
    company = store.manual_company('A','https://example.org/')
    run = store.refresh(company['id'])
    item = store.claim_item(run['id'])
    url = 'https://example.org/team'
    parse_warning = url+': Structured data limit reached'
    failure = url+': HTTPError: 503'
    store.finish_item(item['id'],'partial',{'checkpoint':{
        'queue':{},'failures':{url:[0,1,1]},'failure_warnings':{url:failure},'attempted':[url],
        'warnings':[parse_warning, failure]}})
    store.finalize(run['id'])
    store.control(run['id'],'resume')
    with store.connect() as con:
        value = json.loads(con.execute('select result from work_items where id=?',(item['id'],)).fetchone()[0])
    assert value['checkpoint']['warnings'] == [parse_warning]
