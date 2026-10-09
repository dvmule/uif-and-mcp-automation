"""Contract tests -- the load-bearing test in UIF. Every response from the
running app is validated against the registered openapi.yaml's component schemas.
If code and contract drift, this fails the build."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.contract.contract_spec import contract_has_path, schema_errors

AUTH = {"Authorization": "Bearer test-token"}


def client() -> TestClient:
    return TestClient(create_app())


def test_contract_declares_served_paths():
    assert contract_has_path("/developers")
    assert contract_has_path("/developers/{developerId}")


def test_register_developer_201_matches_developer():
    res = client().post(
        "/api/v1/developers", headers=AUTH,
        json={"email": "ada@example.com", "displayName": "Ada Lovelace"},
    )
    assert res.status_code == 201
    assert res.headers["location"].startswith("/api/v1/developers/dev_")
    assert schema_errors("Developer", res.json()) == []


def test_invalid_input_returns_400_problem():
    res = client().post(
        "/api/v1/developers", headers=AUTH,
        json={"email": "nope", "displayName": ""},
    )
    assert res.status_code == 400
    assert res.headers["content-type"].startswith("application/problem+json")
    assert schema_errors("Problem", res.json()) == []


def test_duplicate_email_returns_409_problem():
    c = client()
    body = {"email": "dup@example.com", "displayName": "Dup"}
    c.post("/api/v1/developers", headers=AUTH, json=body)
    res = c.post("/api/v1/developers", headers=AUTH, json=body)
    assert res.status_code == 409
    assert schema_errors("Problem", res.json()) == []


def test_missing_bearer_returns_401_problem():
    res = client().post(
        "/api/v1/developers",
        json={"email": "ada@example.com", "displayName": "Ada"},
    )
    assert res.status_code == 401
    assert schema_errors("Problem", res.json()) == []


def test_list_returns_developer_page():
    res = client().get("/api/v1/developers", headers=AUTH)
    assert res.status_code == 200
    assert schema_errors("DeveloperPage", res.json()) == []


def test_get_unknown_id_returns_404_problem():
    res = client().get("/api/v1/developers/dev_missing", headers=AUTH)
    assert res.status_code == 404
    assert schema_errors("Problem", res.json()) == []


def test_get_returns_developer_after_registration():
    c = client()
    created = c.post(
        "/api/v1/developers", headers=AUTH,
        json={"email": "grace@example.com", "displayName": "Grace Hopper"},
    )
    dev_id = created.json()["id"]
    res = c.get(f"/api/v1/developers/{dev_id}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["email"] == "grace@example.com"
    assert schema_errors("Developer", res.json()) == []
