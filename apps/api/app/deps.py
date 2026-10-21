from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DbSession

from vector_core.blobs import FsBlobStore
from vector_core.config import Settings
from vector_core.db.engine import create_db_engine
from vector_core.db.repositories import (
    SqlApiKeyRepository,
    SqlChunkRepository,
    SqlJobRepository,
    SqlProjectRepository,
    SqlSessionRepository,
    SqlSourceRepository,
    SqlVersionRepository,
)
from vector_core.domain import Principal, PrincipalKind, Unauthorized
from vector_core.embeddings import build_embedder
from vector_core.extract import StandardContentExtractor, build_link_fetcher
from vector_core.indexes import build_indexes
from vector_core.ports import BlobStore, ContentExtractor, Embedder, TextIndex, VectorIndex
from vector_core.services import (
    ApiKeyService,
    AuthService,
    JobQueueService,
    ProjectService,
    SearchService,
    SourceService,
)
from vector_core.services.common import TransactionHooks

bearer = HTTPBearer(auto_error=False)
MISSING_CREDENTIALS = "authentication required"


@dataclass(frozen=True)
class Services:
    projects: ProjectService
    keys: ApiKeyService
    auth: AuthService
    jobs: JobQueueService
    sources: SourceService
    search: SearchService


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_engine() -> Engine:
    return create_db_engine(get_settings().database_url)


def get_hooks() -> TransactionHooks:
    """Per-request hooks; FastAPI caches the instance so session and services share it."""
    return TransactionHooks()


def get_session(
    engine: Engine = Depends(get_engine), hooks: TransactionHooks = Depends(get_hooks)
) -> Iterator[DbSession]:
    with DbSession(engine) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            try:
                session.rollback()
            finally:
                hooks.rollback()
            raise
        hooks.commit()


def get_blob_store(settings: Settings = Depends(get_settings)) -> BlobStore:
    return FsBlobStore(settings.blob_dir)


def get_extractor() -> ContentExtractor:
    return StandardContentExtractor()


@lru_cache
def get_indexes() -> tuple[VectorIndex, TextIndex]:
    return build_indexes(get_settings())


def get_vector_index() -> VectorIndex:
    return get_indexes()[0]


def get_text_index() -> TextIndex:
    return get_indexes()[1]


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())


def get_services(
    session: DbSession = Depends(get_session, scope="function"),
    settings: Settings = Depends(get_settings),
    blobs: BlobStore = Depends(get_blob_store),
    extractor: ContentExtractor = Depends(get_extractor),
    hooks: TransactionHooks = Depends(get_hooks),
    vector_index: VectorIndex = Depends(get_vector_index),
    text_index: TextIndex = Depends(get_text_index),
    embedder: Embedder = Depends(get_embedder),
) -> Services:
    return build_request_services(
        session, settings, blobs, extractor, hooks, vector_index, text_index, embedder
    )


def build_request_services(
    session: DbSession,
    settings: Settings,
    blobs: BlobStore,
    extractor: ContentExtractor,
    hooks: TransactionHooks,
    vector_index: VectorIndex,
    text_index: TextIndex,
    embedder: Embedder,
) -> Services:
    projects = SqlProjectRepository(session)
    jobs = JobQueueService(
        SqlJobRepository(session),
        projects,
        default_max_attempts=settings.job_max_attempts,
        backoff_base_seconds=settings.job_backoff_base_seconds,
        backoff_max_seconds=settings.job_backoff_max_seconds,
    )
    return Services(
        projects=ProjectService(projects, blobs, hooks, text_index, vector_index),
        keys=ApiKeyService(SqlApiKeyRepository(session), projects),
        auth=AuthService(settings, SqlSessionRepository(session)),
        jobs=jobs,
        sources=SourceService(
            settings,
            SqlSourceRepository(session),
            SqlVersionRepository(session),
            SqlChunkRepository(session),
            projects,
            blobs,
            extractor,
            jobs,
            build_link_fetcher(settings),
            hooks=hooks,
            text_index=text_index,
            vector_index=vector_index,
        ),
        search=SearchService(
            settings,
            projects,
            SqlSourceRepository(session),
            SqlVersionRepository(session),
            SqlChunkRepository(session),
            text_index,
            vector_index,
            embedder,
        ),
    )


def require_owner(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    services: Services = Depends(get_services),
) -> str:
    if credentials is None:
        raise Unauthorized(MISSING_CREDENTIALS)
    services.auth.authenticate_session(credentials.credentials)
    return credentials.credentials


def _resolve_principal(
    credentials: HTTPAuthorizationCredentials | None,
    api_key: str | None,
    services: Services,
) -> Principal:
    if api_key is not None:
        return Principal(PrincipalKind.API_KEY, services.keys.authenticate(api_key))
    if credentials is None:
        raise Unauthorized(MISSING_CREDENTIALS)
    agent = services.auth.agent_principal(credentials.credentials)
    if agent is not None:
        return agent
    services.auth.authenticate_session(credentials.credentials)
    return Principal(PrincipalKind.OWNER)


def require_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    api_key: str | None = Header(default=None, alias="X-Api-Key"),
    services: Services = Depends(get_services),
) -> Principal:
    return _resolve_principal(credentials, api_key, services)


def require_project_access(
    project_id: UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    api_key: str | None = Header(default=None, alias="X-Api-Key"),
    services: Services = Depends(get_services),
) -> UUID:
    principal = _resolve_principal(credentials, api_key, services)
    services.auth.authorize(project_id, principal)
    return project_id
