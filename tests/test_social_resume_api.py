"""Bodyless social recovery through ASGI, using only temporary stores."""
import json

import pytest
from fastapi.testclient import TestClient

from leadworkbench import social_policy
from leadworkbench.api import create_app


READABLE = ("facebook", "instagram", "x", "tiktok", "youtube", "pinterest", "bluesky")


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    client = TestClient(app)  # No lifespan/browser initialization.
    client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
    try:
        yield client, app.state.store
    finally:
        client.close()


@pytest.mark.parametrize("platform", READABLE)
def test_resume_all_readable_platforms_only_clears_selected_gate(api, platform):
    client, store = api
    with store.connect() as con:
        for name in READABLE:
            con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                        (name, "waiting_configuration", 0, "Check connection", 4))
    response = client.post(f"/api/social/platforms/{platform}/resume")
    assert response.status_code == 200, response.text
    assert response.json()["platform"] == platform
    with store.connect() as con:
        remaining = [dict(row) for row in con.execute("SELECT * FROM social_platform_gates")]
    assert {row["platform"] for row in remaining} == set(READABLE) - {platform}
    assert all(row["state"] == "waiting_configuration" and row["failures"] == 4 for row in remaining)


@pytest.mark.parametrize("platform", READABLE)
def test_resume_cannot_bypass_cooldown_and_succeeds_at_deadline(api, monkeypatch, platform):
    client, store = api
    clock = [1000.]
    monkeypatch.setattr(social_policy.time, "time", lambda: clock[0])
    with store.connect() as con:
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    (platform, "waiting_recovery", 2000, "Rate limited", 2))
        before = social_policy.gate(con, platform)
    response = client.post(f"/api/social/platforms/{platform}/resume")
    assert response.status_code == 409, response.text
    assert "冷却" in response.json()["detail"]
    with store.connect() as con:
        assert social_policy.gate(con, platform) == before
    clock[0] = 2000
    assert client.post(f"/api/social/platforms/{platform}/resume").status_code == 200
    with store.connect() as con:
        assert social_policy.allowed(con, platform)


def test_resume_refreshes_manual_pending_settings_without_restarting_finished_runs(api):
    client, store = api
    company = store.manual_company("Fixture", "https://fixture.example")
    store.add_social(company["id"], "https://www.youtube.com/@fixture", "Fixture account")
    old = store.create_social_run([company["id"]], read_only=True)
    store.cancel_social_run(old["id"])
    queued = store.create_social_run([company["id"]], read_only=True)
    with store.connect() as con:
        original = {row["id"]: json.loads(row["settings"])
                    for row in con.execute("SELECT id,settings FROM social_runs")}
        con.execute("INSERT INTO social_platform_gates VALUES(?,?,?,?,?)",
                    ("youtube", "waiting_configuration", 0, "Check configuration", 4))
    assert store.claim_social_item() is None
    (store.directory / "social-connections.json").write_text(
        json.dumps({"proxy_url": "http://127.0.0.1:19090", "max_posts": 2}), encoding="utf-8")
    assert client.post("/api/social/platforms/youtube/resume").status_code == 200
    item = store.claim_social_item()
    assert item["run_id"] == queued["id"]
    assert item["settings"]["connections"]["proxy_url"] == "http://127.0.0.1:19090"
    assert item["settings"]["connections"]["max_posts"] == 2
    assert item["settings"]["read_only"] is True
    assert item["settings"]["profile_only"] is False
    assert {key: value for key, value in item["settings"].items() if key != "connections"} == {
        key: value for key, value in original[queued["id"]].items() if key != "connections"}
    assert store.social_run(old["id"])["status"] == "cancelled"
    with store.connect() as con:
        cancelled = json.loads(con.execute("SELECT settings FROM social_runs WHERE id=?", (old["id"],)).fetchone()[0])
    assert cancelled == original[old["id"]]


@pytest.mark.parametrize("platform", ["youtube", "tiktok", "pinterest", "bluesky"])
def test_readable_resume_does_not_expand_market_platform_selection(api, platform):
    client, _ = api
    payload = dict(country="US", state="Fixture", city="Fixture", keywords=["clothing"],
                   company_types=[], auto_enrich=False)
    assert client.post("/api/tasks", json={**payload, "social_platforms": [platform]}).status_code == 422
    task = client.post("/api/tasks", json=payload).json()
    for method, suffix in (("PATCH", "social-settings"), ("POST", "social")):
        assert client.request(method, f"/api/tasks/{task['id']}/{suffix}",
                              json={"social_platforms": [platform]}).status_code == 422


@pytest.mark.parametrize("platform", ["linkedin", "twitter", "unknown"])
def test_resume_rejects_unreadable_platforms(api, platform):
    assert api[0].post(f"/api/social/platforms/{platform}/resume").status_code == 422


def test_extended_resume_still_requires_local_session_and_origin(api):
    client, _ = api
    path = "/api/social/platforms/youtube/resume"
    assert client.post(path, headers={"X-Local-Token": ""}).status_code == 401
    assert client.post(path, headers={"Origin": "https://elsewhere.example"}).status_code == 403
