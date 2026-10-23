"""Stable Maps recovery uses a fake clock and synthetic source calls only."""
import json
import pytest
from leadworkbench.db import Store
from leadworkbench import maps_policy as policy
from leadworkbench.worker import process_run_item
from test_market_worker import market, planner


def queries(store, run):
    with store.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM work_items WHERE run_id=? AND stage='discovery' ORDER BY rowid",(run,))]


def test_long_backoff_has_three_probes_and_then_stops(tmp_path,monkeypatch):
    store=Store(tmp_path)
    monkeypatch.setattr(policy.random,'uniform',lambda a,b:90)
    with store.connect() as con:
        policy.observe(con,{'status':'completed'},at=100)
        assert policy.status(con,at=189)['retry_after_seconds']==1
        now=190
        for delay in policy.BACKOFF_SECONDS:
            result=policy.observe(con,{'status':'blocked','stop_reason':'timeout'},at=now)
            assert result['auto_retry'] and not result['manual_required']
            assert result['retry_after_seconds']==delay
            assert policy.status(con,at=now+delay)['phase']=='probe'
            now+=delay
        assert policy.observe(con,{'status':'blocked','stop_reason':'timeout'},at=now)['manual_required']
        assert not policy.status(con,at=now+99999)['allowed']


@pytest.mark.asyncio
async def test_timeout_keeps_unattempted_pending_and_only_one_probe_can_be_claimed(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path); run=store.start_task(market(store)['id'])
    clock=[100.]; monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    calls=[]
    async def discover(*args,**kw):
        calls.append(args[0]);return dict(status='blocked',stop_reason='timeout',records=[],warnings=['timeout'])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'],planner=planner)
    for t in [100,700,2500]:
        clock[0]=t; await process_run_item(store,run['id'])
    rows=queries(store,run['id'])
    assert [r['state'] for r in rows]==['pending','pending']
    flow=store.task_activity(run['task_id'])['workflow']
    stage=flow['stages'][1]
    assert stage['processed']==0 and stage['retry_pending']==1 and stage['unattempted']==1
    assert flow['maps_recovery']['phase']=='cooldown'
    await process_run_item(store,run['id'])
    assert len(calls)==3
    # Cooling state survives reopening the store. A query in another run may not run concurrently.
    store=Store(tmp_path);clock[0]=9700
    first=store.claim_item(run['id'],pace_maps=True)
    assert first and first['stage']=='discovery'
    assert store.claim_item(run['id'],pace_maps=True) is None
    assert json.loads(first['result'])['maps']['cycle_attempts']==4


