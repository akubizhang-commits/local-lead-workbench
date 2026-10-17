import json

import pytest

from leadworkbench.db import Store


def market(tmp_path):
    store = Store(tmp_path)
    task = store.create_market(dict(country="US", state="CA", city="LA",
                                   keywords=["clothing"], company_types=["wholesale"], auto_enrich=True))
    run = store.start_task(task["id"])
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (run["id"],))
    return store, task, run


def add_result(store, run, name, state, payload):
    company = store.manual_company(name, "https://example.org")
    store.add_enrichment(run["id"], company["id"])
    with store.connect() as con:
        con.execute("UPDATE work_items SET state=?,result=? WHERE run_id=? AND target=?",
                    (state, json.dumps(payload), run["id"], company["id"]))
    return company


def projection(store, task, **kwargs):
    flow = store.task_activity(task["id"], **kwargs)["workflow"]
    return flow, next(s for s in flow["stages"] if s["key"] == "refresh")["website_summary"]


def test_summary_counts_companies_not_failed_pages_and_keeps_partial_results(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Readable", "succeeded", {"summary": {"pages_checked": 4}})
    add_result(store, run, "Partly readable", "partial", {
        "summary": {"pages_checked": 3, "failed": 2},
        "checkpoint": {"logs": [{"url": f"https://example.org/{n}", "status": "failed",
                                "reason": "HTTP 404"} for n in range(2)]}})
    add_result(store, run, "Denied", "blocked", {
        "summary": {"pages_checked": 0}, "message": "https://example.org/: HTTPError: HTTP 403"})
    add_result(store, run, "Timed out", "failed", {"message": "TimeoutError"})
    flow, summary = projection(store, task, page=2, status="failed")
    assert (summary["total"], summary["processed"], summary["with_pages"], summary["without_pages"]) == (4, 4, 2, 2)
    groups = {r["code"]: r for r in summary["reasons"]}
    assert groups["not_found"]["companies"] == groups["not_found"]["with_pages"] == 1
    assert groups["access_denied"]["without_pages"] == groups["timeout"]["without_pages"] == 1
    assert sum(r["companies"] for r in groups.values()) == 3
    assert "example.org" not in json.dumps(summary)
    assert flow["website_pages"] == 7


def test_zero_pages_budget_and_document_are_not_reported_as_whole_site_denial(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Budget", "partial", {
        "summary": {"pages_checked": 0, "remaining": 2},
        "message": "Website batch made no progress before its time budget; manual retry required"})
    add_result(store, run, "Document", "blocked", {
        "summary": {"pages_checked": 0, "files_unparsed": 1},
        "message": "Website entry is a document; no HTML pages parsed"})
    add_result(store, run, "Parsed with PDF", "succeeded", {
        "summary": {"pages_checked": 1, "files_unparsed": 2, "pages_skipped": 5},
        "checkpoint": {"logs": [{"status": "excluded", "reason": "HTTP 404 irrelevant product"}]}})
    _, summary = projection(store, task)
    groups = {r["code"]: r["companies"] for r in summary["reasons"]}
    assert groups == {"scan_limit": 1, "unsupported_content": 1}
    assert summary["files_unparsed_companies"] == 2
    assert (summary["with_pages"], summary["without_pages"]) == (1, 2)


def test_pending_with_saved_pages_is_not_counted_as_ended_and_other_round_is_excluded(tmp_path):
    store, task, run = market(tmp_path)
    company = add_result(store, run, "Pending", "pending", {
        "summary": {"pages_checked": 2}, "message": "暂时失败 HTTP 403"})
    add_result(store, run, "Running", "running", {"summary": {"pages_checked": 1}})
    store.refresh(company["id"])
    _, summary = projection(store, task)
    assert (summary["total"], summary["processed"], summary["pending"], summary["running"]) == (2, 0, 1, 1)
    assert summary["with_pages"] == summary["without_pages"] == 0
    assert summary["reasons"] == []


def test_recovered_old_page_failure_does_not_override_current_reason(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Recovered page", "partial", {
        "summary": {"pages_checked": 2},
        "message": "Old HTTP 403; Link depth limit reached",
        "checkpoint": {"warnings": ["Link depth limit reached"],
                       "logs": [{"url": "https://example.org", "status": "failed", "reason": "HTTP 403"},
                                {"url": "https://example.org", "status": "parsed"}]}})
    _, summary = projection(store, task)
    assert [r["code"] for r in summary["reasons"]] == ["scan_limit"]


