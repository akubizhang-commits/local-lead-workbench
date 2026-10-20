import json
import pytest
from leadworkbench.db import Store


def setup(store):
    task = store.create_market(dict(name='Market', country='US', state='CA', city='LA', keywords=['clothing'],company_types=['wholesale'],auto_enrich=True))
    company = store.manual_company('Supplier', 'https://example.org')
    other = store.manual_company('Other', 'https://other.example')
    run = store.start_task(task['id'])
    # Single re-check first: once the company sits in the market round's queue, refresh() reuses that
    # round (already_queued, design §6.3) instead of creating a standalone run.
    manual = store.refresh(company['id'])
    store.add_enrichment(run['id'],company['id'])
    store.refresh(other['id'])
    with store.connect() as con:
        con.execute('INSERT INTO market_memberships VALUES(?,?,?,?,?,?)',(task['id'],company['id'],'2026-01-01','2026-01-01',run['id'],run['id']))
    return task,company,run,manual


def test_task_contains_own_enrichment_and_related_standalone_without_false_parentage(tmp_path):
    store=Store(tmp_path)
    task,company,run,manual=setup(store)
    page=store.task_activity(task['id'])
    assert page['total']==2
    assert {r['source'] for r in page['items']}=={'market','standalone'}
    assert {r['company_id'] for r in page['items']}=={company['id']}
    assert [r['id'] for r in page['rounds']]==[run['id']]
    scoped=store.task_activity(task['id'],run_id=run['id'])
    assert scoped['total']==1 and scoped['items'][0]['source']=='market'
    assert store.task_activity(task['id'],status='failed')['total']==0
    assert store.task_activity(task['id'],page=2)['items']==[]
    assert store.refresh_activity()['total']==2
    with pytest.raises(KeyError): store.task_activity('missing')


def test_detail_requires_matching_run_and_does_not_ship_queue(tmp_path):
    store=Store(tmp_path)
    task,company,run,manual=setup(store)
    item=store.task_activity(task['id'],run_id=run['id'])['items'][0]
    with store.connect() as con:
        con.execute('UPDATE work_items SET result=? WHERE id=?',(json.dumps({'summary':{'pages_checked':1},'checkpoint':{'queue':{'private':1},'logs':[{'url':'https://example.org','status':'parsed'}]}}),item['id']))
    detail=store.refresh_detail(run['id'],item['id'])
    assert detail['company_name']=='Supplier'
    assert detail['summary']['pages_checked']==1 and len(detail['logs'])==1
    assert 'checkpoint' not in detail and 'result' not in detail
    with pytest.raises(KeyError): store.refresh_detail(manual['id'],item['id'])


def test_activity_api_validates_and_protects_new_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    app=create_app(tmp_path,allowed_hosts={'testserver'})
    task,_,run,_=setup(app.state.store)
    with TestClient(app) as client:
        assert client.get('/api/refresh-activity').status_code==401
        client.headers['X-Local-Token']=client.get('/api/session').json()['token']
        page=client.get(f"/api/tasks/{task['id']}/activity").json()
        item=page['items'][0]
        assert client.get(f"/api/runs/{item['run_id']}/items/{item['id']}").status_code==200
        assert client.get('/api/refresh-activity?page=0').status_code==422
        assert client.get('/api/refresh-activity?status=bad').status_code==422
        assert client.get('/api/tasks/missing/activity').status_code==404


@pytest.mark.asyncio
async def test_worker_retains_empty_exception_type(tmp_path):
    from leadworkbench.worker import process_run_item
    store=Store(tmp_path)
    company=store.manual_company('A','https://example.org')
    run=store.refresh(company['id'])
    async def broken(*args,**kwargs):
        raise TimeoutError()
    await process_run_item(store,run['id'],enrich=broken)
    item=store.refresh_activity()['items'][0]
    assert item['state']=='failed'
    assert item['message']=='TimeoutError'


def test_workflow_counts_success_and_failures_separately_and_ignores_page_filter(tmp_path):
    store=Store(tmp_path);task,company,run,manual=setup(store)
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='planning'",(run['id'],))
        for n,state in enumerate(['succeeded','running','pending','blocked','partial','failed']):
            con.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?)',
                        (f'query-{n}',run['id'],'discovery',json.dumps({'query':f'clothing in city {n}'}),state,None))
    data=store.task_activity(task['id'],page=2,status='failed')['workflow']
    maps=data['stages'][1]
    assert maps['total']==6 and maps['processed']==4 and maps['succeeded']==1
    assert maps['running']==maps['pending']==maps['blocked']==maps['failed']==maps['partial']==1
    assert data['current']=='正在检索：clothing in city 1'
    assert data['stages'][2]['total']==1  # excludes independently triggered refresh


def test_workflow_wait_offline_pause_and_historical_round_selection(tmp_path):
    from leadworkbench import maps_policy
    store=Store(tmp_path);task,company,run,manual=setup(store)
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='planning'",(run['id'],))
        con.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?)',('waiting-query',run['id'],'discovery','{}','pending',None))
        maps_policy.observe(con,{'stop_reason':'captcha'})
    data=store.task_activity(task['id'])['workflow']
    assert '保护暂停' in data['current'] and data['maps_wait']
    store.control(run['id'],'pause')
    data=store.task_activity(task['id'])['workflow']
    assert data['current']=='任务已暂停，等待手动继续' and data['maps_wait'] is None
    store.control(run['id'],'cancel')
    other=store.start_task(task['id'])
    assert store.task_activity(task['id'],run_id=run['id'])['workflow']['status']=='cancelled'
    assert store.task_activity(task['id'],run_id=other['id'])['workflow']['run_id']==other['id']
    assert store.task_activity(task['id'],run_id='not-this-market')['workflow'] is None


