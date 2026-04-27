"""Shared test fixtures.

Each test gets a fresh, file-backed SQLite database so the schema and
PRAGMAs match production. We use a tmp file (not ``:memory:``) because
aiosqlite + ``:memory:`` does not share state across connections, which
breaks anything that opens more than one session.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio


def _set_env_for_tests(tmp_db: Path, tmp_data: Path) -> None:
    os.environ["JWT_SECRET"] = "test_secret_at_least_16_chars_long_value"
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["DB_URL"] = f"sqlite+aiosqlite:///{tmp_db}"
    os.environ["DATA_ROOT"] = str(tmp_data)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Iterator[Path]:
    db = tmp_path / f"test_{uuid.uuid4().hex}.db"
    yield db


@pytest.fixture
def fresh_env(tmp_db_path: Path, tmp_path: Path) -> Iterator[None]:
    """Set DB_URL + DATA_ROOT to per-test paths and reset Settings cache.

    Settings are cached via ``functools.lru_cache``; we clear it so the
    test sees the env vars we just set, not whatever the previous test
    left behind.
    """
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    _set_env_for_tests(tmp_db_path, data_root)

    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def initialized_db(fresh_env: None) -> AsyncIterator[None]:
    """Run migrations + open the engine for a single test."""
    from app.db import engine as db_engine
    from app.db.migrate import upgrade_to_head

    upgrade_to_head()
    db_engine.init_engine()
    try:
        yield
    finally:
        await db_engine.close_engine()
