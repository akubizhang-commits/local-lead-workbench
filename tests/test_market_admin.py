"""P1-19: market keyword edits, archive and restore (design §6.1, §6.2, §6.6)."""
import json

from fastapi.testclient import TestClient
import pytest

from leadworkbench.api import create_app
from leadworkbench.db import Store
from leadworkbench.market_plan import estimate_queries

SAN_JOSE = dict(country="US", state="California", city="San Jose", state_id="US-CA", place_id="US-PLACE-0668000")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        client.store = Store(tmp_path)
        yield client


def create(client, keywords, types=("wholesale",), region=SAN_JOSE):
    response = client.post("/api/tasks", json={**region, "keywords": list(keywords), "company_types": list(types),
                                               "auto_enrich": False})
    assert response.status_code == 200, response.text
    return response.json()


def keys(store, task_id):
    with store.connect() as con:
        return {r[0] for r in con.execute("SELECT identity FROM market_keys WHERE task_id=?", (task_id,))}


def changes(store, task_id, field="keywords"):
    with store.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM market_changes WHERE market_id=? AND field=? ORDER BY rowid",
                                             (task_id, field))]


def test_edit_updates_name_and_identity(client):
    store = client.store
    task = create(client, ["metal roofing supply"])
    before = keys(store, task["id"])
    response = client.patch(f"/api/tasks/{task['id']}", json={"keywords": ["metal roofing supply", "sheet metal supplier"]})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["keywords"] == ["metal roofing supply", "sheet metal supplier"]
    assert "sheet metal supplier" in edited["name"] and edited["name"] != task["name"]
    assert keys(store, task["id"]).isdisjoint(before)
    [change] = changes(store, task["id"])
    assert json.loads(change["old_value"]) == {"keywords": ["metal roofing supply"], "identities": sorted(before)}
    assert json.loads(change["new_value"]) == ["metal roofing supply", "sheet metal supplier"]
    assert change["reason"] == "用户修改关键词；下一轮起按新关键词检索，已有企业保留"
    # The old keywords now describe a new market.
    again = create(client, ["metal roofing supply"])
    assert again["id"] != task["id"]
    # Same as now: nothing written.
    client.patch(f"/api/tasks/{task['id']}", json={"keywords": ["Sheet Metal Supplier", "metal roofing supply"]})
    assert len(changes(store, task["id"])) == 1


def test_edit_conflicting_with_another_market_is_409(client):
    a = create(client, ["metal roofing supply"])
    b = create(client, ["sheet metal supplier"])
    response = client.patch(f"/api/tasks/{a['id']}", json={"keywords": ["sheet metal supplier"]})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "market_exists" and body["existing_id"] == b["id"] and body["archived"] is False
    assert body["detail"] == f"已有相同地区和关键词的目标市场「{b['name']}」，请直接使用该市场。"
    assert client.store.task(a["id"])["keywords"] == ["metal roofing supply"]


def test_missing_market_is_404(client):
    assert client.patch("/api/tasks/nope", json={"keywords": ["x"]}).status_code == 404


def test_paused_round_keeps_its_snapshot_and_the_next_round_uses_new_keywords(client):
    store = client.store
    task = create(client, ["metal roofing supply"])
    run = store.start_task(task["id"])
    store.control(run["id"], "pause")
    client.patch(f"/api/tasks/{task['id']}", json={"keywords": ["sheet metal supplier"]})
    assert store.run(run["id"])["snapshot"]["keywords"] == ["metal roofing supply"]
    store.control(run["id"], "cancel")
    fresh = store.start_task(task["id"])
    assert fresh["id"] != run["id"] and fresh["snapshot"]["keywords"] == ["sheet metal supplier"]
    assert estimate_queries(fresh["snapshot"])["terms"] == ["sheet metal supplier"]


@pytest.mark.parametrize("types,expected", [
    (["retail"], ["metal supply store"]),
    (["import"], ["steel importer"]),
    (["wholesale", "retail"], ["metal supplier", "metal supply store"]),
])
def test_preset_expands_with_the_market_company_types(client, types, expected):
    task = create(client, ["x placeholder"], types)
    response = client.patch(f"/api/tasks/{task['id']}", json={"industry": "metals"})
    assert response.status_code == 200, response.text
    assert response.json()["keywords"] == expected  # never the wholesale term unless the market is wholesale


def test_more_than_thirty_after_expansion_is_rejected(client):
    task = create(client, ["x placeholder"], ["retail", "import"])
    response = client.patch(f"/api/tasks/{task['id']}", json={"industry": "metals",
                                                              "keywords": [f"keyword {i}" for i in range(29)]})
    assert response.status_code == 400
    assert response.json()["detail"] == "关键词和预设展开后最多 30 个"
    assert client.store.task(task["id"])["keywords"] == ["x placeholder"]


def test_edit_preview_excludes_the_market_itself(client):
    a = create(client, ["metal roofing supply"])
    b = create(client, ["sheet metal supplier"])
    own = client.post("/api/tasks/preview", json={"market_id": a["id"], "keywords": ["metal roofing supply"]}).json()
    assert own["existing_market"] is None and own["existing_market_scope"] == "edit"
    assert (own["geometry"], own["max"]) == ("catalogue", 421)
    other = client.post("/api/tasks/preview", json={"market_id": a["id"], "keywords": ["sheet metal supplier"]}).json()
    assert other["existing_market"] == {"id": b["id"], "name": b["name"], "archived": False}
    retail = create(client, ["x"], ["retail"])
    expanded = client.post("/api/tasks/preview", json={"market_id": retail["id"], "industry": "metals"}).json()
    assert expanded["keywords"] == ["metal supply store"]
    region = client.post("/api/tasks/preview", json={"market_id": a["id"], "keywords": ["x"], "city": "Campbell"})
    assert region.status_code == 422
    assert [(e["loc"], e["msg"]) for e in region.json()["detail"]] == [(["body", "city"], "不支持此字段")]
    assert client.post("/api/tasks/preview", json={"market_id": "nope", "keywords": ["x"]}).status_code == 404


def test_edit_preview_of_a_municipality_stays_text_only(client):
    from leadworkbench.regions import catalogue, index
    p = next(r for r in catalogue("ZA")["regions"] if r.get("subtype") == "metropolitan_municipality")
    za = create(client, ["metal supplier"], region=dict(country="ZA", state=index()[p["state_id"]]["name"], city=p["name"],
                                                         state_id=p["state_id"], place_id=p["id"]))
    body = client.post("/api/tasks/preview", json={"market_id": za["id"], "keywords": ["steel"]}).json()
    assert body["geometry"] == "text_only"
