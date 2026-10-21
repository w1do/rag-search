import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("name", name="uq_projects_name"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_projects_name_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKeyRow(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        Index("ix_api_keys_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_api_keys_project_id_projects"),
    )
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_sessions_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRow(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_ingestion_jobs_max_attempts_positive"),
        CheckConstraint(
            "(status = 'leased') = (leased_until IS NOT NULL AND leased_by IS NOT NULL)",
            name="ck_ingestion_jobs_lease_consistent",
        ),
        Index("ix_ingestion_jobs_project_id", "project_id"),
        Index(
            "ix_ingestion_jobs_claim",
            "available_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_ingestion_jobs_stale", "leased_until", postgresql_where=text("status = 'leased'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_ingestion_jobs_project_id_projects"),
    )
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("kind IN ('text', 'file', 'url')", name="ck_sources_kind"),
        CheckConstraint("status IN ('pending', 'indexed', 'failed')", name="ck_sources_status"),
        Index("ix_sources_project_id", "project_id"),
        Index(
            "uq_sources_project_id_origin_key",
            "project_id",
            "origin_key",
            unique=True,
            postgresql_where=text("origin_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_sources_project_id_projects"),
    )
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceVersionRow(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_source_versions_source_id_version"),
        CheckConstraint(
            "status IN ('pending', 'published', 'failed')",
            name="ck_source_versions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sources.id", ondelete="CASCADE", name="fk_source_versions_source_id_sources"),
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_indexed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_chunks_version_id_ordinal"),
        Index("ix_chunks_version_id", "version_id"),
        Index("ix_chunks_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sources.id", ondelete="CASCADE", name="fk_chunks_source_id_sources"),
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("source_versions.id", ondelete="CASCADE", name="fk_chunks_version_id_source_versions"),
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    text: Mapped[str] = mapped_column(Text)
