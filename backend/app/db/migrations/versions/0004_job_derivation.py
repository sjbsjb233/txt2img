"""Add derivation columns + cost tracking to jobs.

Adds five columns to ``jobs`` so the mask-edit / outpaint feature can
record (a) the parent → child link and (b) detailed cost / token usage
returned by the upstream call. A new index on ``parent_hash_id`` powers
``GET /api/jobs/{hash}/derived`` (list a parent's derived children) at
constant cost.

All columns are nullable — every existing row predates the feature and
should remain valid.

Revision ID: 0004_job_derivation
Revises: 0003_picker
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_job_derivation"
down_revision: Union[str, None] = "0003_picker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("parent_hash_id", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("derivation_kind", sa.Text(), nullable=True)
        )
        batch.add_column(sa.Column("cost_dollars", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("usage_input_tokens", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("usage_output_tokens", sa.Integer(), nullable=True)
        )
    op.create_index(
        "idx_jobs_parent_hash_id",
        "jobs",
        ["parent_hash_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_jobs_parent_hash_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("usage_output_tokens")
        batch.drop_column("usage_input_tokens")
        batch.drop_column("cost_dollars")
        batch.drop_column("derivation_kind")
        batch.drop_column("parent_hash_id")
