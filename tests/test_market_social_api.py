"""Market social API contracts; ASGI requests and temporary databases only."""
import json

import pytest
from fastapi.testclient import TestClient

from leadworkbench.api import create_app
from leadworkbench.db import Conflict


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    # Skip lifespan so API tests never open or close a browser session.
    client = TestClient(app)
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    try:
        yield client, app.state.store
    finally:
        client.close()


def task_payload(**settings):
    return dict(country="US", state="Fixture", city="Fixture",
                keywords=["clothing"], company_types=[], auto_enrich=False, **settings)


def create_task(client, **settings):
    response = client.post("/api/tasks", json=task_payload(**settings))
    assert response.status_code == 200, response.text
    return response.json()


def task_settings(task):
    return {key: task[key] for key in ("auto_social", "social_platforms", "social_read_posts")}


def market_with_company(client, store):
    task = create_task(client)
    run = client.post(f"/api/tasks/{task['id']}/runs").json()
    store.ingest_market(dict(
        source_key="fixture:api", source_url="https://maps.example/api",
        name="API fixture", address="Fixture street", website=""),
        run["id"], "clothing", task["id"])
    return task, run


def test_legacy_creation_returns_disabled_defaults_in_task_list(api):
    client, store = api
    task = create_task(client)
    expected = {
        "auto_social": False, "social_platforms": ["facebook", "instagram", "x"],
        "social_read_posts": False,
    }
    assert task_settings(task) == expected
    assert task_settings(client.get("/api/tasks").json()[0]) == expected
    run = client.post(f"/api/tasks/{task['id']}/runs").json()
    assert task_settings(run["snapshot"]) == expected
    assert store.social_runs() == []


def test_creation_passes_social_options_to_store_and_run_snapshot(api):
    client, store = api
    task = create_task(client, auto_social=True,
                       social_platforms=["x", "facebook", "x"], social_read_posts=True)
    expected = {"auto_social": True, "social_platforms": ["x", "facebook"], "social_read_posts": True}
    assert task_settings(task) == expected
    assert task_settings(client.get("/api/tasks").json()[0]) == expected
    run = client.post(f"/api/tasks/{task['id']}/runs").json()
    assert task_settings(run["snapshot"]) == expected
    with store.connect() as con:
        config = con.execute("""SELECT s.settings FROM social_runs s JOIN market_social_runs m
            ON m.social_run_id=s.id WHERE m.market_run_id=?""", (run["id"],)).fetchone()[0]
    assert json.loads(config)["market_options"] == expected


def test_disabled_creation_allows_no_selected_platforms(api):
    client, _ = api
    task = create_task(client, auto_social=False, social_platforms=[])
    assert task_settings(task) == {
        "auto_social": False, "social_platforms": [], "social_read_posts": False}


@pytest.mark.parametrize("settings", [
    {"auto_social": True, "social_platforms": []},
    {"social_platforms": ["linkedin"]},
    {"social_platforms": ["facebook", "unknown"]},
    {"social_platforms": "facebook"},
    {"social_platforms": None},
    {"social_platforms": [None]},
    {"auto_social": "sometimes"},
    {"social_read_posts": "sometimes"},
])
def test_creation_rejects_invalid_social_options_without_creating_task(api, settings):
    client, store = api
    response = client.post("/api/tasks", json=task_payload(**settings))
    assert response.status_code == 422, response.text
    assert store.tasks() == []


def test_patch_updates_future_settings_and_task_list_without_changing_current_run(api):
    client, store = api
    task = create_task(client)
    original = client.post(f"/api/tasks/{task['id']}/runs").json()
    settings = {"auto_social": True, "social_platforms": ["instagram"], "social_read_posts": True}
    response = client.patch(f"/api/tasks/{task['id']}/social-settings", json=settings)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == task["id"]
    assert task_settings(response.json()) == settings
    assert task_settings(client.get("/api/tasks").json()[0]) == settings
    assert store.run(original["id"])["snapshot"] == original["snapshot"]
    assert store.social_runs() == []
    assert client.post(f"/api/runs/{original['id']}/cancel").status_code == 200
    later = client.post(f"/api/tasks/{task['id']}/runs").json()
    assert task_settings(later["snapshot"]) == settings


@pytest.mark.parametrize("patch,expected", [
    ({}, {"auto_social": True, "social_platforms": ["x"], "social_read_posts": False}),
    ({"social_read_posts": True},
     {"auto_social": True, "social_platforms": ["x"], "social_read_posts": True}),
    ({"auto_social": False, "social_platforms": []},
     {"auto_social": False, "social_platforms": [], "social_read_posts": False}),
])
def test_patch_preserves_omitted_settings(api, patch, expected):
    client, _ = api
    task = create_task(client, auto_social=True, social_platforms=["x"])
    response = client.patch(f"/api/tasks/{task['id']}/social-settings", json=patch)
    assert response.status_code == 200, response.text
    assert task_settings(response.json()) == expected


@pytest.mark.parametrize("patch,status", [
    ({"auto_social": True, "social_platforms": []}, 422),
    ({"social_platforms": []}, 400),
    ({"social_platforms": ["youtube"]}, 422),
    ({"social_platforms": None}, 422),
    ({"social_read_posts": None}, 422),
    ({"auto_social": None}, 422),
    ({"auto_enrich": False}, 422),
])
def test_invalid_patch_preserves_saved_settings(api, patch, status):
    client, store = api
    task = create_task(client, auto_social=True, social_platforms=["x"])
    response = client.patch(f"/api/tasks/{task['id']}/social-settings", json=patch)
    assert response.status_code == status, response.text
    assert task_settings(store.task(task["id"])) == task_settings(task)


