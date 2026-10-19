"""P1-20: one enabled plan per market, SchedulePatch and per-market ordering (design §7.1, §7.5)."""
from datetime import datetime, timezone
import sqlite3
import threading

from fastapi.testclient import TestClient
import pytest

from leadworkbench import market_admin
from leadworkbench.api import create_app
from leadworkbench.db import Store, calendar_next
from leadworkbench.errors import Conflict

UTC = timezone.utc


def market(store, city="San Jose"):
    return store.create_market(dict(country="US", state="California", city=city, keywords=["metal roofing supply"],
                                    company_types=["wholesale"], auto_enrich=False))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        client.store = Store(tmp_path)
        yield client


def plan(client, task_id, **extra):
    return client.post("/api/schedules", json={"task_id": task_id, "kind": "discovery", **extra})


def test_second_enabled_plan_is_409_and_patch_enable_too(client):
    task = market(client.store)
    first = plan(client, task["id"])
    assert first.status_code == 200 and first.json()["interval_months"] == 1
    assert first.json()["task_name"] == task["name"]
    second = plan(client, task["id"])
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "schedule_exists" and body["existing_id"] == first.json()["id"]
    assert body["detail"].startswith("该市场已有启用中的定时计划（每 1 个月，下次 ")
    disabled = plan(client, task["id"], enabled=False).json()
    response = client.patch(f"/api/schedules/{disabled['id']}", json={"enabled": True})
    assert response.status_code == 409 and response.json()["existing_id"] == first.json()["id"]


def test_default_intervals(client):
    task = market(client.store)
    company = client.store.manual_company("Fixture", "https://fixture.example")
    assert plan(client, task["id"]).json()["interval_months"] == 1
    refresh = client.post("/api/schedules", json={"company_id": company["id"], "kind": "refresh"}).json()
    assert refresh["interval_months"] == 3


def test_patch_interval_recomputes_next_due_and_keeps_anchor(client):
    task = market(client.store)
    created = plan(client, task["id"]).json()
    before = datetime.now(UTC)
    updated = client.patch(f"/api/schedules/{created['id']}", json={"interval_months": 6, "timezone": "UTC"}).json()
    assert updated["anchor"] == created["anchor"] and updated["interval_months"] == 6
    expected = calendar_next(datetime.fromisoformat(created["anchor"]), before, 6, "UTC")
    assert datetime.fromisoformat(updated["next_due_at"]) == expected


def test_patch_validation_and_missing_plan(client):
    task = market(client.store)
    created = plan(client, task["id"]).json()
    empty = client.patch(f"/api/schedules/{created['id']}", json={})
    assert empty.status_code == 422 and "请至少修改一项" in str(empty.json()["detail"])
    assert client.patch(f"/api/schedules/{created['id']}", json={"kind": "refresh"}).status_code == 422
    missing = client.patch("/api/schedules/nope", json={"enabled": False})
    assert missing.status_code == 404 and missing.json()["detail"] == "计划不存在"


