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
        assert first['auto_retry'] and first['retry_after_seconds']==600
        second=policy.observe(con,{'stop_reason':'timeout'},760)
        assert second['auto_retry'] and second['retry_after_seconds']==1800
        third=policy.observe(con,{'stop_reason':'timeout'},2560)
        assert third['auto_retry'] and third['retry_after_seconds']==7200
        fourth=policy.observe(con,{'stop_reason':'timeout'},9760)
        assert fourth['manual_required'] and not fourth['auto_retry']
    restored = Store(tmp_path)
    with restored.connect() as con:
        with pytest.raises(ValueError):policy.resume(con,9761)
        assert policy.status(con,11561)['manual_required']
        policy.resume(con,11561)
        assert policy.status(con,11561)['allowed']


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
    clock[0]=220
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
