"""Add ``batches`` table + ``jobs.batch_id`` column.

Adds the server-side concept of a batch (frontend doc v0.3 / backend
doc v0.3): a user's "batch" — a group of jobs submitted together — is
now a first-class entity rather than a frontend-only construct. The
batch row is authoritative for status, progress counters, and the
60-second watchdog that flips abandoned submissions to ``abandoned``.

``jobs`` grows a nullable ``batch_id`` column so a regular single-job
``POST /api/jobs`` still leaves it ``NULL``; only batch-bound jobs
populate it. We do not enforce the FK at the SQL level — same posture
as ``jobs.set_id`` and ``jobs.session_id`` — application-layer checks
in :func:`app.api.jobs._bind_to_batch` are the real gate.

Revision ID: 0005_batches
Revises: 0004_job_derivation
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_batches"
down_revision: Union[str, None] = "0004_job_derivation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("total_job_count", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "succeeded_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cancelled_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitting','running','completed','partial','cancelled','abandoned')",
            name="ck_batches_status",
        ),
    )
    op.create_index(
        "idx_batches_user_status",
        "batches",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_batches_user_updated",
        "batches",
        ["user_id", "updated_at"],
        unique=False,
    )
    # Partial index for the watchdog scan — keeps the hot lookup tiny
    # because the vast majority of historical batches are terminal.
    op.create_index(
        "idx_batches_watchdog",
        "batches",
        ["status", "last_activity_at"],
        unique=False,
        sqlite_where=sa.text("status = 'submitting'"),
    )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("batch_id", sa.Text(), nullable=True))
    op.create_index("idx_jobs_batch", "jobs", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_jobs_batch", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("batch_id")
    op.drop_index("idx_batches_watchdog", table_name="batches")
    op.drop_index("idx_batches_user_updated", table_name="batches")
    op.drop_index("idx_batches_user_status", table_name="batches")
    op.drop_table("batches")
