import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.deps import get_services
from app.main import app
from tests.conftest import MCP_TOKEN, OWNER_EMAIL, OWNER_PASSWORD
from tests.fakes import (
    FakeEmbedder,
    InMemoryChunkRepository,
    InMemorySourceRepository,
    InMemoryStore,
    InMemoryVersionRepository,
    build_services,
    memory_indexing_scope,
)
from vector_core.config import Settings
from vector_core.domain import (
    TEXT_SEARCH_UNAVAILABLE,
    VECTOR_SEARCH_UNAVAILABLE,
    Chunk,
    NotFound,
    ServiceUnavailable,
    Source,
    ValidationError,
)
from vector_core.embeddings import NullEmbedder
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.ports import IndexBackendError
from vector_core.services import IndexingService, SearchService
from vector_core.services.search import rrf_fuse

TEXT = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"


class RaisingEmbedder:
    dim = 8

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        raise IndexBackendError("embeddings unavailable")


class BrokenTextIndex(InMemoryTextIndex):
    def search(self, project_id: UUID, query: str, limit: int) -> list[tuple[UUID, float]]:
        raise IndexBackendError("typesense is down")


@dataclass
class SearchEnv:
    settings: Settings
    store: InMemoryStore
    services: object
    text: InMemoryTextIndex
    vector: InMemoryVectorIndex
    embedder: FakeEmbedder
    indexing: IndexingService
    project_id: UUID

    def publish(self, title: str, text: str, project_id: UUID | None = None) -> Source:
        project = project_id if project_id is not None else self.project_id
        source = self.services.sources.add_text(project, title, text)
        version = next(v for v in self.store.versions.values() if v.source_id == source.id)
        self.indexing.index_version(project, source.id, version.id)
        return source

    def search_service(self, text=None, vector=None, embedder=None, settings=None) -> SearchService:
        return SearchService(
            settings or self.settings,
            self.services.projects,
            InMemorySourceRepository(self.store),
            InMemoryVersionRepository(self.store),
            InMemoryChunkRepository(self.store),
            text or self.text,
            vector or self.vector,
            embedder or self.embedder,
        )


@pytest.fixture
def search_env(settings: Settings, store: InMemoryStore) -> SearchEnv:
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    embedder = FakeEmbedder()
    services = build_services(
        settings, store, text_index=text, vector_index=vector, embedder=embedder
    )
    indexing = IndexingService(memory_indexing_scope(store), text, vector, embedder, settings)
    project = services.projects.create("alpha project")
    return SearchEnv(settings, store, services, text, vector, embedder, indexing, project.id)


def test_rrf_fuse_ranks_items_found_by_both_rankings_first():
    a, b, c = uuid4(), uuid4(), uuid4()

    fused = rrf_fuse([[a, b], [b, c]])

    assert [item for item, _ in fused] == [b, a, c]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_search_returns_full_text_hits_with_warning_when_embeddings_are_missing(search_env: SearchEnv):
    source = search_env.publish("Greek", TEXT)

    result = search_env.search_service(embedder=NullEmbedder()).search(
        search_env.project_id, "alpha", 5
    )

    assert result.warning == VECTOR_SEARCH_UNAVAILABLE
    assert [hit.source_id for hit in result.hits] == [source.id]
    assert result.hits[0].version == 1
    assert result.hits[0].title == "Greek"


def test_search_isolates_projects(search_env: SearchEnv):
    services = search_env.services
    other = services.projects.create("beta project")
    own = search_env.publish("Own", TEXT)
    search_env.publish("Foreign", TEXT, project_id=other.id)

    result = search_env.search_service().search(search_env.project_id, TEXT, 10)

    assert {hit.source_id for hit in result.hits} == {own.id}


def test_slow_embedder_is_cut_off_by_timeout(search_env: SearchEnv):
    search_env.publish("Slow", TEXT)
    settings = replace(search_env.settings, search_timeout_seconds=0.2)
    slow = FakeEmbedder(delay=1.0)

    started = time.monotonic()
    result = search_env.search_service(embedder=slow, settings=settings).search(
        search_env.project_id, TEXT, 5
    )

    assert time.monotonic() - started < 0.9
    assert result.warning == VECTOR_SEARCH_UNAVAILABLE
    assert len(result.hits) == 1


