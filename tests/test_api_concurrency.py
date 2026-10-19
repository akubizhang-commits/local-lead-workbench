"""P1-1: no global API lock; restore-only maintenance gate (design §7.5)."""
import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from leadworkbench import api as api_module
from leadworkbench import maintenance, worker
from leadworkbench.api import create_app
from leadworkbench.db import Store


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        http.headers["X-Local-Token"] = (await http.get("/api/session")).json()["token"]
        yield http, app


async def timed(http, path, method="GET", **kwargs):
    start = time.monotonic()
    response = await http.request(method, path, **kwargs)
    return response, time.monotonic() - start


def p95(values):
    ordered = sorted(values)
    return ordered[max(0, round(0.95 * len(ordered)) - 1)]


async def test_1_fast_requests_do_not_wait_for_a_slow_one(client, monkeypatch):
    http, app = client
    monkeypatch.setattr(app.state.store, "company_page", lambda **kw: time.sleep(2) or {"items": []})
    slow = asyncio.create_task(http.get("/api/company-page"))
    await asyncio.sleep(0.2)
    (settings, t1), (overview, t2) = await asyncio.gather(timed(http, "/api/settings"), timed(http, "/api/overview"))
    assert settings.status_code == overview.status_code == 200
    assert t1 < 0.2 and t2 < 0.2, (t1, t2)
    assert not slow.done()
    assert (await slow).status_code == 200


async def test_1b_cpu_bound_request_keeps_p95_low(client, monkeypatch):
    http, app = client
    def busy(**kwargs):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            sum(range(1000))
        return {"items": []}
    monkeypatch.setattr(app.state.store, "company_page", busy)
    slow = asyncio.create_task(http.get("/api/company-page"))
    await asyncio.sleep(0.1)
    latencies = []
    while not slow.done() and len(latencies) < 18:
        response, elapsed = await timed(http, "/api/settings")
        assert response.status_code == 200
        latencies.append(elapsed)
        await asyncio.sleep(0.1)
    await slow
    assert len(latencies) >= 10
    assert p95(latencies) < 0.2, latencies


async def test_2_probe_network_wait_blocks_nothing(client, monkeypatch):
    http, app = client
    from leadworkbench.sources import social_discovery
    async def slow_search(url, query):
        await asyncio.sleep(3)
        return {"status": "ok", "message": "fixture"}
    monkeypatch.setattr(social_discovery, "search_searx", slow_search)
    monkeypatch.setattr(app.state.store, "restore", lambda name: None)
    probe = asyncio.create_task(http.post("/api/social/probe"))
    await asyncio.sleep(0.2)
    settings, elapsed = await timed(http, "/api/settings")
    assert settings.status_code == 200 and elapsed < 0.1, elapsed
    restore, elapsed = await timed(http, "/api/backups/restore", "POST", json={"name": "backup-x.sqlite3"})
    assert restore.status_code == 200 and elapsed < 1, (restore.text, elapsed)
    assert not probe.done()
    assert (await probe).json()["status"] == "ok"


async def test_2_probe_settings_read_waits_for_restore(client, monkeypatch):
    http, app = client
    from leadworkbench.sources import social_discovery
    stamps = {}
    async def quick_search(url, query):
        return {"status": "ok", "message": "fixture"}
    def slow_restore(name):
        time.sleep(1)
        stamps["restore_end"] = time.monotonic()
    real_settings = app.state.store.social_settings
    def settings():
        stamps["settings"] = time.monotonic()
        return real_settings()
    monkeypatch.setattr(social_discovery, "search_searx", quick_search)
    monkeypatch.setattr(app.state.store, "restore", slow_restore)
    monkeypatch.setattr(app.state.store, "social_settings", settings)
    restore = asyncio.create_task(http.post("/api/backups/restore", json={"name": "backup-x.sqlite3"}))
    await asyncio.sleep(0.3)
    assert (await http.post("/api/social/probe")).status_code == 200
    assert (await restore).status_code == 200
    assert stamps["settings"] >= stamps["restore_end"]


