"""Picker page: per-image judgment state + per-session picker progress.

Adds the columns the Picker page (PRD v1) needs to persist user
decisions across sessions:

- ``images.pick_state`` — enum of unjudged / picked / discarded / final /
  deferred. Default ``unjudged`` so all existing rows arrive in a known
  starting state.
- ``images.pick_state_updated_at`` — wall-clock of the last state change.
  Used by the audit trail surfaced in the Finalized session "History"
  rail block.
- ``sessions.picker_state`` — enum of not_started / judging / finalized.
  ``ready_to_finalize`` is intentionally a frontend-computed view;
  persisting only the three "real" states keeps the state machine small
  and avoids double-source-of-truth bugs.
- ``sessions.final_image_id`` — pointer to the one chosen image, nullable.
  ``ON DELETE SET NULL`` so deleting the underlying image (cleanup
  task) doesn't dangle the FK.
- ``sessions.cursor_image_id`` — last image the user had focused. Lets
  the page restore the cursor on revisit.
- ``sessions.finalized_at`` — timestamp the user clicked Finalize.

Also backfills existing ``images.starred=1`` rows to ``pick_state=picked``
so the Archive page's star bit is reflected in Picker on day one.

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
    # --------------------------------------------------------------- images
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
            sa.Column("pick_state_updated_at", sa.DateTime(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_images_pick_state",
            "pick_state IN ('unjudged','picked','discarded','final','deferred')",
        )
    op.create_index("idx_images_pick_state", "images", ["pick_state"])
    op.create_index("idx_images_job_pick", "images", ["job_id", "pick_state"])

    # ------------------------------------------------------------ sessions
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column(
                "picker_state",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch.add_column(sa.Column("final_image_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cursor_image_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))
        batch.create_check_constraint(
            "ck_sessions_picker_state",
            "picker_state IN ('not_started','judging','finalized')",
        )
        batch.create_foreign_key(
            "fk_sessions_final_image",
            "images",
            ["final_image_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "idx_sessions_picker_state", "sessions", ["user_id", "picker_state"]
    )

    # --------------------------------------------- backfill starred -> picked
    # Mirror the existing star bit into ``pick_state`` so the Picker page
    # sees a consistent world on first load. Only touch ``unjudged`` rows
    # so a re-run of the migration is a no-op.
    op.execute(
        "UPDATE images SET pick_state = 'picked' "
        "WHERE starred = 1 AND pick_state = 'unjudged'"
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