def test_vector_backend_error_yields_warning(search_env: SearchEnv):
    search_env.publish("Broken vectors", TEXT)

    result = search_env.search_service(embedder=RaisingEmbedder()).search(
        search_env.project_id, "alpha", 5
    )

    assert result.warning == VECTOR_SEARCH_UNAVAILABLE
    assert len(result.hits) == 1


def test_text_backend_error_yields_warning_and_keeps_vector_hits(search_env: SearchEnv):
    source = search_env.publish("Broken text", TEXT)

    result = search_env.search_service(text=BrokenTextIndex()).search(
        search_env.project_id, TEXT, 5
    )

    assert result.warning == TEXT_SEARCH_UNAVAILABLE
    assert [hit.source_id for hit in result.hits] == [source.id]


def test_both_backends_down_is_service_unavailable(search_env: SearchEnv):
    search_env.publish("Down", TEXT)

    with pytest.raises(ServiceUnavailable) as raised:
        search_env.search_service(
            text=BrokenTextIndex(), embedder=RaisingEmbedder()
        ).search(search_env.project_id, "alpha", 5)

    assert raised.value.code == "search_unavailable"
    assert raised.value.status_code == 503


def test_orphan_chunk_is_dropped(search_env: SearchEnv):
    search_env.publish("Real", TEXT)
    orphan = Chunk(
        id=uuid4(),
        project_id=search_env.project_id,
        source_id=uuid4(),
        version_id=uuid4(),
        ordinal=0,
        text="orphan marker zzzz",
    )
    search_env.text.upsert([orphan])

    result = search_env.search_service(embedder=NullEmbedder()).search(
        search_env.project_id, "zzzz", 5
    )

    assert result.hits == []


def test_chunks_of_unpublished_version_are_dropped(search_env: SearchEnv):
    source = search_env.publish("Draft", TEXT)
    search_env.services.sources.edit(search_env.project_id, source.id, text="pending zzzz marker")
    pending = next(
        v for v in search_env.store.versions.values() if v.source_id == source.id and v.version == 2
    )
    chunk = Chunk(
        id=uuid4(),
        project_id=search_env.project_id,
        source_id=source.id,
        version_id=pending.id,
        ordinal=0,
        text="pending zzzz marker",
    )
    search_env.store.chunks[chunk.id] = chunk
    search_env.text.upsert([chunk])

    result = search_env.search_service(embedder=NullEmbedder()).search(
        search_env.project_id, "zzzz", 5
    )

    assert result.hits == []


def test_chunks_of_superseded_version_are_dropped(search_env: SearchEnv):
    source = search_env.publish("Superseded", TEXT)
    search_env.store.sources[source.id] = replace(
        search_env.store.sources[source.id], current_version=2
    )

    result = search_env.search_service(embedder=NullEmbedder()).search(
        search_env.project_id, "alpha", 5
    )

    assert result.hits == []


def test_chunks_pointing_to_foreign_project_source_are_dropped(search_env: SearchEnv):
    other = search_env.services.projects.create("beta project")
    foreign = search_env.publish("Foreign", TEXT, project_id=other.id)
    foreign_version = next(
        v for v in search_env.store.versions.values() if v.source_id == foreign.id
    )
    chunk = Chunk(
        id=uuid4(),
        project_id=search_env.project_id,
        source_id=foreign.id,
        version_id=foreign_version.id,
        ordinal=0,
        text="foreign zzzz marker",
    )
    search_env.store.chunks[chunk.id] = chunk
    search_env.text.upsert([chunk])

    result = search_env.search_service(embedder=NullEmbedder()).search(
        search_env.project_id, "zzzz", 5
    )

    assert result.hits == []


def test_context_respects_budget_and_lists_each_source_once(search_env: SearchEnv):
    long_text = " ".join(f"alpha word{i}" for i in range(400))
    search_env.publish("Long doc", long_text)
    service = search_env.search_service()

    context = service.context(search_env.project_id, "alpha", max_chars=400)

    assert len(context.context) <= 400
    assert context.context.startswith("[1] Long doc (v1, score ")
    assert len(context.sources) == 1
    assert context.sources[0].title == "Long doc"
    assert context.warning is None


