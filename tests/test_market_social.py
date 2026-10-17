import asyncio
import json
import pytest

from leadworkbench.db import Store


def setup_market(tmp_path, **overrides):
    store = Store(tmp_path)
    task = store.create_market({**dict(country="US", state="Fixture", city="Fixture",
        keywords=["clothing"], company_types=[], auto_enrich=True, auto_social=True,
        social_platforms=["facebook", "instagram", "x"], social_read_posts=False), **overrides})
    run = store.start_task(task["id"])
    return store, task, run


def ingest(store, task, run, n=1, website=""):
    return store.ingest_market(dict(source_key=f"fixture:{n}",
        source_url=f"https://www.google.com/maps?cid={n}", name=f"Fixture Company {n}",
        address=f"{n} Fixture Street", website=website), run["id"], "clothing", task["id"])


def end_upstream(store, rid):
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (rid,))


def release_gates(store):
    with store.connect() as con:
        con.execute("UPDATE social_platform_gates SET not_before=0")


def test_company_without_website_queues_social_once_and_legacy_stays_disabled(tmp_path):
    s, task, run = setup_market(tmp_path)
    company = ingest(s, task, run)
    ingest(s, task, run)
    flow = s.task_activity(task["id"])["workflow"]
    assert flow["social"]["run_id"]
    sr = s.social_run(flow["social"]["run_id"])
    assert len(sr["items"]) == 3
    assert sr["company_ids"] == [company["id"]]
    assert flow["stages"][-1]["total"] == 1
    legacy = s.create_market(dict(country="US", state="Fixture", city="Other",
        keywords=["clothing"], company_types=[], auto_enrich=False))
    assert legacy["auto_social"] is False
    assert s.task_activity(legacy["id"])["workflow"] is None


def test_normal_empty_results_complete_but_do_not_finalize_while_input_open(tmp_path):
    s, task, run = setup_market(tmp_path)
    ingest(s, task, run)
    for _ in range(3):
        item = s.claim_social_item()
        assert item
        s.finish_social_item(item["id"], {"status": "no_results", "message": "明确无结果"})
    flow = s.task_activity(task["id"])["workflow"]
    assert flow["stages"][-1]["succeeded"] == 1
    assert s.social_run(flow["social"]["run_id"])["status"] in {"running", "queued"}
    end_upstream(s, run["id"])
    s.finalize(run["id"])
    assert s.run(run["id"])["status"] == "completed"


def test_login_only_pauses_one_platform_and_pending_is_not_finished(tmp_path):
    s, task, run = setup_market(tmp_path)
    ingest(s, task, run, 1)
    ingest(s, task, run, 2)
    item = s.claim_social_item()
    platform = item["target"]
    s.finish_social_item(item["id"], {"status": "auth_required", "message": "需要重新登录"})
    flow = s.task_activity(task["id"])["workflow"]
    p = next(p for p in flow["social"]["platforms"] if p["platform"] == platform)
    assert p["state"] == "waiting_login"
    assert p["processed"] == 0
    assert p["manual_required"]
    assert s.claim_social_item()["target"] != platform
    s.resume_social_platform(platform)
    release_gates(s)
    assert s.claim_social_item()["target"] == platform


def test_pause_cancel_reject_late_results_and_do_not_affect_manual_run(tmp_path):
    s, task, run = setup_market(tmp_path)
    c = ingest(s, task, run)
    item = s.claim_social_item()
    s.control(run["id"], "pause")
    assert not s.finish_social_item(item["id"], {"status": "succeeded",
        "links": [{"url": "https://facebook.com/latefixture/"}]}, claim_token=item["claim_token"])
    assert not s.social_detail(c["id"])["accounts"]
    other = s.manual_company("Independent", "https://independent.example")
    manual = s.create_social_run([other["id"]], platform_search=True)
    assert s.claim_social_item()["run_id"] == manual["id"]
    s.control(run["id"], "cancel")
    assert s.social_run(manual["id"])["status"] == "running"


def test_website_links_append_profile_and_optional_posts_without_duplicate_reads(tmp_path):
    s, task, run = setup_market(tmp_path)
    c = ingest(s, task, run, website="https://fixture.example")
    payload = dict(status="completed", contacts=[], observations=[], warnings=[],
        social_links=["https://www.instagram.com/fixturebrand/"])
    s.apply_enrichment(c["id"], payload, run["id"])
    s.apply_enrichment(c["id"], payload, run["id"])
    srid = s.task_activity(task["id"])["workflow"]["social"]["run_id"]
    sr = s.social_run(srid)
    assert len([w for w in sr["items"] if w["stage"] == "profile"]) == 1
    assert not [w for w in sr["items"] if w["stage"] == "read"]
    assert sr["summary"]["total"] == 4


