"""Picker page: pick_state on images, picker_state on sessions.

Adds the schema needed by the Picker page (PRD §8):

- ``images.pick_state`` enum (unjudged/picked/discarded/final/deferred) +
  ``pick_state_updated_at`` timestamp + check constraint + indexes.
  Default ``unjudged``. Pre-existing rows with ``starred=1`` get
  back-filled to ``picked`` so the user's old work isn't silently
  reset to "unjudged".

- ``sessions.picker_state`` enum (not_started/judging/finalized) +
  ``final_image_id`` FK + ``cursor_image_id`` + ``finalized_at``.

The "ready_to_finalize" state is **front-end-computed** and intentionally
not persisted — it's a pure function of the current judging counts.

Revision ID: 0003_picker
Revises: 0002_user_settings
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_picker"
down_revision: Union[str, None] = "0002_user_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------ images
    with op.batch_alter_table("images") as batch:
        batch.add_column(
            sa.Column(
                "pick_state",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'unjudged'"),
            )
        )
        batch.add_column(
            sa.Column(
                "pick_state_updated_at",
                sa.DateTime(),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            "ck_images_pick_state",
            "pick_state IN ('unjudged','picked','discarded','final','deferred')",
        )

    op.create_index(
        "idx_images_pick_state",
        "images",
        ["pick_state"],
    )
    op.create_index(
        "idx_images_job_pick",
        "images",
        ["job_id", "pick_state"],
    )

    # Back-fill: starred rows become 'picked' so user's prior work survives
    # the migration. The "final" promotion is left to the user; we don't
    # try to guess which of the starred images they considered best.
    op.execute(
        "UPDATE images SET pick_state = 'picked' "
        "WHERE starred = 1 AND pick_state = 'unjudged'"
    )

    # ----------------------------------------------------------- sessions
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column(
                "picker_state",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch.add_column(
            sa.Column("final_image_id", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("cursor_image_id", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("finalized_at", sa.DateTime(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_sessions_picker_state",
            "picker_state IN ('not_started','judging','finalized')",
        )
        # FK is declared without enforcement-cascade because deleting an
        # image should not silently delete the session — we want to NULL
        # ``final_image_id`` instead. The ON DELETE SET NULL clause on
        # the foreign key handles that.
        batch.create_foreign_key(
            "fk_sessions_final_image",
            "images",
            ["final_image_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "idx_sessions_picker_state",
        "sessions",
        ["user_id", "picker_state"],
    )

    # Set picker_state='judging' for sessions that already have any judged
    # image after the back-fill. Otherwise keep 'not_started' (default).
    op.execute(
        """
        UPDATE sessions
        SET picker_state = 'judging'
        WHERE id IN (
            SELECT DISTINCT sj.session_id
            FROM session_jobs sj
            JOIN images i ON i.job_id IN (
                SELECT id FROM jobs WHERE jobs.id = sj.job_id
            )
            WHERE i.pick_state != 'unjudged'
        )
        """
    )


def downgrade() -> None:
    op.drop_index("idx_sessions_picker_state", table_name="sessions")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_constraint("fk_sessions_final_image", type_="foreignkey")
        batch.drop_constraint("ck_sessions_picker_state", type_="check")
        batch.drop_column("finalized_at")
        batch.drop_column("cursor_image_id")
        batch.drop_column("final_image_id")
        batch.drop_column("picker_state")

    op.drop_index("idx_images_job_pick", table_name="images")
    op.drop_index("idx_images_pick_state", table_name="images")
    with op.batch_alter_table("images") as batch:
        batch.drop_constraint("ck_images_pick_state", type_="check")
        batch.drop_column("pick_state_updated_at")
        batch.drop_column("pick_state")
