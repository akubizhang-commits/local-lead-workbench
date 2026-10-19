"""Execute one durable planning, discovery or website work item.

The worker supplies source dependencies at call time so its public batch API
and monkeypatched source boundaries remain compatible after extraction.
"""
import asyncio
import logging
import json
import time

from .db import encode
from .logging_setup import ITEM_ID, RUN_ID, STAGE

log = logging.getLogger(__name__)


async def execute_run_item(store, run_id, *, discover=None, enrich, planner=None, stages=None):
    """Execute at most one durable work item; return whether one was claimed."""
    run = store.run(run_id)
    if run["status"] not in {"queued", "running"}:
        return False
    pace_maps = discover is None
    item = store.claim_item(run_id, pace_maps=pace_maps, stages=stages)
    if not item:
        store.progress(run_id)
        store.finalize(run_id)
        return False
    # Every log line produced while this item runs carries its ids (§6.2).
    tokens = [(var, var.set(value)) for var, value in
              ((RUN_ID, run_id), (ITEM_ID, item["id"]), (STAGE, item["stage"]))]
    started = time.perf_counter()
    log.info("work item claimed", extra={"event": "work_item_claimed"})
    try:
        return await _execute_claimed(store, run, run_id, item, pace_maps=pace_maps, discover=discover,
                                      enrich=enrich, planner=planner)
    finally:
        _log_finished(store, item["id"], started)
        for var, token in reversed(tokens):
            var.reset(token)


def _log_finished(store, item_id, started):
    fields = {"event": "work_item_finished", "duration_ms": round((time.perf_counter() - started) * 1000)}
    try:
        with store.connect() as con:
            row = con.execute("SELECT state, result FROM work_items WHERE id=?", (item_id,)).fetchone()
        if row:
            result = json.loads(row["result"] or "{}")
            fields.update(state=row["state"], stop_reason=result.get("stop_reason"),
                          error_type=result.get("error_type"))
            if isinstance(result.get("records"), list):
                fields["records"] = len(result["records"])
    except Exception:  # logging must never change the outcome of an item
        pass
    log.info("work item finished", extra=fields)


