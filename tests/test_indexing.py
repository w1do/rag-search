from collections.abc import Iterator
from dataclasses import dataclass, replace
from uuid import UUID

import pytest

from tests.fakes import InMemoryStore, build_services, memory_indexing_scope
from vector_core.config import Settings
from vector_core.domain import SourceStatus, SourceVersion, VersionStatus
from vector_core.embeddings import NullEmbedder
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.ports import IndexBackendError
from vector_core.services import IndexingService, SourceService

TEXT = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
EMBEDDING_DIM = 4


class FakeEmbedder:
    dim: int | None = EMBEDDING_DIM

    def __init__(self) -> None:
        self.available = True

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.available:
            raise IndexBackendError("embeddings unavailable")
        return [[float(len(text)), 1.0, 0.0, 0.0] for text in texts]


@dataclass
class Env:
    store: InMemoryStore
    services: object
    text: InMemoryTextIndex
    vector: InMemoryVectorIndex
    embedder: FakeEmbedder
    indexing: IndexingService
    settings: Settings
    project_id: UUID

    @property
    def sources(self) -> SourceService:
        return self.services.sources

    def index(self, source_id: UUID, version: SourceVersion, final_attempt: bool = True) -> None:
        self.indexing.index_version(
            self.project_id, source_id, version.id, final_attempt=final_attempt
        )

    def versions(self, source_id: UUID) -> list[SourceVersion]:
        return self.sources.versions(self.project_id, source_id)

    def chunks_of(self, version_id: UUID) -> list:
        return [c for c in self.store.chunks.values() if c.version_id == version_id]


@pytest.fixture
def env(settings: Settings, store: InMemoryStore) -> Iterator[Env]:
    small = replace(settings, chunk_size_chars=20, chunk_overlap_chars=5)
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    embedder = FakeEmbedder()
    services = build_services(small, store, text_index=text, vector_index=vector)
    indexing = IndexingService(memory_indexing_scope(store), text, vector, embedder, small)
    project = services.projects.create("Alpha")
    yield Env(store, services, text, vector, embedder, indexing, small, project.id)


def _text_source(env: Env, text: str = TEXT) -> UUID:
    return env.sources.add_text(env.project_id, "Notes", text).id


def test_first_version_is_published_to_both_indexes(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]

    env.index(source_id, version)

    source = env.sources.get(env.project_id, source_id)
    published = env.versions(source_id)[-1]
    assert source.status is SourceStatus.INDEXED
    assert source.current_version == 1
    assert source.error_message is None
    assert published.status is VersionStatus.PUBLISHED
    assert published.vector_indexed is True
    chunks = env.chunks_of(version.id)
    assert len(chunks) > 1
    assert set(env.text.docs) == {c.id for c in chunks}
    assert set(env.vector.points) == {c.id for c in chunks}


def test_new_version_replaces_previous_documents(env: Env):
    source_id = _text_source(env)
    first = env.versions(source_id)[-1]
    env.index(source_id, first)
    old_chunk_ids = {c.id for c in env.chunks_of(first.id)}

    env.sources.edit(env.project_id, source_id, text="rewritten content for version two")
    second = env.versions(source_id)[-1]
    env.index(source_id, second)

    new_chunk_ids = {c.id for c in env.chunks_of(second.id)}
    assert env.chunks_of(first.id) == []
    assert old_chunk_ids.isdisjoint(env.text.docs)
    assert old_chunk_ids.isdisjoint(env.vector.points)
    assert set(env.text.docs) == new_chunk_ids
    assert set(env.vector.points) == new_chunk_ids
    assert env.sources.get(env.project_id, source_id).current_version == 2


def test_null_embedder_publishes_text_only(env: Env):
    env.indexing = IndexingService(
        memory_indexing_scope(env.store), env.text, env.vector, NullEmbedder(), env.settings
    )
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]

    env.index(source_id, version)

    published = env.versions(source_id)[-1]
    assert published.status is VersionStatus.PUBLISHED
    assert published.vector_indexed is False
    assert env.vector.points == {}
    assert len(env.text.docs) == len(env.chunks_of(version.id))


