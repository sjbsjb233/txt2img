"""PR-09 quota guard + generation access policy tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.jobs_repository import get_jobs_repository
from app.db.models import Config, Job, User
from app.domain.access_policy import get_access_policy
from app.domain.quota_guard import beijing_today, get_quota_guard
from app.utils.ids import new_user_id
from app.utils.security import hash_password


async def _bootstrap_runtime() -> None:
    """Seed DB + warm the config caches for domain tests."""
    from app.db import seed
    from app.domain.config_center import get_config_center
    from app.domain.tier_config import get_tier_config

    await seed.bootstrap()
    await get_config_center().load_from_db()
    await get_tier_config().load_from_db()


async def _create_user(
    *,
    username: str = "alice",
    tier: str = "free",
    today_count: int = 0,
    today_reset_date: str | None = None,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
    status: str = "active",
) -> User:
    user = User(
        id=new_user_id(),
        username=username,
        password_hash=hash_password("pw1234567"),
        role="user",
        tier=tier,
        status=status,
        today_count=today_count,
        today_reset_date=today_reset_date or beijing_today(),
        override_soft_quota=override_soft_quota,
        override_hard_quota=override_hard_quota,
    )
    async with get_session() as session:
        session.add(user)
    return user


async def _reload_user(user_id: str) -> User:
    async with get_session() as session:
        return (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_quota_guard_lazy_reset_then_record_usage(
    initialized_db: None,
) -> None:
    await _bootstrap_runtime()
    yesterday = (
        datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)
    ).date().isoformat()
    user = await _create_user(
        username="lazy_reset", today_count=9, today_reset_date=yesterday
    )

    new_count = await get_quota_guard().record_usage(user)
    assert new_count == 1

    fresh = await _reload_user(user.id)
    assert fresh.today_count == 1
    assert fresh.today_reset_date == beijing_today()


@pytest.mark.asyncio
async def test_quota_guard_soft_hard_and_refund(initialized_db: None) -> None:
    await _bootstrap_runtime()
    user = await _create_user(
        username="quota_edges",
        today_count=8,  # free soft=8, hard=10
    )
    guard = get_quota_guard()

    assert await guard.check_soft_quota_exceeded(user) is True
    assert await guard.check_hard_quota_exceeded(user) is False

    await guard.record_usage(user)
    await guard.record_usage(user)
    user = await _reload_user(user.id)
    assert await guard.check_hard_quota_exceeded(user) is True

    assert await guard.refund_usage(user) == 9


@pytest.mark.asyncio
async def test_access_policy_hard_quota_exceeded(initialized_db: None) -> None:
    await _bootstrap_runtime()
    user = await _create_user(
        username="hard_quota",
        today_count=1,
        override_soft_quota=1,
        override_hard_quota=1,
    )

    decision = await get_access_policy().evaluate(user, model="gpt-image-2")
    assert decision.passed is False
    assert decision.http_status == 429
    assert decision.code == "HARD_QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_access_policy_soft_quota_sets_flag(initialized_db: None) -> None:
    await _bootstrap_runtime()
    user = await _create_user(username="soft_quota", today_count=8)

    decision = await get_access_policy().evaluate(user, model="gpt-image-2")
    assert decision.passed is True
    assert decision.soft_quota_exceeded is True
    assert decision.flags == {"SOFT_QUOTA_EXCEEDED": True}


@pytest.mark.asyncio
async def test_access_policy_user_busy_when_active_capacity_full(
    initialized_db: None,
) -> None:
    await _bootstrap_runtime()
    user = await _create_user(username="busy_user")
    repo = get_jobs_repository()

    async with get_session() as session:
        for _ in range(4):  # free max_concurrency=1 + max_queue=3
            await repo.insert_queued(
                user_id=user.id,
                tier_at_submit="free",
                model="gpt-image-2",
                params_json="{}",
                session=session,
            )

    decision = await get_access_policy().evaluate(user, model="gpt-image-2")
    assert decision.passed is False
    assert decision.http_status == 429
    assert decision.code == "USER_BUSY"
    assert decision.active_jobs == 4
    assert decision.active_capacity == 4


@pytest.mark.asyncio
async def test_access_policy_ignores_finished_jobs_for_busy_count(
    initialized_db: None,
) -> None:
    await _bootstrap_runtime()
    user = await _create_user(username="not_busy_after_done")
    repo = get_jobs_repository()

    async with get_session() as session:
        for _ in range(4):
            created = await repo.insert_queued(
                user_id=user.id,
                tier_at_submit="free",
                model="gpt-image-2",
                params_json="{}",
                session=session,
            )
            job = (
                await session.execute(
                    select(Job).where(Job.hash_id == created.hash_id)
                )
            ).scalar_one()
            job.status = "SUCCEEDED"

    decision = await get_access_policy().evaluate(user, model="gpt-image-2")
    assert decision.passed is True
    assert decision.active_jobs == 0


@pytest.mark.asyncio
async def test_access_policy_pause_generation_blocks(
    initialized_db: None,
) -> None:
    await _bootstrap_runtime()
    from app.domain.config_center import get_config_center

    await get_config_center().set_many({"emergency.pause_generation": True})
    user = await _create_user(username="paused_user")

    decision = await get_access_policy().evaluate(user, model="gpt-image-2")
    assert decision.passed is False
    assert decision.http_status == 403
    assert decision.code == "BLOCKED_BY_EMERGENCY"


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_login_block_new_member_login_allows_admin_but_blocks_user(
    seeded_app: httpx.AsyncClient,
) -> None:
    async with get_session() as session:
        session.add(
            User(
                id=new_user_id(),
                username="blocked_login_user",
                password_hash=hash_password("pw1234567"),
                role="user",
                tier="free",
            )
        )
        row = (
            await session.execute(
                select(Config).where(
                    Config.key == "emergency.block_new_member_login"
                )
            )
        ).scalar_one()
        row.value_json = json.dumps(True)

    blocked = await seeded_app.post(
        "/api/auth/login",
        json={"username": "blocked_login_user", "password": "pw1234567"},
    )
    assert blocked.status_code == 401
    assert blocked.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"

    # Bootstrap admin bypasses the login block.
    await _login_admin(seeded_app)


@pytest.mark.asyncio
async def test_dryrun_access_is_admin_only(seeded_app: httpx.AsyncClient) -> None:
    no_auth = await seeded_app.post(
        "/api/jobs/_dryrun_access", json={"model": "gpt-image-2"}
    )
    assert no_auth.status_code == 401

    token = await _login_admin(seeded_app)
    ok = await seeded_app.post(
        "/api/jobs/_dryrun_access",
        json={"model": "gpt-image-2"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200
    assert ok.json()["passed"] is True
