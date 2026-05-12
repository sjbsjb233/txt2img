"""Add ``parent_order`` to ``jobs``.

Records which image of a create-set parent was used as the source for a
derived (mask edit / outpaint) job. NULL on historical rows; the UI
degrades to "order unknown" for those.

Revision ID: 0006_job_parent_order
Revises: 0005_batches
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_parent_order"
down_revision: Union[str, None] = "0005_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("parent_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("parent_order")
