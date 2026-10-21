from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DbSession

from vector_core.config import Settings
from vector_core.db.repositories import (
    SqlChunkRepository,
    SqlSourceRepository,
    SqlVersionRepository,
)
from vector_core.domain import Job
from vector_core.embeddings import build_embedder
from vector_core.indexes import build_indexes
from vector_core.services import (
    INDEX_VERSION_JOB,
    PURGE_SOURCE_JOB,
    REINDEX_PROJECT_JOB,
    IndexingRepos,
    IndexingScope,
    IndexingService,
)

Handler = Callable[[Job], None]


def noop_handler(job: Job) -> None:
    return None


def sql_indexing_scope(engine: Engine) -> IndexingScope:
    @contextmanager
    def scope() -> Iterator[IndexingRepos]:
        with DbSession(engine) as session:
            yield IndexingRepos(
                SqlSourceRepository(session),
                SqlVersionRepository(session),
                SqlChunkRepository(session),
            )
            session.commit()

    return scope


def _payload_uuid(job: Job, key: str) -> UUID:
    return UUID(str(job.payload[key]))


def _final_attempt(job: Job) -> bool:
    return job.attempts + 1 >= job.max_attempts


def build_handlers(service: IndexingService) -> Mapping[str, Handler]:
    return {
        "noop": noop_handler,
        INDEX_VERSION_JOB: lambda job: service.index_version(
            job.project_id,
            _payload_uuid(job, "source_id"),
            _payload_uuid(job, "version_id"),
            final_attempt=_final_attempt(job),
        ),
        REINDEX_PROJECT_JOB: lambda job: service.reindex_project(job.project_id),
        PURGE_SOURCE_JOB: lambda job: service.purge_source(_payload_uuid(job, "source_id")),
    }


def build_default_handlers(engine: Engine, settings: Settings) -> Mapping[str, Handler]:
    vector_index, text_index = build_indexes(settings)
    service = IndexingService(
        sql_indexing_scope(engine),
        text_index,
        vector_index,
        build_embedder(settings),
        settings,
    )
    return build_handlers(service)