@pytest.mark.asyncio
async def test_restriction_does_not_fail_the_rest_of_queue_and_keeps_website_work(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    from test_market_worker import record
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    async def discover(*args,**kw):return dict(status='blocked',stop_reason='captcha',records=[],warnings=['captcha'])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'],planner=planner)
    await process_run_item(store,run['id'])
    assert [r['state'] for r in queries(store,run['id'])]==['blocked','pending']
    c=store.ingest_market(record(1),run['id'],'fixture',run['task_id']);store.add_enrichment(run['id'],c['id'])
    calls=[]
    async def enrich(*args,**kw):calls.append(1);return dict(status='completed',contacts=[],warnings=[])
    await process_run_item(store,run['id'],enrich=enrich)
    assert calls==[1]
    assert store.run(run['id'])['status'] in {'queued','running'}


@pytest.mark.asyncio
async def test_ambiguous_empty_is_rechecked_twice_then_only_that_query_is_blocked(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    async def discover(*args,**kw):return dict(status='blocked',stop_reason='empty_output',records=[],warnings=['unconfirmed empty'])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'],planner=planner)
    await process_run_item(store,run['id'])
    assert queries(store,run['id'])[0]['state']=='pending'
    # Per-query delay does not halt unrelated ready searches.
    clock[0]=700;await process_run_item(store,run['id'])
    clock[0]=2500;await process_run_item(store,run['id'])
    assert queries(store,run['id'])[0]['state']=='blocked'
    with store.connect() as con:assert not policy.status(con)['manual_required']
    assert json.loads(queries(store,run['id'])[0]['result'])['maps']['attempts']==3


@pytest.mark.asyncio
async def test_browser_crash_retries_per_query_without_global_backoff(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    monkeypatch.setattr(policy.random,'uniform',lambda a,b:60)
    calls=[]
    async def discover(query,*args,**kw):
        calls.append(query)
        return dict(status='blocked',stop_reason='browser_closed',records=[],
                    warnings=['采集程序异常：地图页面被意外关闭，本次未完成'])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'],planner=planner)
    first=queries(store,run['id'])[0]['id']
    for delay in [120,600,1800]:
        await process_run_item(store,run['id'])
        row=queries(store,run['id'])[0]
        meta=json.loads(row['result'])['maps']
        assert row['state']=='pending' and meta['next_attempt_at']==clock[0]+delay
        with store.connect() as con:
            access=policy.status(con)
        assert access['failures']==0 and not access['manual_required']
        clock[0]+=delay
    await process_run_item(store,run['id'])
    row=queries(store,run['id'])[0]
    meta=json.loads(row['result'])['maps']
    assert row['id']==first and row['state']=='blocked'
    assert meta['retry_exhausted'] is True and meta['crash_attempts']==4
    assert calls==['clothing wholesaler a']*4
    with store.connect() as con:
        assert not policy.status(con)['manual_required']
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=? AND stage='discovery' AND id!=?",(run['id'],first))
    clock[0]+=600
    await process_run_item(store,run['id'])
    assert store.run(run['id'])['status'] in {'blocked','partial'}
    store.control(run['id'],'resume')
    row=queries(store,run['id'])[0]
    assert row['state']=='pending' and json.loads(row['result'])['maps']['crash_attempts']==0


@pytest.mark.asyncio
async def test_resume_retries_failed_queries_without_replaying_successes(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    await process_run_item(store,run['id'],planner=planner)
    with store.connect() as con:
        rows=queries(store,run['id'])
        con.execute("UPDATE work_items SET state='succeeded' WHERE id=?",(rows[0]['id'],))
    async def discover(*args,**kw):return dict(status='blocked',stop_reason='captcha',records=[],warnings=['captcha'])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'])
    clock[0]=2000
    store.control(run['id'],'resume')
    after=queries(store,run['id'])
    assert after[0]['state']=='succeeded' and after[1]['state']=='pending'
    meta=json.loads(after[1]['result'])['maps']
    assert meta['attempts']==1 and meta['cycle_attempts']==0


def test_legacy_queue_repair_preserves_success_and_pauses_and_is_idempotent(tmp_path):
    from leadworkbench.maps_migration import repair_legacy_maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    paused_task=store.create_market(dict(country='US',state='Fixture',city='Paused',keywords=['clothing'],company_types=[]))
    paused=store.start_task(paused_task['id']);store.control(paused['id'],'pause')
    message='Scraper stopped: timeout；地图采集保护暂停：连续三次地图访问失败。请确认平台恢复后继续'
    with store.connect() as con:
        for r in [run,paused]:
            for n,state,result in [('good','succeeded',{'status':'completed'}),('never','blocked',{'status':'blocked','message':message}),('timeout','blocked',{'status':'blocked','message':message,'stop_reason':'timeout'}),('empty','blocked',{'status':'blocked','message':'no result','stop_reason':'empty_output'})]:
                con.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?)',(r['id']+n,r['id'],'discovery',n,state,json.dumps(result)))
        con.execute("UPDATE maps_access_policy SET manual_required=1,failures=3,reason='连续三次地图访问失败'")
        summary=repair_legacy_maps(con,tmp_path,at=100)
        assert summary['unattempted_restored']==1 and summary['attempted_requeued']==2
        assert policy.status(con,100)['retry_after_seconds']==policy.BACKOFF_SECONDS[-1]
        assert not policy.status(con,100)['manual_required']
        assert repair_legacy_maps(con,tmp_path,at=200)['runs']==[]
    states={r['target']:r['state'] for r in queries(store,run['id'])}
    assert states=={'good':'succeeded','never':'pending','timeout':'pending','empty':'pending'}
    assert {r['target']:r['state'] for r in queries(store,paused['id'])}['never']=='blocked'


def test_restart_clears_cancelled_orphan_without_resuming_cancelled_run(tmp_path):
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    with store.connect() as con:
        con.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?)',('orphan',run['id'],'discovery','query','running',None))
    store.control(run['id'],'cancel')
    store.recover()
    with store.connect() as con:assert policy.status(con)['allowed']
    assert store.run(run['id'])['status']=='cancelled'


@pytest.mark.asyncio
async def test_source_restriction_survives_ingestion_error(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    from test_market_worker import record
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    async def discover(*args,**kw):return dict(status='blocked',stop_reason='captcha',records=[record(1)],warnings=['captcha'])
    def broken(*args,**kw):raise ValueError('alias conflict fixture')
    monkeypatch.setattr(maps,'discover_maps',discover);monkeypatch.setattr(store,'ingest_market',broken)
    await process_run_item(store,run['id'],planner=planner)
    await process_run_item(store,run['id'])
    with store.connect() as con:assert policy.status(con)['manual_required']
    item=queries(store,run['id'])[0]
    assert item['state']=='blocked' and json.loads(item['result'])['stop_reason']=='captcha'


@pytest.mark.asyncio
async def test_empty_checks_do_not_consume_timeout_retry_budget(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    reasons=iter(['empty_output','empty_output','timeout'])
    async def discover(*args,**kw):return dict(status='blocked',stop_reason=next(reasons),records=[],warnings=[])
    monkeypatch.setattr(maps,'discover_maps',discover)
    await process_run_item(store,run['id'],planner=planner)
    for t in [100,700,2500]:clock[0]=t;await process_run_item(store,run['id'])
    with store.connect() as con:
        assert policy.status(con)['retry_after_seconds']==policy.BACKOFF_SECONDS[0]
        assert policy.status(con)['failures']==1
    meta=json.loads(queries(store,run['id'])[0]['result'])['maps']
    assert meta['transient_failures']==1 and meta['empty_attempts']==2