def test_backfill_creates_new_social_round_and_preserves_old_completed_run(tmp_path):
    s, task, run = setup_market(tmp_path)
    ingest(s, task, run)
    end_upstream(s, run["id"])
    with s.connect() as con:
        con.execute("UPDATE social_work SET state='no_results'")
    s.finalize(run["id"])
    original = s.run(run["id"])
    new = s.start_market_social(task["id"], ["facebook"], True)
    assert new["id"] != run["id"] and new["kind"] == "social"
    assert s.run(run["id"])["finished_at"] == original["finished_at"]
    flow = s.task_activity(task["id"])["workflow"]
    assert flow["social_only"]
    assert [stage["key"] for stage in flow["stages"]] == ["social"]
    assert flow["stages"][0]["total"] == 1
    assert s.start_market_social(task["id"], ["facebook"], True)["id"] == new["id"]


def test_round_snapshot_is_independent_of_future_settings_and_platform_subset(tmp_path):
    s, task, run = setup_market(tmp_path, social_platforms=["instagram"])
    s.set_task_social_settings(task["id"], dict(auto_social=False, social_platforms=["x"], social_read_posts=True))
    ingest(s, task, run)
    item=s.claim_social_item()
    assert item["target"]=="instagram"
    assert item["settings"]["market_options"]["social_read_posts"] is False
    assert s.task(task["id"])["auto_social"] is False


@pytest.mark.parametrize("posts", [False, True])
@pytest.mark.asyncio
async def test_selected_platform_search_profile_and_optional_posts_pipeline(tmp_path, posts):
    from leadworkbench.social_worker import process_social_item
    s, task, run=setup_market(tmp_path, social_platforms=["facebook"], social_read_posts=posts)
    company=ingest(s, task, run)
    end_upstream(s, run["id"])
    calls=[]
    async def search(platform, query, directory, settings):
        calls.append(("search",platform))
        return dict(status="succeeded", links=[dict(url="https://facebook.com/fixturebrand/", method="platform_search",
            source_url="https://facebook.com/search/pages", evidence="Fixture Company 1")])
    async def read(url, stage):
        calls.append((stage,url))
        return dict(status="preview_read",title="Fixture",description="Public profile",fields=[],
            posts=[dict(url="https://facebook.com/fixturebrand/posts/123",text="New collection")] if stage=="read" else [])
    for _ in range(4):
        release_gates(s)
        await process_social_item(s,platform_search=search,read=read)
    s.finalize(run["id"])
    assert [c[0] for c in calls]==["search","profile"]+(["read"] if posts else [])
    assert s.run(run["id"])["status"]=="completed"
    flow=s.task_activity(task["id"])["workflow"]
    assert flow["social"]["accounts_found"]==1
    assert flow["social"]["posts_found"]==int(posts)
    assert s.social_detail(company["id"])["accounts"][0]["status"]!="confirmed"


def test_partial_profile_preserved_during_platform_recovery(tmp_path):
    s, task, run=setup_market(tmp_path,social_platforms=["facebook"])
    c=ingest(s,task,run)
    item=s.claim_social_item()
    s.finish_social_item(item["id"],dict(status="succeeded",links=[dict(url="https://facebook.com/fixturebrand/")]))
    release_gates(s)
    item=s.claim_social_item()
    assert item["stage"]=="profile"
    s.finish_social_item(item["id"],dict(status="preview_read",partial=True,reason="network_error",
        title="Fixture profile",description="Retained public description",fields=[]))
    assert s.social_pages(c["id"])[0]["title"]=="Fixture profile"
    flow=s.task_activity(task["id"])["workflow"]
    assert flow["stages"][-1]["processed"]==0
    assert flow["social"]["platforms"][0]["state"]=="waiting_recovery"


def test_retry_cooldowns_are_bounded_and_survive_restart(tmp_path, monkeypatch):
    from leadworkbench import social_policy
    clock=[1000000.]
    monkeypatch.setattr(social_policy.time,"time",lambda:clock[0])
    s,task,run=setup_market(tmp_path,social_platforms=["x"])
    ingest(s,task,run)
    for attempt,seconds in enumerate([600,1800,7200,7200]):
        item=s.claim_social_item()
        assert item
        s.finish_social_item(item["id"],dict(status="failed",reason="network_error",message="timeout"))
        with s.connect() as con:gate=social_policy.gate(con,"x")
        assert gate["not_before"]==clock[0]+seconds
        with pytest.raises(ValueError,match="冷却"):s.resume_social_platform("x")
        assert s.claim_social_item() is None
        s=Store(tmp_path)
        clock[0]+=seconds+1
    assert s.claim_social_item() is None
    s.resume_social_platform("x")
    assert s.claim_social_item()


def test_old_claim_cannot_apply_after_pause_and_new_claim(tmp_path):
    s,task,run=setup_market(tmp_path,social_platforms=["x"])
    c=ingest(s,task,run)
    old=s.claim_social_item()
    s.control(run["id"],"pause")
    s.control(run["id"],"resume")
    new=s.claim_social_item()
    assert old["id"]==new["id"] and old["claim_token"]!=new["claim_token"]
    assert not s.finish_social_item(old["id"],dict(status="succeeded",links=[dict(url="https://x.com/late")]),claim_token=old["claim_token"])
    assert s.finish_social_item(new["id"],dict(status="no_results"),claim_token=new["claim_token"])
    assert not s.social_detail(c["id"])["accounts"]


