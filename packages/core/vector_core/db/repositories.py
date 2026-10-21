from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from vector_core.db.tables import (
    ApiKeyRow,
    ChunkRow,
    JobRow,
    ProjectRow,
    SessionRow,
    SourceRow,
    SourceVersionRow,
)
from vector_core.domain import (
    ApiKey,
    Chunk,
    Conflict,
    Job,
    JobStatus,
    Project,
    Session,
    Source,
    SourceKind,
    SourceStatus,
    SourceVersion,
    VersionStatus,
)


def _flush(session: DbSession) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise Conflict("conflicting record already exists") from exc


def _to_project(row: ProjectRow) -> Project:
    return Project(id=row.id, name=row.name, created_at=row.created_at)


def _to_api_key(row: ApiKeyRow) -> ApiKey:
    return ApiKey(
        id=row.id,
        project_id=row.project_id,
        prefix=row.prefix,
        key_hash=row.key_hash,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _to_session(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def _to_job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        project_id=row.project_id,
        type=row.type,
        payload=dict(row.payload),
        status=JobStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        available_at=row.available_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        leased_until=row.leased_until,
        leased_by=row.leased_by,
        last_error=row.last_error,
    )


def _to_source(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        project_id=row.project_id,
        kind=SourceKind(row.kind),
        title=row.title,
        status=SourceStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        current_version=row.current_version,
        origin_key=row.origin_key,
        sync_root=row.sync_root,
        content_type=row.content_type,
        error_message=row.error_message,
    )


def _to_version(row: SourceVersionRow) -> SourceVersion:
    return SourceVersion(
        id=row.id,
        source_id=row.source_id,
        version=row.version,
        status=VersionStatus(row.status),
        created_at=row.created_at,
        storage_key=row.storage_key,
        content_hash=row.content_hash,
        extracted_text=row.extracted_text,
        extracted_chars=row.extracted_chars,
        vector_indexed=row.vector_indexed,
        error_message=row.error_message,
    )


def _to_chunk(row: ChunkRow) -> Chunk:
    return Chunk(
        id=row.id,
        project_id=row.project_id,
        source_id=row.source_id,
        version_id=row.version_id,
        ordinal=row.ordinal,
        text=row.text,
        locator=dict(row.locator),
    )


class SqlProjectRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, project: Project) -> None:
        self._session.add(ProjectRow(id=project.id, name=project.name, created_at=project.created_at))
        _flush(self._session)

    def get(self, project_id: UUID) -> Project | None:
        row = self._session.get(ProjectRow, project_id)
        return _to_project(row) if row is not None else None

    def get_by_name(self, name: str) -> Project | None:
        row = self._session.scalar(select(ProjectRow).where(ProjectRow.name == name))
        return _to_project(row) if row is not None else None

    def list_all(self) -> list[Project]:
        rows = self._session.scalars(select(ProjectRow).order_by(ProjectRow.created_at, ProjectRow.name))
        return [_to_project(row) for row in rows]

    def rename(self, project_id: UUID, name: str) -> None:
        row = self._session.get(ProjectRow, project_id)
        if row is not None:
            row.name = name
            _flush(self._session)

    def delete(self, project_id: UUID) -> bool:
        row = self._session.get(ProjectRow, project_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class SqlApiKeyRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, key: ApiKey) -> None:
        self._session.add(
            ApiKeyRow(
                id=key.id,
                project_id=key.project_id,
                prefix=key.prefix,
                key_hash=key.key_hash,
                created_at=key.created_at,
                revoked_at=key.revoked_at,
            )
        )
        _flush(self._session)

    def get(self, key_id: UUID) -> ApiKey | None:
        row = self._session.get(ApiKeyRow, key_id)
        return _to_api_key(row) if row is not None else None

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        row = self._session.scalar(select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash))
        return _to_api_key(row) if row is not None else None

    def list_by_project(self, project_id: UUID) -> list[ApiKey]:
        rows = self._session.scalars(
            select(ApiKeyRow).where(ApiKeyRow.project_id == project_id).order_by(ApiKeyRow.created_at)
        )
        return [_to_api_key(row) for row in rows]

    def set_revoked_at(self, key_id: UUID, revoked_at: datetime) -> None:
        row = self._session.get(ApiKeyRow, key_id)
        if row is not None:
            row.revoked_at = revoked_at
            self._session.flush()


class SqlSessionRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, session: Session) -> None:
        self._session.add(
            SessionRow(
                id=session.id,
                token_hash=session.token_hash,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )
        _flush(self._session)

    def get_by_hash(self, token_hash: str) -> Session | None:
        row = self._session.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash))
        return _to_session(row) if row is not None else None

    def delete_by_hash(self, token_hash: str) -> None:
        row = self._session.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash))
        if row is not None:
            self._session.delete(row)
            self._session.flush()

    def delete_expired(self, now: datetime) -> int:
        result = self._session.execute(delete(SessionRow).where(SessionRow.expires_at <= now))
        return result.rowcount


class SqlJobRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, job: Job) -> None:
        self._session.add(
            JobRow(
                id=job.id,
                project_id=job.project_id,
                type=job.type,
                payload=job.payload,
                status=job.status.value,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                available_at=job.available_at,
                leased_until=job.leased_until,
                leased_by=job.leased_by,
                last_error=job.last_error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
        _flush(self._session)

    def get(self, job_id: UUID) -> Job | None:
        row = self._session.get(JobRow, job_id)
        return _to_job(row) if row is not None else None

    def claim_one(self, worker_id: str, now: datetime, leased_until: datetime) -> Job | None:
        row = self._session.scalar(
            select(JobRow)
            .where(JobRow.status == JobStatus.PENDING.value, JobRow.available_at <= now)
            .order_by(JobRow.available_at, JobRow.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        row.status = JobStatus.LEASED.value
        row.leased_by = worker_id
        row.leased_until = leased_until
        row.updated_at = now
        self._session.flush()
        return _to_job(row)

    def lock_stale(self, now: datetime, limit: int) -> list[Job]:
        rows = self._session.scalars(
            select(JobRow)
            .where(JobRow.status == JobStatus.LEASED.value, JobRow.leased_until < now)
            .order_by(JobRow.leased_until)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [_to_job(row) for row in rows]

    def update_leased(self, job: Job, expected_worker_id: str) -> bool:
        result = self._session.execute(
            update(JobRow)
            .where(
                JobRow.id == job.id,
                JobRow.status == JobStatus.LEASED.value,
                JobRow.leased_by == expected_worker_id,
            )
            .values(
                status=job.status.value,
                attempts=job.attempts,
                available_at=job.available_at,
                leased_until=job.leased_until,
                leased_by=job.leased_by,
                last_error=job.last_error,
                updated_at=job.updated_at,
            )
        )
        return result.rowcount == 1

    def extend_lease(self, job_id: UUID, expected_worker_id: str, leased_until: datetime) -> bool:
        result = self._session.execute(
            update(JobRow)
            .where(
                JobRow.id == job_id,
                JobRow.status == JobStatus.LEASED.value,
                JobRow.leased_by == expected_worker_id,
            )
            .values(leased_until=leased_until)
        )
        return result.rowcount == 1


class SqlSourceRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, source: Source) -> None:
        self._session.add(
            SourceRow(
                id=source.id,
                project_id=source.project_id,
                kind=source.kind.value,
                title=source.title,
                status=source.status.value,
                current_version=source.current_version,
                origin_key=source.origin_key,
                sync_root=source.sync_root,
                content_type=source.content_type,
                error_message=source.error_message,
                created_at=source.created_at,
                updated_at=source.updated_at,
            )
        )
        _flush(self._session)

    def get(self, source_id: UUID) -> Source | None:
        row = self._session.get(SourceRow, source_id)
        return _to_source(row) if row is not None else None

    def get_for_update(self, source_id: UUID) -> Source | None:
        row = self._session.scalar(select(SourceRow).where(SourceRow.id == source_id).with_for_update())
        return _to_source(row) if row is not None else None

    def get_by_origin(self, project_id: UUID, origin_key: str) -> Source | None:
        row = self._session.scalar(
            select(SourceRow).where(SourceRow.project_id == project_id, SourceRow.origin_key == origin_key)
        )
        return _to_source(row) if row is not None else None

    def list_by_project(self, project_id: UUID) -> list[Source]:
        rows = self._session.scalars(
            select(SourceRow)
            .where(SourceRow.project_id == project_id)
            .order_by(SourceRow.created_at, SourceRow.id)
        )
        return [_to_source(row) for row in rows]

    def list_by_sync_root(self, project_id: UUID, sync_root: str) -> list[Source]:
        rows = self._session.scalars(
            select(SourceRow)
            .where(SourceRow.project_id == project_id, SourceRow.sync_root == sync_root)
            .order_by(SourceRow.created_at, SourceRow.id)
        )
        return [_to_source(row) for row in rows]

    def update(self, source: Source) -> None:
        row = self._session.get(SourceRow, source.id)
        if row is not None:
            row.kind = source.kind.value
            row.title = source.title
            row.status = source.status.value
            row.current_version = source.current_version
            row.origin_key = source.origin_key
            row.sync_root = source.sync_root
            row.content_type = source.content_type
            row.error_message = source.error_message
            row.updated_at = source.updated_at
            _flush(self._session)

    def delete(self, source_id: UUID) -> bool:
        row = self._session.get(SourceRow, source_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class SqlVersionRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add(self, version: SourceVersion) -> None:
        self._session.add(
            SourceVersionRow(
                id=version.id,
                source_id=version.source_id,
                version=version.version,
                status=version.status.value,
                storage_key=version.storage_key,
                content_hash=version.content_hash,
                extracted_text=version.extracted_text,
                extracted_chars=version.extracted_chars,
                vector_indexed=version.vector_indexed,
                error_message=version.error_message,
                created_at=version.created_at,
            )
        )
        _flush(self._session)

    def get(self, version_id: UUID) -> SourceVersion | None:
        row = self._session.get(SourceVersionRow, version_id)
        return _to_version(row) if row is not None else None

    def list_by_source(self, source_id: UUID) -> list[SourceVersion]:
        rows = self._session.scalars(
            select(SourceVersionRow)
            .where(SourceVersionRow.source_id == source_id)
            .order_by(SourceVersionRow.version)
        )
        return [_to_version(row) for row in rows]

    def get_by_number(self, source_id: UUID, version: int) -> SourceVersion | None:
        row = self._session.scalar(
            select(SourceVersionRow).where(
                SourceVersionRow.source_id == source_id,
                SourceVersionRow.version == version,
            )
        )
        return _to_version(row) if row is not None else None

    def next_version(self, source_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(SourceVersionRow.version)).where(SourceVersionRow.source_id == source_id)
        )
        return (current or 0) + 1

    def update(self, version: SourceVersion) -> None:
        row = self._session.get(SourceVersionRow, version.id)
        if row is not None:
            row.status = version.status.value
            row.storage_key = version.storage_key
            row.content_hash = version.content_hash
            row.extracted_text = version.extracted_text
            row.extracted_chars = version.extracted_chars
            row.vector_indexed = version.vector_indexed
            row.error_message = version.error_message
            _flush(self._session)


class SqlChunkRepository:
    def __init__(self, session: DbSession) -> None:
        self._session = session

    def add_many(self, chunks: list[Chunk]) -> None:
        self._session.add_all(
            ChunkRow(
                id=chunk.id,
                project_id=chunk.project_id,
                source_id=chunk.source_id,
                version_id=chunk.version_id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                locator=chunk.locator,
            )
            for chunk in chunks
        )
        _flush(self._session)

    def delete_by_version(self, version_id: UUID) -> None:
        self._session.execute(delete(ChunkRow).where(ChunkRow.version_id == version_id))
        self._session.flush()

    def delete_by_source(self, source_id: UUID) -> None:
        self._session.execute(delete(ChunkRow).where(ChunkRow.source_id == source_id))
        self._session.flush()

    def list_by_version(self, version_id: UUID) -> list[Chunk]:
        rows = self._session.scalars(
            select(ChunkRow).where(ChunkRow.version_id == version_id).order_by(ChunkRow.ordinal)
        )
        return [_to_chunk(row) for row in rows]

    def list_published_by_project(self, project_id: UUID) -> list[Chunk]:
        rows = self._session.scalars(
            select(ChunkRow)
            .join(SourceVersionRow, ChunkRow.version_id == SourceVersionRow.id)
            .where(
                ChunkRow.project_id == project_id,
                SourceVersionRow.status == VersionStatus.PUBLISHED.value,
            )
            .order_by(ChunkRow.source_id, ChunkRow.ordinal)
        )
        return [_to_chunk(row) for row in rows]

    def get_many(self, chunk_ids: list[UUID]) -> list[Chunk]:
        if not chunk_ids:
            return []
        rows = self._session.scalars(select(ChunkRow).where(ChunkRow.id.in_(chunk_ids)))
        return [_to_chunk(row) for row in rows]
