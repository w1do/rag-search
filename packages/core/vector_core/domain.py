from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


def utcnow() -> datetime:
    return datetime.now(UTC)


class DomainError(Exception):
    status_code = 500
    code = "internal_error"


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class AuthNotConfigured(DomainError):
    status_code = 503
    code = "auth_not_configured"


class PayloadTooLarge(DomainError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedFormat(DomainError):
    status_code = 422
    code = "unsupported_format"


class ServiceUnavailable(DomainError):
    status_code = 503
    code = "db_unavailable"

    def __init__(self, message: str = "service unavailable", code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class PrincipalKind(StrEnum):
    OWNER = "owner"
    AGENT = "agent"
    API_KEY = "api_key"


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    project_id: UUID | None = None


@dataclass(frozen=True)
class Project:
    id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True)
class ApiKey:
    id: UUID
    project_id: UUID
    prefix: str
    key_hash: str = field(repr=False)
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def status(self) -> str:
        return "revoked" if self.revoked_at is not None else "active"


@dataclass(frozen=True)
class ApiKeyIssued:
    id: UUID
    project_id: UUID
    prefix: str
    secret: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True)
class Session:
    id: UUID
    token_hash: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


class JobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    id: UUID
    project_id: UUID
    type: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    leased_until: datetime | None = None
    leased_by: str | None = None
    last_error: str | None = None


class SourceKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    URL = "url"


class SourceStatus(StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


class VersionStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True)
class Source:
    id: UUID
    project_id: UUID
    kind: SourceKind
    title: str
    status: SourceStatus
    created_at: datetime
    updated_at: datetime
    current_version: int | None = None
    origin_key: str | None = None
    sync_root: str | None = None
    content_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class SourceVersion:
    id: UUID
    source_id: UUID
    version: int
    status: VersionStatus
    created_at: datetime
    storage_key: str | None = None
    content_hash: str | None = None
    extracted_text: str | None = None
    extracted_chars: int | None = None
    vector_indexed: bool = False
    error_message: str | None = None


@dataclass(frozen=True)
class Chunk:
    id: UUID
    project_id: UUID
    source_id: UUID
    version_id: UUID
    ordinal: int
    text: str
    locator: dict[str, Any] = field(default_factory=dict)


VECTOR_SEARCH_UNAVAILABLE = "vector_search_unavailable"
TEXT_SEARCH_UNAVAILABLE = "text_search_unavailable"


@dataclass(frozen=True)
class SearchHit:
    source_id: UUID
    title: str
    version: int
    text: str
    score: float
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    warning: str | None = None


@dataclass(frozen=True)
class ContextSource:
    source_id: UUID
    title: str
    version: int


@dataclass(frozen=True)
class ContextResult:
    context: str
    sources: list[ContextSource]
    warning: str | None = None


@dataclass(frozen=True)
class SyncFile:
    origin_key: str
    content_hash: str
    size: int


@dataclass(frozen=True)
class SyncPlan:
    to_upload: list[str]
    unchanged: list[str]
    to_prune: list[UUID]


class SyncFileStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
