from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import get_services
from app.main import app
from tests.fakes import build_services
from vector_core.indexes.memory import InMemoryTextIndex


def _create_project(client: TestClient, headers: dict[str, str], name: str) -> str:
    response = client.post("/api/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def _issue_key(client: TestClient, headers: dict[str, str], project_id: str) -> dict:
    response = client.post(f"/api/projects/{project_id}/keys", headers=headers)
    assert response.status_code == 201
    return response.json()


def _v1_get(client: TestClient, project_id: str, api_key: str):
    return client.get(f"/api/v1/projects/{project_id}", headers={"X-Api-Key": api_key})


def test_project_crud(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")

    listed = client.get("/api/projects", headers=owner_headers).json()
    assert [item["id"] for item in listed] == [project_id]

    fetched = client.get(f"/api/projects/{project_id}", headers=owner_headers).json()
    assert fetched["name"] == "Alpha"
    assert fetched["created_at"]

    renamed = client.patch(f"/api/projects/{project_id}", json={"name": "Renamed"}, headers=owner_headers)
    assert renamed.status_code == 200
    assert renamed.json()["id"] == project_id
    assert renamed.json()["name"] == "Renamed"

    assert client.delete(f"/api/projects/{project_id}", headers=owner_headers).status_code == 204
    assert client.get(f"/api/projects/{project_id}", headers=owner_headers).status_code == 404


def test_project_endpoints_require_owner_session(client: TestClient, owner_headers):
    assert client.post("/api/projects", json={"name": "Alpha"}).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_empty_project_name_is_rejected(client: TestClient, owner_headers):
    response = client.post("/api/projects", json={"name": "   "}, headers=owner_headers)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"
    assert client.get("/api/projects", headers=owner_headers).json() == []


def test_duplicate_project_name_is_conflict(client: TestClient, owner_headers):
    _create_project(client, owner_headers, "Alpha")

    response = client.post("/api/projects", json={"name": "Alpha"}, headers=owner_headers)

    assert response.status_code == 409


def test_rename_to_used_name_is_conflict_and_changes_nothing(client: TestClient, owner_headers):
    _create_project(client, owner_headers, "Alpha")
    beta_id = _create_project(client, owner_headers, "Beta")

    response = client.patch(f"/api/projects/{beta_id}", json={"name": "Alpha"}, headers=owner_headers)

    assert response.status_code == 409
    assert client.get(f"/api/projects/{beta_id}", headers=owner_headers).json()["name"] == "Beta"


def test_unknown_project_is_not_found(client: TestClient, owner_headers):
    unknown = uuid4()

    assert client.get(f"/api/projects/{unknown}", headers=owner_headers).status_code == 404
    assert client.patch(f"/api/projects/{unknown}", json={"name": "X"}, headers=owner_headers).status_code == 404
    assert client.delete(f"/api/projects/{unknown}", headers=owner_headers).status_code == 404
    assert client.post(f"/api/projects/{unknown}/keys", headers=owner_headers).status_code == 404


def test_issued_secret_is_shown_once_and_listing_has_no_secret(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")
    issued = _issue_key(client, owner_headers, project_id)

    assert issued["secret"].startswith(f"vk_{issued['prefix']}_")

    listed = client.get(f"/api/projects/{project_id}/keys", headers=owner_headers).json()
    assert len(listed) == 1
    assert "secret" not in listed[0]
    assert listed[0]["status"] == "active"
    assert listed[0]["prefix"] == issued["prefix"]


def test_key_grants_access_only_to_its_own_project(client: TestClient, owner_headers):
    alpha_id = _create_project(client, owner_headers, "Alpha")
    beta_id = _create_project(client, owner_headers, "Beta")
    key = _issue_key(client, owner_headers, alpha_id)["secret"]

    assert _v1_get(client, alpha_id, key).status_code == 200
    assert _v1_get(client, beta_id, key).status_code == 403


def test_revoked_key_is_rejected_and_listed_as_revoked(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")
    issued = _issue_key(client, owner_headers, project_id)

    revoke = client.post(f"/api/projects/{project_id}/keys/{issued['id']}/revoke", headers=owner_headers)

    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"
    assert revoke.json()["revoked_at"]
    assert _v1_get(client, project_id, issued["secret"]).status_code == 401


def test_unknown_and_malformed_keys_give_identical_unauthorized_response(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")

    unknown = _v1_get(client, project_id, "vk_0123abcd_" + "f" * 32)
    malformed = _v1_get(client, project_id, "broken-key")

    assert unknown.status_code == malformed.status_code == 401
    assert unknown.json() == malformed.json()


def test_deleting_project_removes_its_keys(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")
    key = _issue_key(client, owner_headers, project_id)["secret"]

    assert client.delete(f"/api/projects/{project_id}", headers=owner_headers).status_code == 204

    assert _v1_get(client, project_id, key).status_code == 401


def test_delete_project_returns_503_when_index_unavailable_and_keeps_project(
    client: TestClient, owner_headers, settings, store
):
    project_id = _create_project(client, owner_headers, "Alpha")
    text = InMemoryTextIndex()
    text.available = False
    app.dependency_overrides[get_services] = lambda: build_services(settings, store, text_index=text)

    response = client.delete(f"/api/projects/{project_id}", headers=owner_headers)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "index_unavailable"
    assert client.get(f"/api/projects/{project_id}", headers=owner_headers).status_code == 200


def test_v1_without_credentials_returns_401(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")

    assert client.get(f"/api/v1/projects/{project_id}").status_code == 401


def test_owner_session_can_read_project_via_v1(client: TestClient, owner_headers):
    project_id = _create_project(client, owner_headers, "Alpha")

    assert client.get(f"/api/v1/projects/{project_id}", headers=owner_headers).status_code == 200
