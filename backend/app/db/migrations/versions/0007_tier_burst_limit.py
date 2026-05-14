"""Add ``burst_limit`` to ``tiers``.

Per-tier override of the rolling-window threshold consulted by
``app/api/jobs._recent_burst`` (previously a hardcoded literal 5).
DEFAULT 5 preserves prior behaviour for any tier that hasn't been
bumped explicitly. The seed file resets the defaults on a brand-new
install; this migration covers existing deployments that already
have ``tiers`` rows with the old behaviour.

Revision ID: 0007_tier_burst_limit
Revises: 0006_job_parent_order
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tier_burst_limit"
down_revision: Union[str, None] = "0006_job_parent_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tiers") as batch:
        batch.add_column(
            sa.Column(
                "burst_limit",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
    # Bump existing rows to the new per-tier defaults. New deploys hit
    # ``seed.py`` directly and skip this branch; this UPDATE is for
    # deployments where the rows existed before ``burst_limit`` did.
    op.execute("UPDATE tiers SET burst_limit = 20 WHERE tier = 'vip'")
    op.execute("UPDATE tiers SET burst_limit = 12 WHERE tier = 'premium'")
    op.execute("UPDATE tiers SET burst_limit = 8  WHERE tier = 'standard'")
    # free stays at 5 — anti-abuse posture for unverified accounts.


def downgrade() -> None:
    with op.batch_alter_table("tiers") as batch:
        batch.drop_column("burst_limit")