@pytest.mark.parametrize("body,platforms,read_posts", [
    ({}, ["facebook", "instagram", "x"], False),
    ({"social_platforms": ["instagram"], "social_read_posts": True}, ["instagram"], True),
])
def test_explicit_social_start_returns_market_run_and_keeps_future_settings(api, body, platforms, read_posts):
    client, store = api
    task, original = market_with_company(client, store)
    url = f"/api/tasks/{task['id']}/social"
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["id"] == original["id"]
    assert run["task_id"] == task["id"]
    assert task_settings(run["snapshot"]) == {
        "auto_social": True, "social_platforms": platforms, "social_read_posts": read_posts}
    assert task_settings(store.task(task["id"])) == task_settings(task)
    with store.connect() as con:
        targets = [row[0] for row in con.execute("""SELECT w.target FROM social_work w
            JOIN market_social_runs m ON m.social_run_id=w.run_id
            WHERE m.market_run_id=? AND w.stage='platform_search' ORDER BY w.rowid""", (run["id"],))]
    assert targets == platforms
    assert client.post(url, json=body).json()["id"] == run["id"]


@pytest.mark.parametrize("body", [
    {"social_platforms": []},
    {"social_platforms": ["tiktok"]},
    {"social_platforms": None},
    {"social_read_posts": "sometimes"},
    {"auto_social": True},
    {"company_ids": ["fixture"]},
])
def test_explicit_social_start_rejects_invalid_selection(api, body):
    client, store = api
    task, original = market_with_company(client, store)
    response = client.post(f"/api/tasks/{task['id']}/social", json=body)
    assert response.status_code == 422, response.text
    assert store.run(original["id"])["snapshot"] == original["snapshot"]
    assert store.social_runs() == []


def test_explicit_social_start_reports_missing_market_companies(api):
    client, store = api
    task = create_task(client)
    response = client.post(f"/api/tasks/{task['id']}/social", json={})
    assert response.status_code == 400
    assert response.json()["detail"]
    assert store.runs() == []


@pytest.mark.parametrize("method,suffix", [("patch", "social-settings"), ("post", "social")])
def test_missing_task_uses_existing_not_found_response(api, method, suffix):
    client, _ = api
    response = client.request(method, f"/api/tasks/missing/{suffix}", json={})
    assert response.status_code == 404
    assert response.json() == {"detail": "记录不存在"}


@pytest.mark.parametrize("platform", ["facebook", "instagram", "x"])
def test_resume_has_no_body_and_only_clears_selected_platform_gate(api, platform):
    client, store = api
    with store.connect() as con:
        for name in ("facebook", "instagram", "x"):
            con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                        (name, "waiting_login", 0, "Fixture login required", 0))
    response = client.post(f"/api/social/platforms/{platform}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["platform"] == platform
    assert response.json()["state"] == "pending"
    with store.connect() as con:
        remaining = {row[0] for row in con.execute("SELECT platform FROM social_platform_gates")}
    assert remaining == {"facebook", "instagram", "x"} - {platform}


def test_resume_rejects_cooldown_as_conflict_without_clearing_gate(api, monkeypatch):
    from leadworkbench import social_policy

    client, store = api
    monkeypatch.setattr(social_policy.time, "time", lambda: 100)
    with store.connect() as con:
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    ("facebook", "waiting_recovery", 200, "Fixture cooldown", 1))
    response = client.post("/api/social/platforms/facebook/resume")
    assert response.status_code == 409, response.text
    assert "冷却" in response.json()["detail"]
    with store.connect() as con:
        assert con.execute("SELECT not_before FROM social_platform_gates WHERE platform='facebook'").fetchone()[0] == 200


@pytest.mark.parametrize("platform", ["linkedin", "twitter", "unknown"])
def test_resume_rejects_unsupported_platform(api, platform):
    client, _ = api
    assert client.post(f"/api/social/platforms/{platform}/resume").status_code == 422


@pytest.mark.parametrize("method,url", [
    ("patch", "/api/tasks/missing/social-settings"),
    ("post", "/api/tasks/missing/social"),
    ("post", "/api/social/platforms/facebook/resume"),
])
def test_new_mutations_require_local_token_and_same_origin(api, method, url):
    client, _ = api
    assert client.request(method, url, json={}, headers={"X-Local-Token": ""}).status_code == 401
    assert client.request(method, url, json={}, headers={"Origin": "https://elsewhere.example"}).status_code == 403


@pytest.mark.parametrize("method,suffix,store_method", [
    ("patch", "social-settings", "set_task_social_settings"),
    ("post", "social", "start_market_social"),
])
def test_store_conflicts_use_existing_api_response(api, monkeypatch, method, suffix, store_method):
    client, store = api
    task = create_task(client)

    def conflict(*args, **kwargs):
        raise Conflict("Fixture concurrent edit")

    monkeypatch.setattr(store, store_method, conflict)
    response = client.request(method, f"/api/tasks/{task['id']}/{suffix}", json={})
    assert response.status_code == 409
    assert response.json() == {"detail": "Fixture concurrent edit"}