def test_workflow_explains_planning_fallback_and_other_errors(tmp_path):
    store=Store(tmp_path);task,company,run,manual=setup(store)
    message='City cache unavailable: 所选地区目录未提供范围坐标；按自治市完整名称进行文字检索; root searches only'
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='partial',result=? WHERE run_id=? AND stage='planning'",(json.dumps({'message':message,'status':'partial'}),run['id']))
    stage=store.task_activity(task['id'])['workflow']['stages'][0]
    assert stage['mode']=='text_only'
    assert stage['processed']==1 and stage['succeeded']==0 and stage['partial']==1
    assert '无法进行坐标分区检索' in stage['details'][0]
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='failed',result=? WHERE run_id=? AND stage='planning'",(json.dumps({'message':'Invalid country'}),run['id']))
    stage=store.task_activity(task['id'])['workflow']['stages'][0]
    assert stage['mode'] is None
    assert stage['details']==['Invalid country']


def test_completed_fallback_is_not_an_unresolved_task_but_real_failures_remain(tmp_path):
    store=Store(tmp_path);task,company,run,manual=setup(store)
    store.save_market_plan(run['id'], {'status':'completed','mode':'text_only',
        'warnings':['地区范围不可用，暂用城市文字检索'], 'coverage':{'bbox':None},
        'jobs':[{'key':'root','query':'fixture'}]})
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?",(run['id'],))
        con.execute("UPDATE work_items SET result=? WHERE run_id=? AND stage='planning'",(json.dumps({'status':'completed','mode':'text_only','message':'地区范围不可用，暂用城市文字检索'}),run['id']))
    store.finalize(run['id'])
    # P0-5: a text-only round is reported incomplete (one reason), yet planning itself succeeded.
    assert store.run(run['id'])['status']=='partial'
    [reason]=store.task(task['id'])['coverage']['unresolved']
    assert reason.startswith('结果不完整：') and store.task(task['id'])['coverage']['relocatable'] is True
    stage=store.task_activity(task['id'])['workflow']['stages'][0]
    assert stage['succeeded']==1 and stage['partial']==0 and stage['mode']=='text_only'
    with store.connect() as con:
        con.execute("UPDATE runs SET status='running' WHERE id=?",(run['id'],))
        con.execute("UPDATE work_items SET state='failed' WHERE run_id=? AND stage='discovery'",(run['id'],))
    store.finalize(run['id'])
    assert store.run(run['id'])['status']=='partial'


def _discovery(con, run_id, item_id, query, state, result):
    con.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?)',
                (item_id, run_id, 'discovery', json.dumps({'query': query}), state, json.dumps(result)))


def _maps(reason, attempts=1, next_at=None):
    return {'message': 'x', 'stop_reason': reason,
            'maps': {'attempts': attempts, 'last_stop_reason': reason, 'next_attempt_at': next_at,
                     'last_attempt_at': '2026-10-15T00:00:00+00:00',
                     'history': [{'attempt': 1, 'stop_reason': reason, 'message': 'x'}]}}


def test_workflow_failure_kind_separates_scraper_errors_from_empty_results(tmp_path):
    store=Store(tmp_path);task,company,run,manual=setup(store)
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='planning'",(run['id'],))
        _discovery(con, run['id'], 'crash', 'clothing in a', 'blocked', _maps('browser_closed'))
        _discovery(con, run['id'], 'empty', 'clothing in b', 'blocked', _maps('empty_output'))
        _discovery(con, run['id'], 'paused', 'clothing in c', 'partial', _maps('cancelled'))
        _discovery(con, run['id'], 'skipped', 'clothing wholesaler in d', 'succeeded',
                   {**_maps('skipped_legacy_query', attempts=2), 'stop_reason': 'skipped_legacy_query'})
    flow=store.task_activity(task['id'])['workflow']
    by_id={q['id']:q for q in flow['maps_queries']}
    assert by_id['crash']['failure_kind']=='scraper_error' and by_id['crash']['stop_reason']=='browser_closed'
    assert by_id['crash']['history'][0]['failure_kind']=='scraper_error'
    assert by_id['empty']['failure_kind']=='no_results'
    assert by_id['paused']['failure_kind'] is None
    assert 'skipped' not in by_id and flow['maps_query_issue_count']==3


@pytest.mark.parametrize('reason,text', [
    ('browser_closed', '采集程序异常，等待自动重试，已保存查询与已有结果'),
    ('empty_output', '等待空结果复核时间，已保存查询与已有结果'),
])
def test_query_wait_text_depends_on_earliest_retry_reason(tmp_path, monkeypatch, reason, text):
    from leadworkbench import maps_policy
    store=Store(tmp_path);task,company,run,manual=setup(store)
    monkeypatch.setattr(maps_policy.time, 'time', lambda: 1000.0)
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='planning'",(run['id'],))
        _discovery(con, run['id'], 'first', 'clothing in a', 'pending', _maps(reason, next_at=1500.0))
        _discovery(con, run['id'], 'later', 'clothing in b', 'pending',
                   _maps('empty_output' if reason == 'browser_closed' else 'browser_closed', next_at=1900.0))
    flow=store.task_activity(task['id'])['workflow']
    assert flow['current']==text
