"""P0-6: manually added contacts (design §6.4, user requirement 2)."""
import time

import pytest
from fastapi.testclient import TestClient

from leadworkbench.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    with TestClient(app) as c:
        c.headers["X-Local-Token"] = c.get("/api/session").json()["token"]
        yield c, app.state.store


def company(store):
    return store.manual_company("Manual Co", "https://manual.example")


def post(client, company_id, **body):
    return client.post(f"/api/companies/{company_id}/contacts", json=body)


def added(detail, **match):
    return next(c for c in detail["contacts"] if all(c.get(k) == v for k, v in match.items()))


@pytest.mark.parametrize("body,message", [
    (dict(name="Ann"), "请至少填写邮箱、电话或联系页面中的一项"),
    (dict(email="not-an-email"), "邮箱格式无效"),
    (dict(email="your.email@x.example"), "邮箱格式无效"),
    (dict(phone="12345"), "电话格式无效"),
    (dict(phone="+1 408 555 0100 ext 9"), "电话格式无效"),
    (dict(url="http://127.0.0.1/contact"), "官网不能是内网、本机或保留 IP 地址"),
    (dict(email="a@b.example", name="x" * 121), "不能超过 120 个字符"),
    (dict(email="a@b.example", note="x" * 1001), "不能超过 1000 个字符"),
    (dict(email="a@b.example", extra=1), "不支持此字段"),
])
def test_validation_matrix(client, body, message):
    client, store = client
    c = company(store)
    response = post(client, c["id"], revision=c["revision"], **body)
    assert response.status_code == 422
    assert message in str(response.json()["detail"])
    assert post(client, c["id"], email="a@b.example").status_code == 422  # revision required


def test_checkbox_and_role_rules(client):
    client, store = client
    c = company(store)

    def add(**body):
        response = post(client, c["id"], revision=store.company(c["id"])["revision"], **body)
        assert response.status_code == 200, response.json()
        return response.json()

    detail = add(name="Bob Buyer", email="bob@manual.example", procurement=True)
    bob = added(detail, email="bob@manual.example")
    assert (bob["tier"], bob["kind"], bob["role_status"]) == ("named_procurement", "procurement", "explicit")
    assert store.company_page()["items"][0]["procurement_count"] == 1
    detail = add(email="orders@manual.example", procurement=True)
    orders = added(detail, email="orders@manual.example")
    assert (orders["tier"], orders["role_status"]) == ("department_email", "department")
    assert store.company_page()["items"][0]["procurement_count"] == 2
    detail = add(name="Una Known", email="una@manual.example")
    assert added(detail, email="una@manual.example")["tier"] == "named_unknown"
    assert store.company_page()["items"][0]["procurement_count"] == 2
    detail = add(name="Sam Seller", role="Sales Manager", phone="+1 (408) 555-0100")
    sam = added(detail, name="Sam Seller")
    assert sam["tier"] == "named_role"
    assert {f["key"]: f["value"] for f in sam["fields"]}["phone"] == "+1 (408) 555-0100"
    detail = add(url="https://manual.example/contact-us")
    form = added(detail, url="https://manual.example/contact-us")
    assert (form["tier"], form["kind"]) == ("form_phone", "general")
    with store.connect() as con:
        assert con.execute("SELECT channel_ok FROM contact_facets WHERE contact_id=?", (form["id"],)).fetchone()[0] == 1
    assert all(f["source_label"] == "人工录入" for f in bob["fields"])
    assert bob["review_status"] == "accept" and bob["adoption_status"] == "accepted"
    assert store.company(c["id"])["revision"] == c["revision"] + 5


def test_conflicts(client):
    client, store = client
    c = company(store)
    assert post(client, c["id"], revision=c["revision"] + 1, email="a@manual.example").json()["detail"] == \
        "资料已更新，请刷新后重试"
    assert post(client, "missing", revision=1, email="a@manual.example").status_code == 404
    first = post(client, c["id"], revision=c["revision"], name="Pat", phone="+14085550100")
    assert first.status_code == 200
    revision = store.company(c["id"])["revision"]
    duplicate = post(client, c["id"], revision=revision, name="Pat", phone="+14085550100")
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "该联系人已存在，请直接核对现有记录"
    assert post(client, c["id"], revision=revision, url="https://manual.example/c").status_code == 200
    revision = store.company(c["id"])["revision"]
    assert post(client, c["id"], revision=revision, url="https://manual.example/c").status_code == 409
    assert post(client, c["id"], revision=revision, email="Ann@Manual.example").status_code == 200
    revision = store.company(c["id"])["revision"]
    same = post(client, c["id"], revision=revision, name="Other", email=" ann@manual.example")
    assert same.status_code == 409 and same.json()["detail"] == "该邮箱已在联系人中，请直接核对现有记录"
    store.update_company(c["id"], revision, {}, status="do_not_contact")
    refused = post(client, c["id"], revision=revision + 1, email="z@manual.example")
    assert refused.status_code == 409 and refused.json()["detail"] == "此企业已标记拒绝联系，不能新增联系人"


def test_later_collection_does_not_overwrite_and_returns_quickly(client):
    client, store = client
    c = company(store)
    start = time.perf_counter()
    store.add_manual_contact(c["id"], c["revision"], name="Bob Buyer", email="bob@manual.example", procurement=True)
    assert time.perf_counter() - start < 2
    store.apply_enrichment(c["id"], dict(status="completed", contacts=[dict(
        name="Robert Other", role="Sales Rep", email="bob@manual.example", kind="general", role_status="explicit",
        association_status="same_block", source_url="https://manual.example/about", evidence="Robert Other")]))
    bob = added(store.detail(c["id"]), email="bob@manual.example")
    assert (bob["name"], bob["tier"], bob["origin"]) == ("Bob Buyer", "named_procurement", "manual")
    assert not [r for r in store.reviews() if r["kind"] == "contact"]
