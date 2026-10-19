"""422 responses carry Chinese per-field messages and never echo input/ctx."""
import pytest
from fastapi.testclient import TestClient

from leadworkbench.api import create_app
from leadworkbench.validation_messages import URL_MESSAGES, translate_errors


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        yield client


def detail(response):
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert all(set(item) == {"loc", "msg", "type"} for item in body)
    return body


def test_private_ip_website_is_chinese_and_does_not_echo_input(client):
    response = client.post("/api/companies", json={"name": "x", "website": "http://10.0.0.5"})
    body = detail(response)
    assert body[0]["msg"] == "官网不能是内网、本机或保留 IP 地址"
    assert body[0]["loc"][-1] == "website"
    assert "10.0.0.5" not in response.text and '"input"' not in response.text and '"ctx"' not in response.text


def test_task_without_keywords_or_types(client):
    body = detail(client.post("/api/tasks", json={"state": "California", "city": "San Jose"}))
    assert body[0]["msg"] == "请填写检索关键词，或从预设行业中选择一个"
    assert body[0]["loc"] == ["body", "keywords"]
def test_task_with_only_company_types_needs_keywords(client):
    body = detail(client.post("/api/tasks", json={"state": "California", "city": "San Jose",
                                                  "company_types": ["wholesale"]}))
    assert [(e["loc"], e["msg"]) for e in body] == [(["body", "keywords"], "请填写检索关键词，或从预设行业中选择一个")]
def test_task_with_unknown_industry_reports_only_the_industry(client):
    body = detail(client.post("/api/tasks", json={"state": "California", "city": "San Jose", "industry": "unknown"}))
    assert [(e["loc"], e["msg"]) for e in body] == [(["body", "industry"], "未知的预设行业")]


@pytest.mark.parametrize("payload,expected", [
    ({"website": "https://ok.example"}, "此项为必填"),
    ({"name": "x" * 201, "website": "https://ok.example"}, "不能超过 200 个字符"),
    ({"name": "", "website": "https://ok.example"}, "不能为空"),
    ({"name": "x", "website": "https://ok.example", "extra": 1}, "不支持此字段"),
])
def test_common_pydantic_errors_are_chinese(client, payload, expected):
    body = detail(client.post("/api/companies", json=payload))
    assert body[0]["msg"] == expected


def test_invalid_json_and_query_lower_bound(client):
    body = detail(client.post("/api/companies", content=b"{bad", headers={"content-type": "application/json"}))
    assert body[0]["msg"] == "请求内容不是有效的 JSON"
    body = detail(client.get("/api/refresh-activity?page=0"))
    assert body[0]["msg"] == "不能小于 1" and body[0]["loc"] == ["query", "page"]


@pytest.mark.parametrize("english,chinese", URL_MESSAGES.items())
def test_url_table(english, chinese):
    [item] = translate_errors([{"loc": ("body", "website"), "msg": f"Value error, {english}",
                                "type": "value_error", "input": "secret", "ctx": {"error": "x"}}])
    assert item == {"loc": ["body", "website"], "msg": chinese, "type": "value_error"}


def test_unknown_english_has_chinese_fallback():
    [item] = translate_errors([{"loc": ("body",), "msg": "Something odd", "type": "custom"}])
    assert item["msg"] == "输入不符合要求（Something odd）"
