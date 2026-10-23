import json
import pytest
from leadworkbench.db import Store
from leadworkbench import maps_policy as policy
from leadworkbench.worker import process_run_item
from test_market_worker import market, planner


def test_policy_persists_and_transient_backoff_is_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(policy.random,"uniform",lambda a,b:60)
    store = Store(tmp_path)
    with store.connect() as con:
        assert policy.status(con,100)['allowed']
        policy.observe(con,{'status':'completed'},100)
        assert policy.status(con,159)['retry_after_seconds']==1
        assert policy.status(con,160)['allowed']
        first=policy.observe(con,{'stop_reason':'timeout'},160)
        assert first['auto_retry'] and first['retry_after_seconds']==policy.BACKOFF_SECONDS[0]
        second=policy.observe(con,{'stop_reason':'timeout'},160+policy.BACKOFF_SECONDS[0])
        assert second['auto_retry'] and second['retry_after_seconds']==policy.BACKOFF_SECONDS[1]
        third=policy.observe(con,{'stop_reason':'timeout'},160+policy.BACKOFF_SECONDS[0]+policy.BACKOFF_SECONDS[1])
        assert third['auto_retry'] and third['retry_after_seconds']==policy.BACKOFF_SECONDS[2]
        fourth=policy.observe(con,{'stop_reason':'timeout'},160+sum(policy.BACKOFF_SECONDS))
        assert fourth['manual_required'] and not fourth['auto_retry']
    restored = Store(tmp_path)
    manual_at = 160 + sum(policy.BACKOFF_SECONDS)
    cooled_at = manual_at + policy.MANUAL_COOLDOWN_SECONDS + 1
    with restored.connect() as con:
        with pytest.raises(ValueError):policy.resume(con,manual_at+1)
        assert policy.status(con,cooled_at)['manual_required']
        policy.resume(con,cooled_at)
        assert policy.status(con,cooled_at)['allowed']


@pytest.mark.parametrize('reason',['captcha','rate_limited','access_denied'])
def test_restriction_needs_explicit_resume_even_after_cooldown(tmp_path,reason):
    store=Store(tmp_path)
    with store.connect() as con:
        result=policy.observe(con,{'stop_reason':reason,'records':[{}]},100)
        assert result['manual_required'] and not result['auto_retry']
        assert not policy.status(con,9999)['allowed']
        policy.resume(con,9999)
        assert policy.status(con,9999)['allowed']


@pytest.mark.asyncio
async def test_real_boundary_gate_is_shared_by_runs_and_preserves_jobs(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path)
    task=market(store); run=store.start_task(task['id'])
    other=store.create_market(dict(country='US',state='Fixture',city='Other',keywords=['clothing'],company_types=[]))
    run2=store.start_task(other['id'])
    clock=[100.]
    monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    calls=[]
    async def fake(*args,**kwargs):
        calls.append(args[0]);return dict(status='completed',records=[],warnings=[],stop_reason='finished')
    monkeypatch.setattr(maps,'discover_maps',fake)
    await process_run_item(store,run['id'],planner=planner)
    await process_run_item(store,run2['id'],planner=planner)
    await process_run_item(store,run['id'])
    assert len(calls)==1
    await process_run_item(store,run2['id'])
    assert len(calls)==1 and '低速等待' in store.run(run2['id'])['message']
    clock[0]=341
    await process_run_item(store,run2['id'])
    assert len(calls)==2


@pytest.mark.asyncio
async def test_backoff_retries_same_pending_unit_then_circuit_opens(tmp_path,monkeypatch):
    from leadworkbench.sources import maps
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    calls=[]
    async def fake(*args,**kwargs):
        calls.append(args[0]);return dict(status='blocked',stop_reason='timeout',records=[],warnings=['timeout'])
    monkeypatch.setattr(maps,'discover_maps',fake)
    await process_run_item(store,run['id'],planner=planner)
    for t in [100,700,2500,9700]:
        clock[0]=t
        await process_run_item(store,run['id'])
    assert len(calls)==4 and len(set(calls))==1
    with store.connect() as con:
        assert policy.status(con)['manual_required']
        assert con.execute("SELECT count(*) FROM work_items WHERE stage='discovery' AND state='blocked'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_cooldown_does_not_block_website_enrichment(tmp_path,monkeypatch):
    from test_market_worker import record
    store=Store(tmp_path);run=store.start_task(market(store)['id'])
    await process_run_item(store,run['id'],planner=planner)
    company=store.ingest_market(record(1),run['id'],'fixture',run['task_id'])
    store.add_enrichment(run['id'],company['id'])
    with store.connect() as con:policy.observe(con,{'stop_reason':'captcha'})
    calls=[]
    async def enrich(url,**kw):
        calls.append(url);return dict(status='completed',contacts=[],warnings=[],pages_checked=0)
    await process_run_item(store,run['id'],enrich=enrich)
    assert len(calls)==1


def test_global_recovery_api_obeys_cooldown_and_requires_local_token(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    app=create_app(tmp_path,allowed_hosts={'testserver'})
    clock=[100.];monkeypatch.setattr(policy.time,'time',lambda:clock[0])
    with app.state.store.connect() as con:policy.observe(con,{'stop_reason':'captcha'})
    with TestClient(app) as client:
        assert client.post('/api/maps/resume',json={}).status_code==401
        client.headers['X-Local-Token']=client.get('/api/session').json()['token']
        assert client.post('/api/maps/resume',json={}).status_code==409
        clock[0]=1901
        result=client.post('/api/maps/resume',json={})
        assert result.status_code==200 and result.json()['allowed']


def test_timeout_with_results_is_benign_and_does_not_count_globally(tmp_path):
    # Maps returns a large, region-unbounded result set: a tile can time out while
    # still producing records. That benign partial must not raise the global
    # failure counter (so it can never drive the installation into cooldown), and
    # it must not ask for a global auto-retry — subdivision handles coverage.
    store = Store(tmp_path)
    with store.connect() as con:
        for i in range(6):
            r = policy.observe(con, {"status": "partial", "stop_reason": "timeout",
                                     "records": [{"source_key": f"x{i}"}]}, at=100 + i)
            assert not r["auto_retry"]
            assert not r["manual_required"]
        assert policy.status(con, 9999)["failures"] == 0
        assert policy.status(con, 9999)["allowed"]


def test_timeout_without_results_still_backs_off_globally(tmp_path):
    # A timeout with zero records keeps the original throttling semantics.
    store = Store(tmp_path)
    with store.connect() as con:
        r = policy.observe(con, {"status": "blocked", "stop_reason": "timeout",
                                 "records": []}, at=100)
        assert r["auto_retry"] and r["retry_after_seconds"] == policy.BACKOFF_SECONDS[0]
        assert policy.status(con, 100)["failures"] == 1
