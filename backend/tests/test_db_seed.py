"""Seed correctness + idempotency tests."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_seed_inserts_four_tiers(initialized_db: None) -> None:
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import Tier

    await seed.bootstrap()

    async with get_session() as session:
        rows = (await session.execute(select(Tier))).scalars().all()

    by_tier = {r.tier: r for r in rows}
    assert set(by_tier) == {"vip", "premium", "standard", "free"}

    # Spot-check a few values straight from design doc §3.1.
    assert by_tier["vip"].weight == 8
    assert by_tier["vip"].max_concurrency == 4
    assert by_tier["vip"].soft_quota == 100
    assert by_tier["vip"].hard_quota == 200
    assert by_tier["vip"].slo_p95_ms == 30_000

    assert by_tier["free"].weight == 1
    assert by_tier["free"].soft_quota == 8
    assert by_tier["free"].hard_quota == 10
    assert by_tier["free"].slo_p95_ms is None


@pytest.mark.asyncio
async def test_seed_inserts_all_default_config(initialized_db: None) -> None:
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import Config

    await seed.bootstrap()

    async with get_session() as session:
        rows = (await session.execute(select(Config))).scalars().all()
    by_key = {r.key: json.loads(r.value_json) for r in rows}

    # Every key documented in §13.5 must be present.
    expected_keys = set(seed.DEFAULT_CONFIG.keys())
    assert set(by_key) == expected_keys

    # Spot-check a few of the load-bearing values.
    assert by_key["scheduler.global_max_workers"] == 32
    assert by_key["soft_penalty.base_delay_seconds"] == 5
    assert by_key["soft_penalty.require_turnstile"] is True
    assert by_key["provider_scoring.weights.cost"] == 0.30
    assert by_key["circuit_breaker.failure_threshold"] == 5
    assert by_key["emergency.pause_generation"] is False


@pytest.mark.asyncio
async def test_seed_creates_bootstrap_admin(initialized_db: None) -> None:
    """Admin row is created with argon2id-hashed password and tier=vip."""
    import os

    from argon2 import PasswordHasher

    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import User

    await seed.bootstrap()

    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == os.environ["ADMIN_USERNAME"])
            )
        ).scalar_one()

    assert admin.role == "admin"
    assert admin.tier == "vip"
    assert admin.status == "active"
    assert admin.id.startswith("u_")
    assert len(admin.id) == 14  # 'u_' + 12

    # Hash must verify against the env-supplied password (argon2id format).
    assert admin.password_hash.startswith("$argon2id$")
    PasswordHasher().verify(admin.password_hash, os.environ["ADMIN_PASSWORD"])


@pytest.mark.asyncio
async def test_seed_is_idempotent(initialized_db: None) -> None:
    """Calling bootstrap a second time must not duplicate rows or throw."""
    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import Config, Tier, User

    await seed.bootstrap()
    await seed.bootstrap()  # again
    await seed.bootstrap()  # and again

    async with get_session() as session:
        tiers = (await session.execute(select(Tier))).scalars().all()
        configs = (await session.execute(select(Config))).scalars().all()
        users = (await session.execute(select(User))).scalars().all()

    assert len(tiers) == 4
    assert len(configs) == len(seed.DEFAULT_CONFIG)
    assert len(users) == 1


@pytest.mark.asyncio
async def test_seed_does_not_overwrite_existing_admin(
    initialized_db: None,
) -> None:
    """If admin already exists, seed must not stomp the (possibly rotated) password."""
    import os

    from argon2 import PasswordHasher

    from app.db import seed
    from app.db.engine import get_session
    from app.db.models import User

    await seed.bootstrap()

    # Admin rotates their own password manually; later we try to bootstrap.
    new_pw_hash = PasswordHasher().hash("rotated-password-9999")
    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == os.environ["ADMIN_USERNAME"])
            )
        ).scalar_one()
        admin.password_hash = new_pw_hash

    await seed.bootstrap()

    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == os.environ["ADMIN_USERNAME"])
            )
        ).scalar_one()
    PasswordHasher().verify(admin.password_hash, "rotated-password-9999")


@pytest.mark.asyncio
async def test_create_and_read_user_round_trip(initialized_db: None) -> None:
    """Smoke test: insert a user, read it back, defaults look right."""
    from app.db.engine import get_session
    from app.db.models import User

    async with get_session() as session:
        session.add(
            User(
                id="u_round_trip01",
                username="alice",
                password_hash="$argon2id$v=19$m=65536,t=2,p=1$abc$def",
                role="user",
                tier="premium",
                display_name="Alice",
            )
        )

    async with get_session() as session:
        rows = (
            await session.execute(select(User).where(User.username == "alice"))
        ).scalars().all()
    assert len(rows) == 1

    alice = rows[0]
    assert alice.role == "user"
    assert alice.tier == "premium"
    assert alice.status == "active"  # server default
    assert alice.today_count == 0  # server default
    assert alice.last_seq_no == 0  # server default
    assert alice.override_soft_quota is None
    assert alice.created_at is not None