def test_text_index_outage_fails_version_and_retry_publishes(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]
    env.text.available = False

    with pytest.raises(IndexBackendError):
        env.index(source_id, version, final_attempt=True)

    source = env.sources.get(env.project_id, source_id)
    failed = env.versions(source_id)[-1]
    assert failed.status is VersionStatus.FAILED
    assert failed.error_message
    assert source.status is SourceStatus.FAILED
    assert source.error_message == failed.error_message
    assert source.current_version is None
    assert env.chunks_of(version.id) == []

    env.text.available = True
    env.index(source_id, version)

    assert env.versions(source_id)[-1].status is VersionStatus.PUBLISHED
    source = env.sources.get(env.project_id, source_id)
    assert source.status is SourceStatus.INDEXED
    assert source.error_message is None
    assert source.current_version == 1


def test_text_index_outage_before_last_attempt_keeps_version_pending(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]
    env.text.available = False

    with pytest.raises(IndexBackendError):
        env.index(source_id, version, final_attempt=False)

    source = env.sources.get(env.project_id, source_id)
    pending = env.versions(source_id)[-1]
    assert pending.status is VersionStatus.PENDING
    assert pending.error_message
    assert source.status is SourceStatus.PENDING
    assert source.error_message == pending.error_message
    assert source.current_version is None
    assert env.chunks_of(version.id) == []

    env.text.available = True
    env.index(source_id, version)

    assert env.versions(source_id)[-1].status is VersionStatus.PUBLISHED
    source = env.sources.get(env.project_id, source_id)
    assert source.status is SourceStatus.INDEXED
    assert source.error_message is None
    assert source.current_version == 1


def test_embedder_outage_keeps_previous_version_published(env: Env):
    source_id = _text_source(env)
    first = env.versions(source_id)[-1]
    env.index(source_id, first)
    env.sources.edit(env.project_id, source_id, text="second version text")
    second = env.versions(source_id)[-1]
    env.embedder.available = False

    with pytest.raises(IndexBackendError):
        env.index(source_id, second)

    source = env.sources.get(env.project_id, source_id)
    assert source.current_version == 1
    assert source.status is SourceStatus.FAILED
    assert env.versions(source_id)[0].status is VersionStatus.PUBLISHED
    assert env.chunks_of(first.id)
    assert env.versions(source_id)[-1].status is VersionStatus.FAILED

    env.embedder.available = True
    env.index(source_id, second)

    assert env.sources.get(env.project_id, source_id).current_version == 2


def test_superseded_version_is_not_published(env: Env):
    source_id = _text_source(env)
    first = env.versions(source_id)[-1]
    env.sources.edit(env.project_id, source_id, text="newer text for version two")
    second = env.versions(source_id)[-1]
    env.index(source_id, second)

    env.index(source_id, first)

    stale = env.versions(source_id)[0]
    assert stale.status is VersionStatus.FAILED
    assert stale.error_message == "superseded by version 2"
    source = env.sources.get(env.project_id, source_id)
    assert source.current_version == 2
    assert source.status is SourceStatus.INDEXED
    assert env.chunks_of(first.id) == []
    assert set(env.text.docs) == {c.id for c in env.chunks_of(second.id)}


def test_indexing_deleted_source_is_skipped(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]
    env.sources.delete(env.project_id, source_id)

    env.index(source_id, version)

    assert env.text.docs == {}
    assert env.vector.points == {}


def test_repeated_index_call_is_idempotent(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]
    env.index(source_id, version)
    before = set(env.text.docs)

    env.index(source_id, version)

    assert set(env.text.docs) == before
    assert env.sources.get(env.project_id, source_id).current_version == 1


def test_reindex_project_restores_lost_documents(env: Env):
    source_id = _text_source(env)
    version = env.versions(source_id)[-1]
    env.index(source_id, version)
    expected = set(env.text.docs)
    env.text.docs.clear()
    env.vector.points.clear()

    env.indexing.reindex_project(env.project_id)

    assert set(env.text.docs) == expected
    assert set(env.vector.points) == expected
    assert env.versions(source_id)[-1].vector_indexed is True


def test_purge_source_removes_documents_from_both_indexes(env: Env):
    source_id = _text_source(env)
    env.index(source_id, env.versions(source_id)[-1])

    env.indexing.purge_source(source_id)

    assert env.text.docs == {}
    assert env.vector.points == {}