async def _execute_claimed(store, run, run_id, item, *, pace_maps, discover, enrich, planner):
    previous_result = json.loads(item.get("result") or "{}")
    applied = {}
    access = None
    source_result = None
    skipped = None

    def observe_source(result):
        # Persist CAPTCHA/rate restrictions before any record ingestion can fail.
        nonlocal access, source_result
        if not pace_maps or item['stage'] != 'discovery':
            return
        source_result = result
        from .maps_policy import observe
        with store.connect() as con:
            access = observe(con, result,
                query_failures=previous_result.get('maps',{}).get('transient_failures',0)+1)

    try:
        market_mode = run["kind"] == "discovery" and run["snapshot"].get("limit") == 0
        if item["stage"] == "planning":
            if planner is None:
                from .market_plan import plan_market
                planner = plan_market
            snapshot = {**run["snapshot"], "_relocate": True} if previous_result.get("relocate") else run["snapshot"]
            plan = await planner(snapshot, store.directory)
            if store.run(run_id)["status"] not in {"queued", "running"}:
                # Keep the relocate marker so "继续" re-plans the same way.
                store.finish_item(item["id"], "pending", {**previous_result, "message": "市场规划已暂停"})
                return True
            store.save_market_plan(run_id, plan)
            result = {"status": plan.get("status", "completed"), "warnings": plan.get("warnings", []),
                      "mode": plan.get("mode")}
        elif item["stage"] == "discovery" and market_mode:
            from .market_worker import discover_market_item, legacy_query_skip
            # Resumed rounds planned before keyword-only planning may hold
            # unrelated type queries: finish them without touching Maps.
            skipped = legacy_query_skip(run, item)
            if skipped:
                result = skipped
            else:
                if discover is None:
                    from .sources.maps import discover_maps
                    discover = discover_maps
                result = await discover_market_item(store, run, item, discover,on_result=observe_source)
        elif item["stage"] == "discovery":
            if discover is None:
                from .sources.maps import discover_maps
                discover = discover_maps
            config = run["snapshot"]
            progress = store.progress(run_id)
            remaining = max(0, config["limit"] - progress["discovered"])
            if remaining == 0:
                result = {"status": "completed", "records": [], "warnings": [], "stop_reason": "company_limit"}
            else:
                result = await discover(
                    item["target"], store.directory / "runs" / run_id / item["id"],
                    limit=config["limit"], timeout_seconds=180, data_dir=store.directory,
                    should_stop=lambda: store.run(run_id)["status"] not in {"queued", "running"})
            observe_source(result)
            seen = store.run_source_keys(run_id)
            for record in result.get("records", []):
                if record["source_key"] not in seen and len(seen) >= config["limit"]:
                    continue
                company = store.ingest(record, run_id, item["target"], city=config["city"], country=config["country"])
                seen.add(record["source_key"])
                if config["auto_enrich"] and company["website"] and company["status"] != "do_not_contact":
                    store.add_enrichment(run_id, company["id"])
        else:
            company = store.company(item["target"])
            if company["status"] == "do_not_contact":
                result = {"status": "completed", "warnings": ["已拒绝联系，跳过复核"]}
            else:
                # Cancellation is cooperative at fetch boundaries. Cancel the
                # coroutine if a user pauses while a page is loading.
                from .sources.website import WEBSITE_BATCH_SIZE
                options = {"max_pages": WEBSITE_BATCH_SIZE}
                from .sources.website import enrich_website
                if enrich is enrich_website:
                    from .website_policy import WebsiteAccessPolicy
                    options["access_policy"] = WebsiteAccessPolicy(store)
                if previous_result.get("checkpoint"):
                    options["checkpoint"] = previous_result["checkpoint"]
                job = asyncio.create_task(enrich(company["website"], **options))
                try:
                    while not job.done():
                        await asyncio.wait({job}, timeout=0.5)
                        if store.run(run_id)["status"] not in {"queued", "running"}:
                            job.cancel()
                            await asyncio.gather(job, return_exceptions=True)
                            store.finish_item(item["id"], "pending", {**previous_result, "message": "用户暂停或取消"})
                            return True
                    result = job.result()
                finally:
                    if not job.done():
                        job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                applied = store.apply_enrichment(company["id"], result, run_id) or {}
        if pace_maps and item['stage'] == 'discovery' and not skipped:
            if access is None:
                observe_source(result)
            if access['manual_required']:
                result = {**result, 'status': 'blocked',
                          'warnings': [*result.get('warnings', []), access['message']]}
        warnings = result.get("warnings", [])
        message = "；".join(str(w) for w in warnings) or str(result.get("stop_reason", ""))
        state = {"completed": "succeeded", "partial": "partial", "blocked": "blocked"}.get(result.get("status"), "failed")
        item_result = {"message": message, "status": result.get("status")}
        retry_maps = False
        if access:
            from .maps_retry import outcome
            item_result["maps"], retry_maps = outcome(previous_result, result, access)
        if item["stage"] == "planning":
            item_result["mode"] = result.get("mode")
        if "checkpoint" in result:
            item_result["checkpoint"] = result["checkpoint"]
            item_result["has_more"] = result.get("has_more", False)
        for key in ("retryable", "stop_reason", "website_access"):
            if key in result:
                item_result[key] = result[key]
        if store.run(run_id)["status"] in {"paused", "cancelled"}:
            state = "partial" if result.get("retryable") is False else "pending"
            store.finish_item(item["id"], state, item_result)
            return True
        if item["stage"] == "refresh" and "contacts" in result:
            item_result["summary"] = dict(result.get("summary") or {
                "pages_checked": result.get("pages_checked", 0),
                "pages_skipped": result.get("pages_skipped", 0),
                "contacts_found": len(result["contacts"]),
            })
            # New deduplicated contact entries across all batches of this item.
            item_result["summary"]["entries_added"] = ((previous_result.get("summary") or {}).get("entries_added", 0)
                                                       + applied.get("entries_added", 0))
        if item["stage"] == "refresh" and result.get("has_more"):
            state = "pending"
            summary = item_result["summary"]
            item_result["message"] = (f"官网分批扫描：已读取 {summary['pages_checked']} 页，"
                                      f"待扫描 {summary['remaining']} 个链接；后台将自动继续")
            with store.connect() as con:
                con.execute("UPDATE runs SET message=? WHERE id=?", (item_result["message"], run_id))
        if retry_maps:
            state = 'pending'
            item_result['message'] = message + '；已保留本次结果，将按计划自动重试'
            with store.connect() as con:
                con.execute('UPDATE runs SET message=? WHERE id=?', (item_result['message'], run_id))
        store.finish_item(item["id"], state, item_result)
        from .market_worker import source_wide_block
        if not pace_maps and item["stage"] == "discovery" and state == "blocked" and (not market_mode or source_wide_block(result)):
            # Legacy injected/batch adapters lack the global pacing gate.
            # The production source keeps untouched queries pending behind that gate.
            with store.connect() as con:
                con.execute("""UPDATE work_items SET state='blocked',result=?
                    WHERE run_id=? AND stage='discovery' AND state='pending'""",
                    (encode({"message": message, "status": "blocked"}), run_id))
    except asyncio.CancelledError:
        if pace_maps and item['stage'] == 'discovery':
            from .maps_policy import observe
            with store.connect() as con:
                observe(con, {'status': 'partial', 'stop_reason': 'cancelled'})
        store.finish_item(item["id"], "pending", {**previous_result, "message": "worker 停止，等待恢复"})
        raise
    except Exception as exc:
        log.exception("Run item failed: %s", item["id"], extra={"event": "work_item_failed"})
        error = (str(exc).strip() or type(exc).__name__)[:1200]
        if pace_maps and item['stage'] == 'discovery':
            from .maps_policy import observe
            from .maps_retry import outcome
            if access and access['manual_required'] and source_result:
                failure = {**source_result,'warnings':[*source_result.get('warnings',[]),error]}
            else:
                failure = {'status':'failed','stop_reason':'timeout' if isinstance(exc,TimeoutError) else 'adapter_error',
                           'warnings':[error]}
                with store.connect() as con:
                    access = observe(con, failure,
                        query_failures=previous_result.get('maps',{}).get('transient_failures',0)+1)
            meta, retry = outcome(previous_result, failure, access)
            store.finish_item(item["id"], 'pending' if retry else 'blocked' if access['manual_required'] else 'failed',
                              {**previous_result,'maps':meta,'status':failure['status'],
                               'stop_reason':failure['stop_reason'],'message':error,'error_type':type(exc).__name__})
        else:
            store.finish_item(item["id"], "failed", {**previous_result, "message": error, "error_type": type(exc).__name__})
    store.progress(run_id)
    store.finalize(run_id)
    return True
