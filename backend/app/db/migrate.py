"""Programmatic alembic upgrade.

Used at process startup so the application can always assume schema is
current. Running alembic from Python instead of shelling out to the CLI
keeps the entrypoint single-binary friendly (no PATH dependence inside
containers) and lets us reuse the same DB URL the rest of the app reads
from ``Settings``.
"""

from __future__ import annotations

import logging
import os

from alembic import command
from alembic.config import Config as AlembicConfig

logger = logging.getLogger("txt2img.db.migrate")


def _alembic_config() -> AlembicConfig:
    here = os.path.dirname(os.path.abspath(__file__))
    backend_root = os.path.abspath(os.path.join(here, "..", ".."))
    cfg_path = os.path.join(backend_root, "alembic.ini")
    cfg = AlembicConfig(cfg_path)
    # script_location in alembic.ini is "app/db/migrations"; it is resolved
    # relative to alembic.ini's directory, which is the backend root. We
    # also override prepend_sys_path so subprocess + in-process invocations
    # both work even when CWD differs (e.g. tests).
    cfg.set_main_option(
        "script_location",
        os.path.join(backend_root, "app", "db", "migrations"),
    )
    cfg.set_main_option("prepend_sys_path", backend_root)
    return cfg


def upgrade_to_head() -> None:
    """Run ``alembic upgrade head`` against the current ``DB_URL``."""
    cfg = _alembic_config()
    logger.info("running alembic upgrade head")
    command.upgrade(cfg, "head")
    logger.info("alembic upgrade head complete")
