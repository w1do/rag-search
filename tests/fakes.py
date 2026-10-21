import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import httpx

from app.deps import Services
from vector_core.config import Settings
from vector_core.domain import (
    ApiKey,
    Chunk,
    Conflict,
    Job,
    JobStatus,
    NotFound,
    Project,
    Session,
    Source,
    SourceVersion,
    VersionStatus,
)
from vector_core.embeddings import NullEmbedder
from vector_core.extract import StandardContentExtractor, build_link_fetcher
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.ports import Embedder, TextIndex, VectorIndex
from vector_core.services import (
    ApiKeyService,
    AuthService,
    IndexingRepos,
    IndexingScope,
    JobQueueService,
    ProjectService,
    SearchService,
    SourceService,
)
from vector_core.services.common import TransactionHooks


class InMemoryStore:
    def __init__(self) -> None:
        self.projects: dict[UUID, Project] = {}
        self.keys: dict[UUID, ApiKey] = {}
        self.sessions: dict[str, Session] = {}
        self.jobs: dict[UUID, Job] = {}
        self.sources: dict[UUID, Source] = {}
        self.versions: dict[UUID, SourceVersion] = {}
        self.chunks: dict[UUID, Chunk] = {}


def _delete_source_tree(store: InMemoryStore, source_id: UUID) -> None:
    store.sources.pop(source_id, None)
    for version_id in [
        v.id for v in store.versions.values() if v.source_id == source_id
    ]:
        del store.versions[version_id]
    for chunk_id in [c.id for c in store.chunks.values() if c.source_id == source_id]:
        del store.chunks[chunk_id]


class InMemoryProjectRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, project: Project) -> None:
        self._store.projects[project.id] = project

    def get(self, project_id: UUID) -> Project | None:
        return self._store.projects.get(project_id)

    def get_by_name(self, name: str) -> Project | None:
        return next((p for p in self._store.projects.values() if p.name == name), None)

    def list_all(self) -> list[Project]:
        return sorted(
            self._store.projects.values(), key=lambda p: (p.created_at, p.name)
        )

    def rename(self, project_id: UUID, name: str) -> None:
        self._store.projects[project_id] = replace(
            self._store.projects[project_id], name=name
        )

    def delete(self, project_id: UUID) -> bool:
        if self._store.projects.pop(project_id, None) is None:
            return False
        for key_id in [
            key.id for key in self._store.keys.values() if key.project_id == project_id
        ]:
            del self._store.keys[key_id]
        for source_id in [
            s.id for s in self._store.sources.values() if s.project_id == project_id
        ]:
            _delete_source_tree(self._store, source_id)
        return True


class InMemoryApiKeyRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, key: ApiKey) -> None:
        self._store.keys[key.id] = key

    def get(self, key_id: UUID) -> ApiKey | None:
        return self._store.keys.get(key_id)

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        return next(
            (k for k in self._store.keys.values() if k.key_hash == key_hash), None
        )

    def list_by_project(self, project_id: UUID) -> list[ApiKey]:
        keys = [k for k in self._store.keys.values() if k.project_id == project_id]
        return sorted(keys, key=lambda k: k.created_at)

    def set_revoked_at(self, key_id: UUID, revoked_at: datetime) -> None:
        self._store.keys[key_id] = replace(
            self._store.keys[key_id], revoked_at=revoked_at
        )


class InMemorySessionRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, session: Session) -> None:
        self._store.sessions[session.token_hash] = session

    def get_by_hash(self, token_hash: str) -> Session | None:
        return self._store.sessions.get(token_hash)

    def delete_by_hash(self, token_hash: str) -> None:
        self._store.sessions.pop(token_hash, None)

    def delete_expired(self, now: datetime) -> int:
        expired = [h for h, s in self._store.sessions.items() if s.expires_at <= now]
        for token_hash in expired:
            del self._store.sessions[token_hash]
        return len(expired)


class InMemoryJobRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, job: Job) -> None:
        self._store.jobs[job.id] = job

    def get(self, job_id: UUID) -> Job | None:
        return self._store.jobs.get(job_id)

    def claim_one(
        self, worker_id: str, now: datetime, leased_until: datetime
    ) -> Job | None:
        pending = [
            j
            for j in self._store.jobs.values()
            if j.status is JobStatus.PENDING and j.available_at <= now
        ]
        if not pending:
            return None
        oldest = min(pending, key=lambda j: (j.available_at, j.created_at))
        leased = replace(
            oldest,
            status=JobStatus.LEASED,
            leased_by=worker_id,
            leased_until=leased_until,
            updated_at=now,
        )
        self._store.jobs[leased.id] = leased
        return leased

    def lock_stale(self, now: datetime, limit: int) -> list[Job]:
        stale = [
            j
            for j in self._store.jobs.values()
            if j.status is JobStatus.LEASED and j.leased_until < now
        ]
        return sorted(stale, key=lambda j: j.leased_until)[:limit]

    def update_leased(self, job: Job, expected_worker_id: str) -> bool:
        current = self._store.jobs.get(job.id)
        if current is None or current.status is not JobStatus.LEASED:
            return False
        if current.leased_by != expected_worker_id:
            return False
        self._store.jobs[job.id] = job
        return True

    def extend_lease(
        self, job_id: UUID, expected_worker_id: str, leased_until: datetime
    ) -> bool:
        current = self._store.jobs.get(job_id)
        if current is None or current.status is not JobStatus.LEASED:
            return False
        if current.leased_by != expected_worker_id:
            return False
        self._store.jobs[job_id] = replace(current, leased_until=leased_until)
        return True


class InMemorySourceRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, source: Source) -> None:
        if source.origin_key is not None and self.get_by_origin(
            source.project_id, source.origin_key
        ):
            raise Conflict("conflicting record already exists")
        self._store.sources[source.id] = source

    def get(self, source_id: UUID) -> Source | None:
        return self._store.sources.get(source_id)

    def get_for_update(self, source_id: UUID) -> Source | None:
        return self._store.sources.get(source_id)

    def get_by_origin(self, project_id: UUID, origin_key: str) -> Source | None:
        return next(
            (
                s
                for s in self._store.sources.values()
                if s.project_id == project_id and s.origin_key == origin_key
            ),
            None,
        )

    def list_by_project(self, project_id: UUID) -> list[Source]:
        sources = [
            s for s in self._store.sources.values() if s.project_id == project_id
        ]
        return sorted(sources, key=lambda s: (s.created_at, s.id))

    def list_by_sync_root(self, project_id: UUID, sync_root: str) -> list[Source]:
        sources = [
            s
            for s in self._store.sources.values()
            if s.project_id == project_id and s.sync_root == sync_root
        ]
        return sorted(sources, key=lambda s: (s.created_at, s.id))

    def update(self, source: Source) -> None:
        if source.id in self._store.sources:
            self._store.sources[source.id] = source

    def delete(self, source_id: UUID) -> bool:
        if source_id not in self._store.sources:
            return False
        _delete_source_tree(self._store, source_id)
        return True


class InMemoryVersionRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, version: SourceVersion) -> None:
        if self.get_by_number(version.source_id, version.version) is not None:
            raise Conflict("conflicting record already exists")
        self._store.versions[version.id] = version

    def get(self, version_id: UUID) -> SourceVersion | None:
        return self._store.versions.get(version_id)

    def list_by_source(self, source_id: UUID) -> list[SourceVersion]:
        versions = [
            v for v in self._store.versions.values() if v.source_id == source_id
        ]
        return sorted(versions, key=lambda v: v.version)

    def get_by_number(self, source_id: UUID, version: int) -> SourceVersion | None:
        return next(
            (
                v
                for v in self._store.versions.values()
                if v.source_id == source_id and v.version == version
            ),
            None,
        )

    def next_version(self, source_id: UUID) -> int:
        numbers = [
            v.version for v in self._store.versions.values() if v.source_id == source_id
        ]
        return max(numbers, default=0) + 1

    def update(self, version: SourceVersion) -> None:
        if version.id in self._store.versions:
            self._store.versions[version.id] = version


class InMemoryChunkRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add_many(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            if any(
                c.version_id == chunk.version_id and c.ordinal == chunk.ordinal
                for c in self._store.chunks.values()
            ):
                raise Conflict("conflicting record already exists")
            self._store.chunks[chunk.id] = chunk

    def delete_by_version(self, version_id: UUID) -> None:
        for chunk_id in [
            c.id for c in self._store.chunks.values() if c.version_id == version_id
        ]:
            del self._store.chunks[chunk_id]

    def delete_by_source(self, source_id: UUID) -> None:
        for chunk_id in [
            c.id for c in self._store.chunks.values() if c.source_id == source_id
        ]:
            del self._store.chunks[chunk_id]

    def list_by_version(self, version_id: UUID) -> list[Chunk]:
        chunks = [c for c in self._store.chunks.values() if c.version_id == version_id]
        return sorted(chunks, key=lambda c: c.ordinal)

    def list_published_by_project(self, project_id: UUID) -> list[Chunk]:
        published = {
            v.id
            for v in self._store.versions.values()
            if v.status is VersionStatus.PUBLISHED
        }
        chunks = [
            c
            for c in self._store.chunks.values()
            if c.project_id == project_id and c.version_id in published
        ]
        return sorted(chunks, key=lambda c: (c.source_id, c.ordinal))

    def get_many(self, chunk_ids: list[UUID]) -> list[Chunk]:
        return [self._store.chunks[i] for i in chunk_ids if i in self._store.chunks]


class InMemoryBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    def get(self, key: str) -> bytes:
        if key not in self.blobs:
            raise NotFound("blob not found")
        return self.blobs[key]

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        directory = prefix.rstrip("/") + "/"
        for key in [k for k in self.blobs if k.startswith(directory)]:
            del self.blobs[key]

    def exists(self, key: str) -> bool:
        return key in self.blobs


class FakeEmbedder:
    def __init__(self, dim: int = 8, delay: float = 0.0) -> None:
        self.dim: int | None = dim
        self._delay = delay

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if self._delay:
            time.sleep(self._delay)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [byte / 255.0 for byte in digest[: self.dim]]


def memory_indexing_scope(store: InMemoryStore) -> IndexingScope:
    @contextmanager
    def scope() -> Iterator[IndexingRepos]:
        yield IndexingRepos(
            InMemorySourceRepository(store),
            InMemoryVersionRepository(store),
            InMemoryChunkRepository(store),
        )

    return scope


def build_services(
    settings: Settings,
    store: InMemoryStore,
    blobs: InMemoryBlobStore | None = None,
    transport: httpx.BaseTransport | None = None,
    hooks: TransactionHooks | None = None,
    text_index: TextIndex | None = None,
    vector_index: VectorIndex | None = None,
    embedder: Embedder | None = None,
) -> Services:
    projects = InMemoryProjectRepository(store)
    blob_store = blobs if blobs is not None else InMemoryBlobStore()
    transaction_hooks = hooks if hooks is not None else TransactionHooks(immediate=True)
    text = text_index if text_index is not None else InMemoryTextIndex()
    vector = vector_index if vector_index is not None else InMemoryVectorIndex()
    embeddings = embedder if embedder is not None else NullEmbedder()
    jobs = JobQueueService(
        InMemoryJobRepository(store),
        projects,
        default_max_attempts=settings.job_max_attempts,
    )
    return Services(
        projects=ProjectService(projects, blob_store, transaction_hooks, text, vector),
        keys=ApiKeyService(InMemoryApiKeyRepository(store), projects),
        auth=AuthService(settings, InMemorySessionRepository(store)),
        jobs=jobs,
        sources=SourceService(
            settings,
            InMemorySourceRepository(store),
            InMemoryVersionRepository(store),
            InMemoryChunkRepository(store),
            projects,
            blob_store,
            StandardContentExtractor(),
            jobs,
            build_link_fetcher(settings, transport=transport),
            hooks=transaction_hooks,
            text_index=text,
            vector_index=vector,
        ),
        search=SearchService(
            settings,
            projects,
            InMemorySourceRepository(store),
            InMemoryVersionRepository(store),
            InMemoryChunkRepository(store),
            text,
            vector,
            embeddings,
        ),
    )
