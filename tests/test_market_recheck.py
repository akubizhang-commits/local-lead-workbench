"""P1-19: batch website re-check of a market (design §6.3, §6.6); no network."""
from fastapi.testclient import TestClient
import pytest

from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.worker import process_run_item


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        client.store = Store(tmp_path)
        yield client


def market(store, n=3, city="San Jose"):
    task = store.create_market(dict(country="US", state="California", city=city, keywords=["metal supplier"],
                                    company_types=["wholesale"], auto_enrich=False))
    run = store.start_task(task["id"])
    ids = []
    for i in range(n):
        ids.append(store.ingest_market({
            "source_key": f"maps:{city}:{i}", "source_url": f"https://maps.example/{city}/{i}", "name": f"{city} {i:02d}",
            "address": f"{i} Main St, San Jose, CA 95112", "phone": f"1408555{i:04d}",
            "website": f"https://site{i}.example",
            "observed_at": f"2026-10-01T00:{i:02d}:00+00:00"}, run["id"], "q", task["id"])["id"])
    with store.connect() as con:
        con.execute("UPDATE work_items SET state='succeeded' WHERE run_id=?", (run["id"],))
    store.finalize(run["id"])
    return task, ids


def recheck(client, task_id, **body):
    return client.post(f"/api/tasks/{task_id}/website-recheck", json=body)


def status(store, run_id):
    return store.run(run_id)["status"]


def test_input_validation(client):
    task, ids = market(client.store)
    assert recheck(client, task["id"], company_ids=[f"c{i}" for i in range(21)]).status_code == 422
    too_many = recheck(client, task["id"], company_ids=[f"c{i}" for i in range(21)]).json()["detail"]
    assert "一次最多复核 20 家企业" in str(too_many)
    assert recheck(client, task["id"], company_ids=ids * 10).status_code == 202  # counted after de-duplication
    assert recheck(client, task["id"], select="everything").status_code == 422
    assert recheck(client, task["id"], select="never_attempted", company_ids=ids).status_code == 422
    assert recheck(client, task["id"]).status_code == 422
    assert recheck(client, "nope", select="never_attempted").status_code == 404


def test_ineligible_companies_are_skipped_with_reasons(client):
    store = client.store
    task, ids = market(store, 4)
    _, elsewhere = market(store, 1, city="Campbell")
    with store.connect() as con:
        con.execute("UPDATE companies SET website='' WHERE id=?", (ids[3],))
    outsider = store.manual_company("Outsider", "https://outsider.example")["id"]
    refusing = store.company(ids[1])
    store.update_company(ids[1], refusing["revision"], {}, status="do_not_contact")
    queued = store.refresh(ids[2])
    response = recheck(client, task["id"], company_ids=[ids[0], ids[1], ids[2], ids[3], outsider, elsewhere[0]])
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] == 1
    assert {(s["company_id"], s["reason"]) for s in body["skipped"]} == {
        (ids[1], "已标记拒绝联系"), (ids[2], "已在复核队列中"), (ids[3], "无官网"), (outsider, "不属于此市场"),
        (elsewhere[0], "不属于此市场")}
    assert status(store, queued["id"]) == "queued"


def test_nothing_to_recheck_and_one_batch_at_a_time(client):
    store = client.store
    task, ids = market(store, 3)
    nobody = recheck(client, task["id"], company_ids=["missing"])
    assert nobody.status_code == 409 and nobody.json()["code"] == "nothing_to_recheck"
    assert nobody.json()["detail"] == "所选企业都不需要复核：不属于此市场 1 家。"
    first = recheck(client, task["id"], company_ids=ids[:1]).json()
    second = recheck(client, task["id"], company_ids=ids[1:])
    assert second.status_code == 409 and second.json()["code"] == "recheck_running"
    assert second.json()["detail"] == "该市场已有批量官网复核在进行中（已完成 0 / 1 家），请等待完成或取消后再发起。"
    progress = client.get(f"/api/tasks/{task['id']}/website-recheck").json()
    assert (progress["run_id"], progress["status"], progress["total"], progress["done"]) == (first["run_id"], "queued", 1, 0)


