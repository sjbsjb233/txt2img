"""First-run seed.

Idempotent: every step uses ``INSERT OR IGNORE`` semantics so the function
can run on every startup without duplicating rows or clobbering admin
edits. The seed is responsible for *creating* the default state, not for
keeping it in sync — once a row exists, admin owns it.

What we seed:

1. The four default tiers (design doc §3.1 / appendix A).
2. The full default ``config`` table (design doc §13.5).
3. A single bootstrap admin user from ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``.

Things deliberately not done here:
- Provider rows: admin creates these via the API (no defaults).
- Sessions / jobs / images / etc: user-generated.
- Migrations: the lifespan hook runs ``alembic upgrade head`` before
  calling ``bootstrap()``; the seed assumes the schema already exists.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Config, Tier, User

logger = logging.getLogger("txt2img.db.seed")


# ---------------------------------------------------------------------------
# Defaults — the single source of truth for "out of the box" state.
# ---------------------------------------------------------------------------


# (tier, weight, max_concurrency, max_queue, soft_quota, hard_quota, slo_p95_ms)
DEFAULT_TIERS: list[tuple[str, int, int, int, int, int, int | None]] = [
    ("vip", 8, 4, 10, 100, 200, 30_000),
    ("premium", 4, 2, 5, 50, 100, 60_000),
    ("standard", 2, 1, 3, 20, 40, 180_000),
    ("free", 1, 1, 3, 8, 10, None),
]


# Mirrors the YAML block in design doc §13.5. Values are JSON-encoded into
# the ``value_json`` column so a single column can hold ints, floats, bools,
# and (later) nested objects without separate type columns.
DEFAULT_CONFIG: dict[str, Any] = {
    # ----- scheduler -----
    "scheduler.global_max_workers": 32,
    "scheduler.min_share_per_lane": 0.05,
    "scheduler.deadline_promotion_seconds": 300,
    # ----- soft penalty -----
    "soft_penalty.base_delay_seconds": 5,
    "soft_penalty.max_delay_seconds": 15,
    "soft_penalty.base_fail_probability": 0.3,
    "soft_penalty.max_fail_probability": 0.7,
    "soft_penalty.require_turnstile": True,
    # ----- provider scoring -----
    "provider_scoring.weights.cost": 0.30,
    "provider_scoring.weights.success": 0.25,
    "provider_scoring.weights.latency": 0.20,
    "provider_scoring.weights.load": 0.15,
    "provider_scoring.weights.freshness": 0.10,
    "provider_scoring.metric_window_seconds": 300,
    "provider_scoring.fallback_top_k": 3,
    "provider_scoring.max_retries_per_job": 3,
    # ----- circuit breaker -----
    "circuit_breaker.failure_threshold": 5,
    "circuit_breaker.initial_cooldown_seconds": 30,
    "circuit_breaker.max_cooldown_seconds": 600,
    "circuit_breaker.half_open_probe_concurrency": 1,
    # ----- provider filter -----
    "provider_filter.balance_min_threshold": 0.5,
    # ----- retention / thumbnails -----
    "retention.job_retention_days": 30,
    "thumbnail.max_long_edge": 720,
    "thumbnail.quality": 78,
    # ----- emergency switches (all off by default) -----
    "emergency.pause_generation": False,
    "emergency.pause_image_access": False,
    "emergency.block_new_member_login": False,
    "emergency.force_captcha_global": False,
}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def bootstrap() -> None:
    """Seed default tiers, config, and bootstrap admin if missing."""
    async with get_session() as session:
        await _seed_tiers(session)
        await _seed_config(session)
        await _seed_bootstrap_admin(session)
    logger.info("seed bootstrap complete")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _seed_tiers(session: AsyncSession) -> None:
    existing = {
        row[0]
        for row in (await session.execute(select(Tier.tier))).all()
    }
    inserted = 0
    for tier_name, weight, conc, queue, soft, hard, slo in DEFAULT_TIERS:
        if tier_name in existing:
            continue
        session.add(
            Tier(
                tier=tier_name,
                weight=weight,
                max_concurrency=conc,
                max_queue=queue,
                soft_quota=soft,
                hard_quota=hard,
                slo_p95_ms=slo,
            )
        )
        inserted += 1
    if inserted:
        logger.info("seed: inserted %d tier rows", inserted)


async def _seed_config(session: AsyncSession) -> None:
    existing = {
        row[0]
        for row in (await session.execute(select(Config.key))).all()
    }
    inserted = 0
    for key, value in DEFAULT_CONFIG.items():
        if key in existing:
            continue
        session.add(
            Config(
                key=key,
                value_json=json.dumps(value, separators=(",", ":")),
                updated_by="seed",
            )
        )
        inserted += 1
    if inserted:
        logger.info("seed: inserted %d config rows", inserted)


async def _seed_bootstrap_admin(session: AsyncSession) -> None:
    settings = get_settings()
    username = settings.ADMIN_USERNAME

    existing = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        return

    hasher = PasswordHasher()
    user = User(
        id=_generate_user_id(),
        username=username,
        password_hash=hasher.hash(settings.ADMIN_PASSWORD),
        role="admin",
        tier="vip",
        status="active",
        display_name="Admin",
    )
    session.add(user)
    logger.info("seed: created bootstrap admin username=%s", username)


def _generate_user_id() -> str:
    """Return a fresh ``u_`` + 12-char nanoid-style id.

    PR-08 introduces ``app.utils.ids`` as the canonical id generator for
    every entity; until then we keep a tiny inline helper here so the seed
    doesn't depend on an unwritten module. The alphabet matches design doc
    Appendix B (alphanumeric, no ambiguous characters from the URL-safe
    base).
    """
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    suffix = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"u_{suffix}"


# ---------------------------------------------------------------------------
# Test convenience: callable from non-async contexts via asyncio.run
# ---------------------------------------------------------------------------


def _smoke_check_env() -> None:
    """Fail fast if mandatory env vars are missing.

    Useful for the alembic + seed CLI flow during local development; the
    FastAPI lifespan path already has its own pydantic-settings validation.
    """
    if not os.environ.get("JWT_SECRET"):
        raise RuntimeError("JWT_SECRET is required")
    if not os.environ.get("ADMIN_PASSWORD"):
        raise RuntimeError("ADMIN_PASSWORD is required")