async def test_2b_browser_open_spawns_once(client, monkeypatch):
    http, app = client
    import importlib.util
    from leadworkbench.sources import social_browser
    calls = []
    class FakeProcess:
        def __init__(self, *args, **kwargs):
            time.sleep(0.2)
            calls.append(args)
        def poll(self):
            return None
        def terminate(self):
            pass
        def wait(self, timeout=None):
            return 0
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: object() if name == "playwright" else real_find(name, *a))
    monkeypatch.setattr(social_browser.subprocess, "Popen", FakeProcess)
    try:
        responses = await asyncio.gather(*(http.post("/api/social/browser/x/open") for _ in range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert len(calls) == 1
    finally:
        social_browser.SESSIONS.pop((str(app.state.store.directory), "x"), None)


async def test_2b_concurrent_partial_social_settings_keep_both_changes(client):
    http, app = client
    task = (await http.post("/api/tasks", json=dict(country="US", state="Fixture", city="Fixture",
                                                    keywords=["clothing"], company_types=[], auto_enrich=False))).json()
    path = f"/api/tasks/{task['id']}/social-settings"
    for round in range(50):
        posts = round % 2 == 0
        platforms = ["x"] if posts else ["facebook", "instagram"]
        a, b = await asyncio.gather(http.patch(path, json={"social_read_posts": posts}),
                                    http.patch(path, json={"social_platforms": platforms}))
        assert a.status_code == b.status_code == 200, (a.text, b.text)
        current = (await http.get(f"/api/tasks")).json()
        mine = next(t for t in current if t["id"] == task["id"])
        assert mine["social_read_posts"] is posts and mine["social_platforms"] == platforms


async def test_3_restore_is_exclusive(client, monkeypatch):
    http, app = client
    log = []
    lock = threading.Lock()
    def restore(name):
        with lock:
            log.append(("restore", "in", time.monotonic()))
        time.sleep(1)
        with lock:
            log.append(("restore", "out", time.monotonic()))
    def tasks():
        with lock:
            log.append(("tasks", "in", time.monotonic()))
        time.sleep(0.2)
        with lock:
            log.append(("tasks", "out", time.monotonic()))
        return []
    monkeypatch.setattr(app.state.store, "restore", restore)
    monkeypatch.setattr(app.state.store, "tasks", tasks)
    before = [asyncio.create_task(http.get("/api/tasks")) for _ in range(2)]
    await asyncio.sleep(0.05)
    restoring = asyncio.create_task(http.post("/api/backups/restore", json={"name": "backup-x.sqlite3"}))
    await asyncio.sleep(0.05)
    after = [asyncio.create_task(http.get("/api/tasks")) for _ in range(5)]
    results = await asyncio.gather(*before, restoring, *after)
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    start = next(t for k, e, t in log if k == "restore" and e == "in")
    end = next(t for k, e, t in log if k == "restore" and e == "out")
    for kind, edge, stamp in log:
        if kind == "tasks":
            assert not start < stamp < end, (edge, stamp, start, end)
    assert sum(1 for k, e, _ in log if k == "tasks" and e == "out") == 7


async def test_4_restore_times_out_while_a_request_runs(client, monkeypatch):
    http, app = client
    app.state.gate.exclusive_wait = 0.5
    monkeypatch.setattr(app.state.store, "tasks", lambda: time.sleep(2) or [])
    monkeypatch.setattr(app.state.store, "restore", lambda name: pytest.fail("restore must not start"))
    slow = asyncio.create_task(http.get("/api/tasks"))
    await asyncio.sleep(0.1)
    response = await http.post("/api/backups/restore", json={"name": "backup-x.sqlite3"})
    assert response.status_code == 409 and response.json()["code"] == "maintenance_busy"
    # Queued shared requests are released after the timeout.
    settings, elapsed = await timed(http, "/api/settings")
    assert settings.status_code == 200 and elapsed < 0.2
    assert (await slow).status_code == 200


async def test_shared_requests_time_out_during_a_long_restore(client, monkeypatch):
    http, app = client
    app.state.gate.shared_wait = 0.3
    monkeypatch.setattr(app.state.store, "restore", lambda name: time.sleep(1))
    restoring = asyncio.create_task(http.post("/api/backups/restore", json={"name": "backup-x.sqlite3"}))
    await asyncio.sleep(0.1)
    response = await http.get("/api/tasks")
    assert response.status_code == 503 and response.json()["code"] == "maintenance"
    assert (await restoring).status_code == 200


async def test_5_concurrent_writes_keep_invariants(client):
    http, app = client
    store = app.state.store
    company = store.manual_company("Fixture Co", "https://fixture.example")
    responses = await asyncio.gather(*(http.patch(f"/api/companies/{company['id']}",
                                                  json={"revision": company["revision"], "notes": f"n{i}"})
                                       for i in range(20)))
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1 and codes.count(409) == 19, codes
    payload = dict(country="US", state="Fixture", city="Fixture", keywords=["same"], company_types=[],
                   auto_enrich=False)
    created = await asyncio.gather(*(http.post("/api/tasks", json=payload) for _ in range(10)))
    assert all(r.status_code == 200 for r in created)
    assert len({r.json()["id"] for r in created}) == 1
    with store.connect() as con:
        con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,status,
            created_at,fingerprint) VALUES('rv','%s','field_update','phone','','+1 408 555 0101','u','e','pending',
            '2026-10-18','fp-rv')""" % company["id"])
    decisions = await asyncio.gather(*(http.post("/api/reviews/rv/decision", json={"decision": "accept"})
                                       for _ in range(2)))
    assert sorted(r.status_code for r in decisions) == [200, 409]


class FakeCon:
    def __init__(self, failures, wait=0.0):
        self.failures = failures
        self.wait = wait
        self.calls = 0

    def execute(self, sql):
        self.calls += 1
        if self.calls <= self.failures:
            time.sleep(self.wait)
            raise sqlite3.OperationalError("database is locked")


def test_6_begin_retries_only_immediate_busy():
    quick = FakeCon(2)
    Store._begin(quick)
    assert quick.calls == 3
    waited = FakeCon(5, wait=1.0)
    with pytest.raises(sqlite3.OperationalError):
        Store._begin(waited)
    assert waited.calls == 1
    always = FakeCon(10)
    with pytest.raises(sqlite3.OperationalError):
        Store._begin(always)
    assert always.calls == maintenance.BEGIN_RETRIES
    other = FakeCon(0)
    other.execute = lambda sql: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: x"))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        Store._begin(other)


async def test_7_facet_refresh_busy_does_not_fail_reads(client, monkeypatch):
    http, app = client
    store = app.state.store
    company = store.manual_company("Facet Co", "https://facet.example")
    store.add_manual_contact(company["id"], revision=store.company(company["id"])["revision"], name="A", role="",
                             email="a@facet.example", phone="", url="", procurement=False, note="")
    with store.connect() as con:
        con.execute("INSERT OR IGNORE INTO contact_facets_dirty(contact_id) SELECT id FROM contacts")
    def locked(con):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(store, "_begin", locked)
    response = await http.get("/api/company-page")
    assert response.status_code == 200


async def test_8_streamed_body_never_touches_the_store(client, monkeypatch):
    http, app = client
    store = app.state.store
    for i in range(30):
        store.manual_company(f"Stream {i}", f"https://s{i}.example")
    calls = []
    real_connect = Store.connect
    def connect(self):
        calls.append(time.monotonic())
        return real_connect(self)
    returned = {}
    real_array = api_module.json_array
    def array(rows):
        response = real_array(rows)
        returned["at"] = time.monotonic()
        return response
    real_source = Store._backup_source
    def backup_source(self):
        calls.append(time.monotonic())
        return real_source(self)
    monkeypatch.setattr(Store, "connect", connect)
    monkeypatch.setattr(Store, "_backup_source", backup_source)
    monkeypatch.setattr(api_module, "json_array", array)
    async with http.stream("GET", "/api/companies") as response:
        body = b"".join([chunk async for chunk in response.aiter_bytes()])
    finished = time.monotonic()
    assert response.status_code == 200 and len(json.loads(body)) == 30
    assert response.headers["X-Total-Count"] == "30"
    assert not [t for t in calls if returned["at"] < t < finished]


async def test_array_responses_are_byte_identical(client):
    http, app = client
    store = app.state.store
    for i in range(3):
        company = store.manual_company(f"Bytes {i} – ü", f"https://b{i}.example")
        with store.connect() as con:
            con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,
                status,created_at,fingerprint) VALUES(?,?,'field_update','phone','','+1 408 555 010'||?,'u','证据',
                'pending','2026-10-18',?)""", (f"rv{i}", company["id"], i, f"fp{i}"))
    for path, rows in (("/api/companies", store.companies()), ("/api/reviews", store.reviews())):
        expected = JSONResponse(jsonable_encoder(rows)).body
        response = await http.get(path)
        assert response.content == expected, path


def test_worker_health_is_consistent_across_threads(tmp_path):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    (tmp_path / "worker-heartbeat.json").write_text(json.dumps({"pid": 1, "last_seen": stamp}))
    results = []
    barrier = threading.Barrier(20)
    def probe():
        barrier.wait()
        results.append(worker.worker_health(tmp_path)["alive"])
    threads = [threading.Thread(target=probe) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [False] * 20