def test_never_attempted_selection_skips_failed_and_partial_sites(client):
    store = client.store
    task, ids = market(store, 22)
    for company_id, state in ((ids[0], "failed"), (ids[1], "partial")):
        run = store.refresh(company_id)
        with store.connect() as con:
            con.execute("UPDATE work_items SET state=? WHERE run_id=?", (state, run["id"]))
            con.execute("UPDATE runs SET status='partial' WHERE id=?", (run["id"],))
    body = recheck(client, task["id"], select="never_attempted").json()
    assert body["queued"] == 20 and body["skipped"] == []
    assert store.run(body["run_id"])["snapshot"]["company_ids"] == ids[2:22]
    with store.connect() as con:
        con.execute("UPDATE runs SET status='cancelled' WHERE id=?", (body["run_id"],))
        con.execute("UPDATE work_items SET state='cancelled' WHERE run_id=?", (body["run_id"],))
    again = recheck(client, task["id"], select="never_attempted").json()
    assert store.run(again["run_id"])["snapshot"]["company_ids"] == ids[2:22]  # cancelled is not an attempt


def test_archived_market_blocks_resuming_its_batch(client):
    store = client.store
    task, ids = market(store, 2)
    batch = recheck(client, task["id"], company_ids=ids).json()["run_id"]
    with store.connect() as con:
        con.execute("UPDATE runs SET status='blocked' WHERE id=?", (batch,))
    assert client.post(f"/api/tasks/{task['id']}/archive").status_code == 200
    blocked = client.post(f"/api/runs/{batch}/resume")
    assert blocked.status_code == 409 and blocked.json()["code"] == "market_archived"
    assert recheck(client, task["id"], company_ids=ids).json()["code"] == "market_archived"
    client.post(f"/api/tasks/{task['id']}/restore")
    assert client.post(f"/api/runs/{batch}/resume").status_code == 200


def test_an_old_blocked_batch_waits_for_the_new_one(client):
    store = client.store
    task, ids = market(store, 2)
    old = recheck(client, task["id"], company_ids=ids[:1]).json()["run_id"]
    with store.connect() as con:
        con.execute("UPDATE runs SET status='blocked' WHERE id=?", (old,))
    new = recheck(client, task["id"], company_ids=ids[1:])
    assert new.status_code == 202
    response = client.post(f"/api/runs/{old}/resume")
    assert response.status_code == 409 and response.json()["code"] == "recheck_running"
    assert status(store, old) == "blocked"
    client.post(f"/api/runs/{new.json()['run_id']}/cancel")
    assert client.post(f"/api/runs/{old}/resume").status_code == 200


def test_single_refresh_reuses_any_unfinished_queue(client):
    store = client.store
    task, ids = market(store, 2)
    round_ = store.start_task(task["id"])
    store.add_enrichment(round_["id"], ids[0])
    with store.connect() as con:
        runs = con.execute("SELECT count(*) FROM runs").fetchone()[0]
    response = client.post(f"/api/companies/{ids[0]}/refresh").json()
    assert response["id"] == round_["id"] and response["already_queued"] is True
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == runs
    batch = recheck(client, task["id"], company_ids=[ids[1]]).json()["run_id"]
    again = client.post(f"/api/companies/{ids[1]}/refresh").json()
    assert again["id"] == batch and again["already_queued"] is True


@pytest.mark.asyncio
async def test_batch_runs_to_completion_with_a_local_enrich_double(tmp_path):
    store = Store(tmp_path)
    task, ids = market(store, 2)
    seen = []
    async def enrich(url, **_):
        seen.append(url)
        return dict(status="completed", warnings=[], observations=[], contacts=[], social_links=[], has_more=False,
                    summary={"pages_checked": 3, "pages_skipped": 0, "contacts_found": 0, "remaining": 0})
    batch = store.create_website_recheck(task["id"], company_ids=ids)
    run_id = batch["run_id"]
    await process_run_item(store, run_id, enrich=enrich)
    progress = store.website_recheck(task["id"])
    assert (progress["done"], progress["total"]) == (1, 2)
    store.control(run_id, "pause")
    assert store.website_recheck(task["id"])["status"] == "paused"
    store.control(run_id, "resume")
    await process_run_item(store, run_id, enrich=enrich)
    await process_run_item(store, run_id, enrich=enrich)
    store.finalize(run_id)
    final = store.website_recheck(task["id"])
    assert (final["status"], final["done"], final["total"], final["pages_checked"]) == ("completed", 2, 2, 6)
    assert sorted(seen) == ["https://site0.example", "https://site1.example"]
    assert store.run(run_id)["message"].startswith("本次官网扫描读取 6 页")
    other = store.create_website_recheck(task["id"], company_ids=ids[:1])
    store.control(other["run_id"], "cancel")
    assert store.website_recheck(task["id"])["status"] == "cancelled"
