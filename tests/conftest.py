from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.deps import Services, get_services
from app.main import app
from tests.fakes import InMemoryStore, build_services
from vector_core.config import Settings

OWNER_EMAIL = "owner@example.test"
OWNER_PASSWORD = "owner-test-password"
MCP_TOKEN = "mcp-test-token"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="",
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        mcp_token=MCP_TOKEN,
    )


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def services(settings: Settings, store: InMemoryStore) -> Services:
    return build_services(settings, store)


@pytest.fixture
def make_client(store: InMemoryStore) -> Iterator[Callable[[Settings], TestClient]]:
    def factory(settings: Settings) -> TestClient:
        services = build_services(settings, store)
        app.dependency_overrides[get_services] = lambda: services
        return TestClient(app)

    yield factory
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client: Callable[[Settings], TestClient], settings: Settings) -> TestClient:
    return make_client(settings)


@pytest.fixture
def owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}
