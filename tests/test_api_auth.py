from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import MCP_TOKEN, OWNER_EMAIL, OWNER_PASSWORD
from vector_core.config import Settings


def test_login_returns_session_token_and_expiry(client: TestClient):
    response = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["expires_at"]


def test_session_token_grants_access_to_me_and_logout_revokes_it(client: TestClient):
    token = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/auth/me", headers=headers).json() == {"role": "owner"}

    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_wrong_password_is_rejected_without_session(client: TestClient):
    response = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": "wrong"})

    assert response.status_code == 401
    assert "token" not in response.json()


def test_wrong_email_is_rejected(client: TestClient):
    response = client.post("/api/auth/login", json={"email": "other@example.test", "password": OWNER_PASSWORD})

    assert response.status_code == 401


def test_login_without_configured_credentials_returns_503(make_client):
    client = make_client(Settings(database_url="", email="", password="", mcp_token=MCP_TOKEN))

    response = client.post("/api/auth/login", json={"email": "", "password": ""})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_not_configured"


def test_protected_endpoint_without_credentials_returns_401(client: TestClient):
    assert client.get("/api/projects").status_code == 401


def test_wrong_mcp_token_is_rejected(client: TestClient):
    response = client.get(
        f"/api/v1/projects/{uuid4()}",
        headers={"Authorization": "Bearer not-the-token"},
    )

    assert response.status_code == 401


def test_mcp_token_with_project_id_grants_access(client: TestClient, owner_headers):
    project_id = client.post("/api/projects", json={"name": "Agent"}, headers=owner_headers).json()["id"]

    response = client.get(f"/api/v1/projects/{project_id}", headers={"Authorization": f"Bearer {MCP_TOKEN}"})

    assert response.status_code == 200
    assert response.json()["id"] == project_id
