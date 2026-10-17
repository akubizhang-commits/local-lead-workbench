"""Execute one durable market discovery unit; company count is never the goal."""
import json

# Source-level resource budgets, not user-facing market size limits.
BATCH_RECORDS = 120
MAX_DISCOVERY_JOBS = 4096


async def discover_market_item(store, run, item, discover, *, on_result=None):
    from .market_plan import split_job, exhaustion_reason
    prior = store.market_item_exhausted(run["id"], item["id"])
    if prior:
        return dict(status="partial", records=[], retryable=False,
                    stop_reason="capability_exhausted", warnings=[prior])
    job = json.loads(item["target"])
    result = await discover(
        job["query"], store.directory / "runs" / run["id"] / item["id"],
        limit=BATCH_RECORDS, timeout_seconds=180, geo=job.get("geo"),
        zoom=job.get("zoom", 15), depth=job.get("depth", 10),
        should_stop=lambda: store.run(run["id"])["status"] not in {"queued", "running"})
    if on_result:
        on_result(result)
    config = run["snapshot"]
    for record in result.get("records", []):
        company = store.ingest_market(record, run["id"], job["query"], run["task_id"])
        if config.get("auto_enrich") and company.get("website") and company["status"] != "do_not_contact":
            # The store prioritizes discovery work over refresh work. A company
            # encountered in several tiles still gets one website job per round.
            store.add_enrichment(run["id"], company["id"])
    saturated = result.get("saturated") or result.get("stop_reason") == "company_limit"
    if saturated and result.get("status") != "blocked":
        children = split_job(job)
        if children and store.count_market_jobs(run["id"]) + len(children) <= MAX_DISCOVERY_JOBS:
            store.append_market_jobs(run["id"], children)
            result = {**result, "status": "completed",
                      "warnings": [], "stop_reason": "已拆分为更小区域继续检索"}
        else:
            reason = exhaustion_reason(job, job_budget=bool(children))
            store.record_market_exhausted(run["id"], item["id"], reason)
            result = {**result, "status": "partial",
                      "retryable": False, "stop_reason": "capability_exhausted",
                      "warnings": [*result.get("warnings", []), reason]}
    return result


def legacy_query_skip(run, item):
    """Result for an old-plan query unrelated to the round's keywords, else None."""
    from .market_plan import TYPE_QUERIES, keyword_terms
    terms = keyword_terms(run["snapshot"])
    query = " ".join(json.loads(item["target"])["query"].split()).casefold()
    # Old plans were keywords ∪ type defaults; only the type-default queries
    # (root or split `<term> in <location>`) are unrelated to the keywords.
    defaults = {term for group in TYPE_QUERIES.values() for term in group}
    if (terms and any(query.startswith(term + " in ") for term in defaults)
            and not any(query.startswith(term + " in ") for term in terms)):
        return dict(status="completed", records=[], warnings=[], retryable=False,
                    stop_reason="skipped_legacy_query")
    return None
def source_wide_block(result):
    """An empty tile is not a reason to abort unrelated geographic searches."""
    if result.get("status") != "blocked":
        return False
    if result.get("stop_reason") in {"missing_binary", "launch_failed", "captcha", "rate_limited", "access_denied"}:
        return True
    warnings = " ".join(str(w) for w in result.get("warnings", [])).lower()
    return any(term in warnings for term in ("captcha", "验证码", "rate limit", "429", "access denied"))
