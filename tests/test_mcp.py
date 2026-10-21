from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.deps import get_services, get_settings
from app.main import app
from tests.conftest import MCP_TOKEN
from tests.fakes import InMemoryStore, build_services, memory_indexing_scope
from vector_core.config import Settings
from vector_core.embeddings import NullEmbedder
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.services import IndexingService

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MCP_TOKEN}",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def _call(client: TestClient, method: str, params: dict[str, Any], request_id: int) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def mcp_client(settings: Settings, store: InMemoryStore) -> Iterator[TestClient]:
    services = build_services(settings, store)
    app.dependency_overrides[get_services] = lambda: services
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_mcp_rejects_missing_or_wrong_token(mcp_client: TestClient):
    missing = mcp_client.post("/mcp", json=INITIALIZE, headers={"Accept": "application/json, text/event-stream"})
    wrong = mcp_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={**HEADERS, "Authorization": "Bearer not-the-token"},
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json()["detail"]["code"] == "unauthorized"
    assert wrong.status_code == 401


def test_mcp_lists_exactly_three_tools(mcp_client: TestClient):
    assert mcp_client.post("/mcp", json=INITIALIZE, headers=HEADERS).status_code == 200

    body = _call(mcp_client, "tools/list", {}, 2)

    assert sorted(tool["name"] for tool in body["result"]["tools"]) == ["add_note", "context", "search"]


def test_mcp_add_note_creates_text_source_and_search_finds_it(settings: Settings, store: InMemoryStore):
    text, vector = InMemoryTextIndex(), InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    indexing = IndexingService(memory_indexing_scope(store), text, vector, NullEmbedder(), settings)
    app.dependency_overrides[get_services] = lambda: services
    app.dependency_overrides[get_settings] = lambda: settings
    project = services.projects.create("mcp project")
    try:
        with TestClient(app) as mcp_client:
            _run_add_note_and_search(mcp_client, project.id, store, indexing)
    finally:
        app.dependency_overrides.clear()


def _run_add_note_and_search(mcp_client: TestClient, project_id, store, indexing) -> None:
    mcp_client.post("/mcp", json=INITIALIZE, headers=HEADERS)

    added = _call(
        mcp_client,
        "tools/call",
        {
            "name": "add_note",
            "arguments": {"project_id": str(project_id), "title": "Note", "text": "kiwi mango papaya"},
        },
        3,
    )
    assert added["result"]["isError"] is False
    version = next(v for v in store.versions.values())
    indexing.index_version(project_id, version.source_id, version.id)

    found = _call(
        mcp_client,
        "tools/call",
        {"name": "search", "arguments": {"project_id": str(project_id), "query": "mango"}},
        4,
    )

    assert found["result"]["isError"] is False
    assert "Note" in str(found["result"])


def test_mcp_survives_two_client_lifecycles(settings: Settings, store: InMemoryStore):
    services = build_services(settings, store)
    app.dependency_overrides[get_services] = lambda: services
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        for _ in range(2):
            with TestClient(app) as client:
                assert client.post("/mcp", json=INITIALIZE, headers=HEADERS).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_mcp_search_rejects_unknown_or_invalid_project(mcp_client: TestClient):
    mcp_client.post("/mcp", json=INITIALIZE, headers=HEADERS)

    unknown = _call(
        mcp_client,
        "tools/call",
        {"name": "search", "arguments": {"project_id": str(uuid4()), "query": "alpha"}},
        2,
    )
    invalid = _call(
        mcp_client,
        "tools/call",
        {"name": "search", "arguments": {"project_id": "not-a-uuid", "query": "alpha"}},
        3,
    )

    assert unknown["result"]["isError"] is True
    assert invalid["result"]["isError"] is True


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search", {"limit": 0}),
        ("search", {"limit": 51}),
        ("context", {"max_chars": 199}),
        ("context", {"max_chars": 50001}),
    ],
)
def test_mcp_rejects_out_of_range_limits(mcp_client: TestClient, tool: str, arguments: dict[str, int]):
    mcp_client.post("/mcp", json=INITIALIZE, headers=HEADERS)

    response = _call(
        mcp_client,
        "tools/call",
        {"name": tool, "arguments": {"project_id": str(uuid4()), "query": "alpha", **arguments}},
        2,
    )

    assert response["result"]["isError"] is True
