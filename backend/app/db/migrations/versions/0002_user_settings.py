"""User settings: profile, preferences, auth sessions, deletion requests.

Adds the schema needed by the user-facing ``/settings`` page:

- ``users.email`` (nullable, unique modulo NULL) and
  ``users.password_changed_at``
- ``user_preferences`` — single-row-per-user flat table holding generation
  defaults, notification toggles, appearance preferences, and locale
- ``auth_sessions`` — server-side JWT tracking so users can list and
  revoke active devices
- ``account_deletion_requests`` — pending self-service deletion requests
  awaiting admin approval

Revision ID: 0002_user_settings
Revises: 0001_init
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_settings"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------- users.email
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("password_changed_at", sa.DateTime(), nullable=True)
        )
    # SQLite treats NULLs as distinct in UNIQUE indexes, so we can have
    # many users with email=NULL and still enforce uniqueness on real
    # values.
    op.create_index(
        "idx_users_email_unique",
        "users",
        ["email"],
        unique=True,
    )

    # --------------------------------------------------------- user_preferences
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Text(), nullable=False),
        # generation
        sa.Column("default_model_id", sa.Text(), nullable=True),
        sa.Column(
            "default_aspect_ratio",
            sa.Text(),
            server_default=sa.text("'1:1'"),
            nullable=False,
        ),
        sa.Column(
            "default_batch_size",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "auto_bind_session",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "auto_retry",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "remember_prompt_history",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        # notifications
        sa.Column(
            "notif_browser_on_complete",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "notif_sound_on_complete",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "notif_sound_volume",
            sa.Integer(),
            server_default=sa.text("60"),
            nullable=False,
        ),
        sa.Column(
            "notif_desktop_badge",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "notif_announcements_level",
            sa.Text(),
            server_default=sa.text("'all'"),
            nullable=False,
        ),
        # appearance
        sa.Column(
            "theme",
            sa.Text(),
            server_default=sa.text("'system'"),
            nullable=False,
        ),
        sa.Column(
            "density",
            sa.Text(),
            server_default=sa.text("'comfortable'"),
            nullable=False,
        ),
        sa.Column(
            "sidebar_default",
            sa.Text(),
            server_default=sa.text("'expanded'"),
            nullable=False,
        ),
        # locale
        sa.Column(
            "language",
            sa.Text(),
            server_default=sa.text("'en'"),
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.Text(),
            server_default=sa.text("'Asia/Shanghai'"),
            nullable=False,
        ),
        sa.Column(
            "date_format",
            sa.Text(),
            server_default=sa.text("'iso'"),
            nullable=False,
        ),
        # privacy
        sa.Column(
            "hide_prompts_in_screenshot_mode",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_batch_size IN (1,2,4,8)", name="ck_pref_batch"
        ),
        sa.CheckConstraint(
            "notif_sound_volume BETWEEN 0 AND 100", name="ck_pref_volume"
        ),
        sa.CheckConstraint(
            "notif_announcements_level IN ('all','important','none')",
            name="ck_pref_ann",
        ),
        sa.CheckConstraint(
            "theme IN ('light','dark','system')", name="ck_pref_theme"
        ),
        sa.CheckConstraint(
            "density IN ('comfortable','compact')", name="ck_pref_density"
        ),
        sa.CheckConstraint(
            "sidebar_default IN ('expanded','rail')", name="ck_pref_sidebar"
        ),
        sa.CheckConstraint(
            "date_format IN ('iso','long','us')", name="ck_pref_date"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # ------------------------------------------------------------ auth_sessions
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("jti", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti"),
    )
    op.create_index(
        "idx_auth_sessions_user", "auth_sessions", ["user_id", "revoked_at"]
    )

    # --------------------------------------------------- account_deletion_requests
    op.create_table(
        "account_deletion_requests",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','withdrawn')",
            name="ck_adr_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Only one *pending* deletion request per user at a time. Using a
    # partial unique index lets withdrawn / rejected rows accumulate
    # without blocking a future fresh request.
    op.create_index(
        "idx_adr_pending_user",
        "account_deletion_requests",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_adr_pending_user", table_name="account_deletion_requests")
    op.drop_table("account_deletion_requests")

    op.drop_index("idx_auth_sessions_user", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_table("user_preferences")

    op.drop_index("idx_users_email_unique", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_changed_at")
        batch.drop_column("email")
