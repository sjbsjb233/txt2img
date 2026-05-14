"""Alembic environment.

We keep this small: read the SQLAlchemy URL from the application
``Settings`` (so we don't duplicate the env var contract here), point
``target_metadata`` at our declarative ``Base.metadata``, and run the
migration synchronously — alembic's offline + online runners work fine
against the sync sqlite driver. The application itself uses ``aiosqlite``
at runtime; this divergence is intentional because alembic doesn't have
first-class async support yet, and converting the URL on the fly is the
official recommendation.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure ``app`` is importable when alembic is run from the backend dir.
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.config import get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    # ``disable_existing_loggers=False`` keeps the project-wide logging
    # stack configured by ``app.utils.logging_setup`` alive across the
    # alembic bootstrap. Without this, every ``txt2img.*`` logger that
    # was created before ``upgrade_to_head()`` runs ends up with
    # ``disabled=True`` and silently drops records for the rest of the
    # process lifetime.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Translate an ``aiosqlite`` URL to the sync ``pysqlite`` URL alembic uses."""
    url = get_settings().DB_URL
    if url.startswith("sqlite+aiosqlite://"):
        return "sqlite://" + url[len("sqlite+aiosqlite://") :]
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly: rebuild table for ALTER
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
