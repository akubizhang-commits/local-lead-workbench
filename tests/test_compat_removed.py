"""Compatibility endpoints removed in P1 batch 2 (design §10.1, §10.3)."""
from fastapi.testclient import TestClient
import pytest
from leadworkbench.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allowed_hosts={"testserver"})) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        yield client


def test_sync_export_is_gone(client):
    response = client.get("/api/exports", params={"format": "csv"})
    assert response.status_code == 404 and response.json()["code"] == "unknown_endpoint"


def test_lists_without_parameters_return_envelopes(client):
    for n in range(3):
        client.post("/api/companies", json={"name": f"Envelope {n}", "website": f"https://e{n}.example"})
    companies = client.get("/api/companies")
    assert companies.status_code == 200
    body = companies.json()
    assert set(body) >= {"items", "total", "page", "page_size"}
    assert (body["page"], body["page_size"], body["total"]) == (1, 50, 3)
    reviews = client.get("/api/reviews").json()
    assert set(reviews) == {"items", "total", "page", "page_size"}
    assert (reviews["page"], reviews["page_size"]) == (1, 20)


def test_no_deprecation_headers(client):
    for path in ("/api/companies", "/api/reviews", "/api/company-page"):
        response = client.get(path)
        assert "Deprecation" not in response.headers and "Link" not in response.headers, path
        assert "X-Total-Count" not in response.headers, path