def test_search_validates_input(search_env: SearchEnv):
    service = search_env.search_service()

    with pytest.raises(ValidationError):
        service.search(search_env.project_id, "   ", 5)
    with pytest.raises(ValidationError):
        service.search(search_env.project_id, "alpha", 0)
    with pytest.raises(NotFound):
        service.search(uuid4(), "alpha", 5)


@pytest.fixture
def search_client(search_env: SearchEnv) -> Iterator[TestClient]:
    app.dependency_overrides[get_services] = lambda: search_env.services
    yield TestClient(app)
    app.dependency_overrides.clear()


def _owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_search_endpoint_requires_project_id(search_env: SearchEnv, search_client: TestClient):
    response = search_client.post(
        "/api/v1/search", json={"query": "alpha"}, headers=_owner_headers(search_client)
    )

    assert response.status_code == 422


def test_search_endpoint_returns_hits_for_owner(search_env: SearchEnv, search_client: TestClient):
    search_env.publish("Owner doc", TEXT)

    response = search_client.post(
        "/api/v1/search",
        json={"project_id": str(search_env.project_id), "query": "alpha"},
        headers=_owner_headers(search_client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["warning"] is None
    assert body["hits"][0]["title"] == "Owner doc"


def test_api_key_of_other_project_is_forbidden(search_env: SearchEnv, search_client: TestClient):
    other = search_env.services.projects.create("other")
    key = search_env.services.keys.issue(search_env.project_id)

    response = search_client.post(
        "/api/v1/search",
        json={"project_id": str(other.id), "query": "alpha"},
        headers={"X-Api-Key": key.secret},
    )

    assert response.status_code == 403


def test_agent_token_can_search(search_env: SearchEnv, search_client: TestClient):
    search_env.publish("Agent doc", TEXT)

    response = search_client.post(
        "/api/v1/search",
        json={"project_id": str(search_env.project_id), "query": "alpha"},
        headers={"Authorization": f"Bearer {MCP_TOKEN}"},
    )

    assert response.status_code == 200


def test_search_endpoint_without_credentials_is_unauthorized(search_client: TestClient, search_env: SearchEnv):
    response = search_client.post(
        "/api/v1/search",
        json={"project_id": str(search_env.project_id), "query": "alpha"},
    )

    assert response.status_code == 401


def test_search_endpoint_unknown_project_is_not_found(search_client: TestClient):
    response = search_client.post(
        "/api/v1/search",
        json={"project_id": str(uuid4()), "query": "alpha"},
        headers=_owner_headers(search_client),
    )

    assert response.status_code == 404


def test_search_endpoint_is_unavailable_when_both_indexes_are_down(
    search_env: SearchEnv, settings: Settings, store: InMemoryStore
):
    search_env.publish("Down", TEXT)
    services = build_services(
        settings,
        store,
        text_index=BrokenTextIndex(),
        vector_index=InMemoryVectorIndex(),
        embedder=RaisingEmbedder(),
    )
    app.dependency_overrides[get_services] = lambda: services
    try:
        response = TestClient(app).post(
            "/api/v1/search",
            json={"project_id": str(search_env.project_id), "query": "alpha"},
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "search_unavailable"


def test_context_endpoint_requires_credentials(search_env: SearchEnv, search_client: TestClient):
    response = search_client.post(
        "/api/v1/context",
        json={"project_id": str(search_env.project_id), "query": "alpha"},
    )

    assert response.status_code == 401


def test_context_endpoint_forbids_key_of_other_project(search_env: SearchEnv, search_client: TestClient):
    other = search_env.services.projects.create("other context")
    key = search_env.services.keys.issue(search_env.project_id)

    response = search_client.post(
        "/api/v1/context",
        json={"project_id": str(other.id), "query": "alpha"},
        headers={"X-Api-Key": key.secret},
    )

    assert response.status_code == 403
