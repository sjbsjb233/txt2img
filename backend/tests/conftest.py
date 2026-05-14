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

import httpx
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
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def initialized_db(fresh_env: None) -> AsyncIterator[None]:
    """Run migrations + open the engine for a single test."""
    from app.api.client_logs import reset_rate_limit_for_tests
    from app.db import engine as db_engine
    from app.db.migrate import upgrade_to_head
    from app.domain.access_policy import reset_access_policy_for_tests
    from app.domain.batch_service import reset_batch_progress_emitter_for_tests
    from app.domain.circuit_breaker import reset_circuit_breaker_for_tests
    from app.domain.config_center import reset_config_center_for_tests
    from app.domain.job_executor import reset_job_executor_for_tests
    from app.domain.job_queue import reset_job_queue_for_tests
    from app.domain.job_scheduler import reset_job_scheduler_for_tests
    from app.domain.metrics_engine import reset_metrics_engine_for_tests
    from app.domain.provider_selector import reset_provider_selector_for_tests
    from app.domain.quota_guard import reset_quota_guard_for_tests
    from app.domain.soft_penalty import reset_soft_penalty_for_tests
    from app.domain.sse_hub import reset_sse_hub_for_tests
    from app.domain.tier_config import reset_tier_config_for_tests
    from app.utils.logging_setup import reset_logging_for_tests

    # Singletons persist at module level; tests with fresh DBs need a
    # clean slate or they'd see leftover cache from a previous test's DB.
    reset_config_center_for_tests()
    reset_tier_config_for_tests()
    reset_quota_guard_for_tests()
    reset_access_policy_for_tests()
    reset_metrics_engine_for_tests()
    reset_circuit_breaker_for_tests()
    reset_provider_selector_for_tests()
    reset_job_queue_for_tests()
    reset_job_executor_for_tests()
    reset_job_scheduler_for_tests()
    reset_soft_penalty_for_tests()
    reset_sse_hub_for_tests()
    reset_batch_progress_emitter_for_tests()
    reset_logging_for_tests()
    reset_rate_limit_for_tests()

    upgrade_to_head()
    db_engine.init_engine()
    try:
        yield
    finally:
        await db_engine.close_engine()
        reset_config_center_for_tests()
        reset_tier_config_for_tests()
        reset_quota_guard_for_tests()
        reset_access_policy_for_tests()
        reset_metrics_engine_for_tests()
        reset_circuit_breaker_for_tests()
        reset_provider_selector_for_tests()
        reset_job_queue_for_tests()
        reset_job_executor_for_tests()
        reset_job_scheduler_for_tests()
        reset_soft_penalty_for_tests()
        reset_sse_hub_for_tests()
        reset_batch_progress_emitter_for_tests()
    reset_logging_for_tests()
    reset_rate_limit_for_tests()


@pytest_asyncio.fixture
async def seeded_app() -> AsyncIterator[httpx.AsyncClient]:
    """Boot the FastAPI app via lifespan and yield an httpx AsyncClient.

    Goes through the real lifespan so the app under test sees the same
    migration + seed path as production. The fixture also depends on
    ``fresh_env`` indirectly: we set env vars and clear the settings
    cache here so each test gets its own DB without bleeding state.
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="txt2img_test_"))
    db_path = tmp_dir / f"test_{uuid.uuid4().hex}.db"
    data_root = tmp_dir / "data"
    data_root.mkdir(exist_ok=True)
    _set_env_for_tests(db_path, data_root)

    from app.api.client_logs import reset_rate_limit_for_tests
    from app.config import get_settings
    from app.domain.access_policy import reset_access_policy_for_tests
    from app.domain.batch_service import reset_batch_progress_emitter_for_tests
    from app.domain.circuit_breaker import reset_circuit_breaker_for_tests
    from app.domain.config_center import reset_config_center_for_tests
    from app.domain.job_executor import reset_job_executor_for_tests
    from app.domain.job_queue import reset_job_queue_for_tests
    from app.domain.job_scheduler import reset_job_scheduler_for_tests
    from app.domain.metrics_engine import reset_metrics_engine_for_tests
    from app.domain.provider_selector import reset_provider_selector_for_tests
    from app.domain.quota_guard import reset_quota_guard_for_tests
    from app.domain.soft_penalty import reset_soft_penalty_for_tests
    from app.domain.sse_hub import reset_sse_hub_for_tests
    from app.domain.tier_config import reset_tier_config_for_tests
    from app.utils.logging_setup import reset_logging_for_tests

    get_settings.cache_clear()
    reset_config_center_for_tests()
    reset_tier_config_for_tests()
    reset_quota_guard_for_tests()
    reset_access_policy_for_tests()
    reset_metrics_engine_for_tests()
    reset_circuit_breaker_for_tests()
    reset_provider_selector_for_tests()
    reset_job_queue_for_tests()
    reset_job_executor_for_tests()
    reset_job_scheduler_for_tests()
    reset_soft_penalty_for_tests()
    reset_sse_hub_for_tests()
    reset_batch_progress_emitter_for_tests()
    reset_logging_for_tests()
    reset_rate_limit_for_tests()

    # Import here so env vars are already in place before Settings is
    # instantiated by anything down the import graph.
    from app.main import create_app

    app = create_app()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client

    get_settings.cache_clear()
    reset_config_center_for_tests()
    reset_tier_config_for_tests()
    reset_quota_guard_for_tests()
    reset_access_policy_for_tests()
    reset_metrics_engine_for_tests()
    reset_circuit_breaker_for_tests()
    reset_provider_selector_for_tests()
    reset_job_queue_for_tests()
    reset_job_executor_for_tests()
    reset_job_scheduler_for_tests()
    reset_soft_penalty_for_tests()
    reset_sse_hub_for_tests()
    reset_batch_progress_emitter_for_tests()
    reset_logging_for_tests()
    reset_rate_limit_for_tests()