def test_concurrent_creates_and_enables_leave_exactly_one(tmp_path):
    store = Store(tmp_path)
    task = market(store)
    results = []
    def create():
        try:
            results.append(store.create_schedule(task_id=task["id"], kind="discovery")["id"])
        except Conflict as exc:
            results.append(exc)
    threads = [threading.Thread(target=create) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ok = [r for r in results if isinstance(r, str)]
    assert len(ok) == 1 and results[[isinstance(r, Conflict) for r in results].index(True)].code == "schedule_exists"
    store.update_schedule(ok[0], enabled=False)
    a = store.create_schedule(task_id=task["id"], kind="discovery", enabled=False)["id"]
    results.clear()
    def enable(id):
        try:
            results.append(store.update_schedule(id, enabled=True)["id"])
        except Conflict as exc:
            results.append(exc)
    threads = [threading.Thread(target=enable, args=(id,)) for id in (ok[0], a)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = [r for r in results if isinstance(r, str)]
    losers = [r for r in results if isinstance(r, Conflict)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].code == "schedule_exists" and losers[0].extra["existing_id"] == winners[0]


def test_the_unique_index_backs_up_the_store_check(tmp_path, monkeypatch):
    store = Store(tmp_path)
    task = market(store)
    first = store.create_schedule(task_id=task["id"], kind="discovery")
    disabled = store.create_schedule(task_id=task["id"], kind="discovery", enabled=False)
    monkeypatch.setattr(market_admin.MarketAdminMixin, "_schedule_conflict",
                        lambda self, con, task_id, exclude_id=None: None)
    with pytest.raises(Conflict) as created:
        store.create_schedule(task_id=task["id"], kind="discovery")
    assert created.value.code == "schedule_exists"
    with pytest.raises(Conflict) as enabled:
        store.update_schedule(disabled["id"], enabled=True)
    assert enabled.value.code == "schedule_exists"
    assert [s["id"] for s in store.schedules(task["id"]) if s["enabled"]] == [first["id"]]


def test_other_integrity_errors_are_not_turned_into_409(tmp_path, monkeypatch):
    store = Store(tmp_path)
    company = store.manual_company("Fixture", "https://fixture.example")
    existing = store.create_schedule(company_id=company["id"], kind="refresh")
    monkeypatch.setattr(market_admin, "ident", lambda: existing["id"])
    with pytest.raises(sqlite3.IntegrityError, match="schedules.id"):
        store.create_schedule(company_id=company["id"], kind="refresh")


def test_reenabling_an_overdue_plan_does_not_catch_up(client):
    store = client.store
    task = market(store)
    created = store.create_schedule(task_id=task["id"], kind="discovery", enabled=False)
    with store.connect() as con:
        con.execute("UPDATE schedules SET next_due_at='2026-01-01T00:00:00+00:00' WHERE id=?", (created["id"],))
        runs = con.execute("SELECT count(*) FROM runs").fetchone()[0]
    response = client.patch(f"/api/schedules/{created['id']}", json={"enabled": True})
    assert response.status_code == 200
    store.dispatch_due()
    after = store.schedule(created["id"])
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == runs
    assert after["next_due_at"] > datetime.now(UTC).isoformat() and after["anchor"] == created["anchor"]


def test_enabling_a_legacy_plan_with_missing_settings_fills_them(client):
    store = client.store
    task = market(store)
    with store.connect() as con:
        con.execute("""INSERT INTO schedules(id,task_id,company_id,kind,interval_months,timezone,next_due_at,enabled,anchor)
            VALUES('legacy',?,NULL,'discovery',NULL,NULL,NULL,0,NULL)""", (task["id"],))
    response = client.patch("/api/schedules/legacy", json={"enabled": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["anchor"] and body["interval_months"] == 1 and body["timezone"] == "Asia/Shanghai"
    assert body["next_due_at"] > datetime.now(UTC).isoformat()
    with store.connect() as con:
        change = con.execute("SELECT field,reason FROM market_changes WHERE market_id=?", (task["id"],)).fetchall()
    assert [tuple(c) for c in change] == [("schedule", "补齐计划缺失的设置后重新计算下次时间")]


def test_schedules_of_one_market_are_ordered_enabled_first_then_anchor(client):
    store = client.store
    task, other = market(store), market(store, city="Campbell")
    rows = [("late", "2026-05-01T00:00:00+00:00", 0), ("null", None, 0), ("early", "2026-02-01T00:00:00+00:00", 0),
            ("on", "2026-09-01T00:00:00+00:00", 1)]
    with store.connect() as con:
        for id, anchor, enabled in rows:
            con.execute("""INSERT INTO schedules(id,task_id,company_id,kind,interval_months,timezone,next_due_at,enabled,anchor)
                VALUES(?,?,NULL,'discovery',1,'UTC','2026-12-01T00:00:00+00:00',?,?)""", (id, task["id"], enabled, anchor))
        con.execute("""INSERT INTO schedules(id,task_id,company_id,kind,interval_months,timezone,next_due_at,enabled,anchor)
            VALUES('other',?,NULL,'discovery',1,'UTC','2026-11-01T00:00:00+00:00',1,'2026-01-01T00:00:00+00:00')""",
                    (other["id"],))
    listed = client.get("/api/schedules", params={"task_id": task["id"]}).json()
    assert [s["id"] for s in listed] == ["on", "early", "late", "null"]
    assert client.get("/api/schedules", params={"task_id": "x" * 65}).status_code == 422
    everything = client.get("/api/schedules").json()
    assert [s["next_due_at"] for s in everything] == sorted(s["next_due_at"] for s in everything)
    assert everything[0]["id"] == "other"