@pytest.mark.asyncio
async def test_pause_cancels_inflight_source_and_releases_lane(tmp_path):
    from leadworkbench.social_worker import process_social_item
    s,task,run=setup_market(tmp_path,social_platforms=["x"])
    ingest(s,task,run)
    started=asyncio.Event()
    stopped=asyncio.Event()
    async def search(*args):
        started.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    job=asyncio.create_task(process_social_item(s,platform_search=search))
    await asyncio.wait_for(started.wait(),1)
    s.control(run["id"],"pause")
    await asyncio.wait_for(job,2)
    assert stopped.is_set()
    assert s.task_activity(task["id"])["workflow"]["social"]["platforms"][0]["state"]=="paused"


def test_market_backfill_can_exceed_manual_twenty_company_limit(tmp_path):
    s,task,run=setup_market(tmp_path,auto_social=False)
    for n in range(22):ingest(s,task,run,n)
    end_upstream(s,run["id"]);s.finalize(run["id"])
    backfill=s.start_market_social(task["id"],["instagram"])
    assert backfill["kind"]=="social"
    flow=s.task_activity(task["id"])["workflow"]
    assert flow["stages"][0]["total"]==22
    assert backfill["progress"]["total"]==22


def test_profile_cap_keeps_all_candidates_but_limits_reads(tmp_path):
    s,task,run=setup_market(tmp_path,social_platforms=["facebook"])
    c=ingest(s,task,run)
    item=s.claim_social_item()
    s.finish_social_item(item["id"],dict(status="succeeded",
        links=[dict(url=f"https://facebook.com/fixture{n}/") for n in range(12)]))
    flow=s.task_activity(task["id"])["workflow"]
    report=s.social_run(flow["social"]["run_id"])
    assert len([i for i in report["items"] if i["stage"]=="profile"])==8
    assert flow["social"]["accounts_found"]==12
    assert len(s.social_detail(c["id"])["accounts"])==12


def test_manual_search_never_reuses_market_owned_child(tmp_path):
    s,task,run=setup_market(tmp_path)
    c=ingest(s,task,run)
    linked=s.task_activity(task["id"])["workflow"]["social"]["run_id"]
    manual=s.create_social_run([c["id"]],platform_search=True)
    assert manual["id"]!=linked
    s.control(run["id"],"cancel")
    assert s.social_run(manual["id"])["status"]=="queued"


def test_social_only_round_keeps_last_discovery_market_statistics(tmp_path):
    s,task,run=setup_market(tmp_path,auto_social=False)
    ingest(s,task,run)
    end_upstream(s,run["id"]);s.finalize(run["id"])
    before=s.task(task["id"])
    s.start_market_social(task["id"],["facebook"])
    after=s.task(task["id"])
    assert after["market_stats"]==before["market_stats"]
    assert after["coverage"]==before["coverage"]
    assert after["latest_run"]["kind"]=="social"
    assert "discovery_total" not in after["latest_run"]["progress"]


def test_platform_resume_uses_corrected_connection_keeps_business_scope(tmp_path):
    from leadworkbench.social_connections import save_connections
    s,task,run=setup_market(tmp_path,social_platforms=["facebook"],social_read_posts=False)
    ingest(s,task,run)
    item=s.claim_social_item()
    s.finish_social_item(item["id"],dict(status="auth_required"))
    save_connections(tmp_path,dict(proxy_url="http://127.0.0.1:19090"))
    s.resume_social_platform("facebook")
    retried=s.claim_social_item()
    assert retried["settings"]["connections"]["proxy_url"]=="http://127.0.0.1:19090"
    assert retried["settings"]["market_options"]==dict(auto_social=True,social_platforms=["facebook"],social_read_posts=False)


def test_excluded_account_during_read_rejects_late_posts(tmp_path):
    s,task,run=setup_market(tmp_path,social_platforms=["facebook"])
    c=ingest(s,task,run)
    item=s.claim_social_item()
    s.finish_social_item(item["id"],dict(status="succeeded",links=[dict(url="https://facebook.com/fixturebrand/")]))
    release_gates(s)
    item=s.claim_social_item()
    account=s.social_detail(c["id"])["accounts"][0]
    s.decide_social(c["id"],account["id"],"excluded","unknown")
    assert not s.social_item_active(item)
    assert not s.finish_social_item(item["id"],dict(status="preview_read",title="Ignored",posts=[
        dict(url="https://facebook.com/fixturebrand/posts/123",text="Late post")]),claim_token=item["claim_token"])
    with s.connect() as con:
        assert con.execute("SELECT count(*) FROM social_page_observations").fetchone()[0]==0
        assert con.execute("SELECT count(*) FROM social_entities WHERE kind='post'").fetchone()[0]==0
        assert con.execute("SELECT state FROM social_work WHERE id=?",(item["id"],)).fetchone()[0]=="cancelled"
