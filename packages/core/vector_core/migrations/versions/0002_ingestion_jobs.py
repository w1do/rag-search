"""ingestion jobs queue

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_ingestion_jobs_max_attempts_positive"),
        sa.CheckConstraint(
            "(status = 'leased') = (leased_until IS NOT NULL AND leased_by IS NOT NULL)",
            name="ck_ingestion_jobs_lease_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_ingestion_jobs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_jobs_project_id", "ingestion_jobs", ["project_id"], unique=False)
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["available_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_ingestion_jobs_stale",
        "ingestion_jobs",
        ["leased_until"],
        unique=False,
        postgresql_where=sa.text("status = 'leased'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_stale", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_claim", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_project_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