@pytest.mark.parametrize("payload,code", [
    ({"message": "robots disallows https://example.org"}, "robots"),
    ({"website_access": {"http_status": 429, "reason": "HTTP 429"}}, "rate_limited"),
    ({"message": "ClientConnectorCertificateError: SSL certificate verify failed"}, "certificate"),
    ({"message": "Cross-site redirect: https://elsewhere.example"}, "redirect"),
    ({"message": "Unsupported content type: application/pdf"}, "unsupported_content"),
    ({"message": "HTML body byte limit reached"}, "content_limit"),
    ({"message": "Page requires a security challenge"}, "access_denied"),
    ({}, "other"),
])
def test_reason_categories_are_conservative(tmp_path, payload, code):
    store, task, run = market(tmp_path)
    add_result(store, run, "Result", "blocked", payload)
    _, summary = projection(store, task)
    assert summary["reasons"][0]["code"] == code
    assert summary["reasons"][0]["without_pages"] == 1


def test_terminal_partial_banner_does_not_claim_user_must_complete_every_website(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Some pages", "partial", {"summary": {"pages_checked": 2}})
    with store.connect() as con:
        con.execute("UPDATE runs SET status='partial' WHERE id=?", (run["id"],))
    flow = store.task_activity(task["id"])["workflow"]
    assert "本轮已结束" in flow["current"]
    assert "已取得的结果已保存" in flow["current"]
    assert "请处理未完成" not in flow["current"]


def test_stale_terminal_label_does_not_hide_a_pending_website(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Waiting", "pending", {"summary": {"pages_checked": 2}})
    with store.connect() as con:
        con.execute("UPDATE runs SET status='partial' WHERE id=?", (run["id"],))
    flow = store.task_activity(task["id"])["workflow"]
    assert "本轮已结束" not in flow["current"]
    assert "待处理" in flow["current"]


def test_old_unstructured_message_is_not_used_after_structured_recovery(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Recovered", "partial", {
        "message": "Old HTTP 403", "summary": {"pages_checked": 1},
        "checkpoint": {"logs": [{"url": "https://example.org", "status": "failed", "reason": "HTTP 403"},
                                {"url": "https://example.org", "status": "parsed"}]}})
    _, summary = projection(store, task)
    assert summary["reasons"][0]["code"] == "other"


@pytest.mark.parametrize("extra", [
    {"failure_warnings": {"https://example.org": "https://example.org: HTTP 403"}},
    {"warnings": ["https://example.org: HTTP 403"]},
    {"warnings": ["HTTP 403"]},
    {"warnings": ["HTTP 403", "Link depth limit reached"]},
    {"warnings": ["Link depth limit reached", "HTTP 403"]},
])
def test_recovered_page_is_removed_from_legacy_warning_sources(tmp_path, extra):
    store, task, run = market(tmp_path)
    add_result(store, run, "Recovered", "partial", {
        "summary": {"pages_checked": 1},
        "checkpoint": {**extra, "logs": [
            {"url": "https://example.org", "status": "failed", "reason": "HTTP 403"},
            {"url": "https://example.org", "status": "parsed"}]}})
    _, summary = projection(store, task)
    assert summary["reasons"][0]["code"] == ("scan_limit" if "Link depth limit reached" in extra.get("warnings", []) else "other")


def test_current_gate_beats_old_warnings_and_summary_fields_fall_back_individually(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "Current limit", "partial", {
        "summary": {"contacts_found": 0}, "website_access": {"http_status": 429, "reason": "HTTP 429"},
        "checkpoint": {"summary": {"pages_checked": 5, "files_unparsed": 1},
                       "warnings": ["robots disallows https://example.org/old"]}})
    _, summary = projection(store, task)
    assert summary["with_pages"] == summary["files_unparsed_companies"] == 1
    assert summary["reasons"][0]["code"] == "rate_limited"


def test_dns_message_with_ssl_default_does_not_claim_certificate_failure(tmp_path):
    store, task, run = market(tmp_path)
    add_result(store, run, "DNS", "blocked", {
        "message": "Cannot connect to host example.org:443 ssl:default [nodename nor servname provided]"})
    _, summary = projection(store, task)
    assert summary["reasons"][0]["code"] == "network"
