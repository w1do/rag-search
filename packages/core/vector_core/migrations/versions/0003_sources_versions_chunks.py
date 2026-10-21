"""sources, source versions and chunks

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("origin_key", sa.Text(), nullable=True),
        sa.Column("sync_root", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('text', 'file', 'url')", name="ck_sources_kind"),
        sa.CheckConstraint("status IN ('pending', 'indexed', 'failed')", name="ck_sources_status"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_sources_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_project_id", "sources", ["project_id"], unique=False)
    op.create_index(
        "uq_sources_project_id_origin_key",
        "sources",
        ["project_id", "origin_key"],
        unique=True,
        postgresql_where=sa.text("origin_key IS NOT NULL"),
    )

    op.create_table(
        "source_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_chars", sa.Integer(), nullable=True),
        sa.Column("vector_indexed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'failed')",
            name="ck_source_versions_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_versions_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "version", name="uq_source_versions_source_id_version"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_chunks_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
            name="fk_chunks_version_id_source_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_chunks_version_id_ordinal"),
    )
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"], unique=False)
    op.create_index("ix_chunks_project_id", "chunks", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chunks_project_id", table_name="chunks")
    op.drop_index("ix_chunks_version_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("source_versions")
    op.drop_index("uq_sources_project_id_origin_key", table_name="sources")
    op.drop_index("ix_sources_project_id", table_name="sources")
    op.drop_table("sources")
