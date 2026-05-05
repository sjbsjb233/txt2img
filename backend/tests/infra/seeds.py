"""Test seed helpers — install fake providers + factory users.

Builds on top of ``conftest.seeded_app``; that fixture has already
applied migrations and seeded tiers + bootstrap admin. We layer the
test-only data (fake provider, role users) on top.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import httpx
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import (
    Provider,
    ProviderModel,
    ProviderTierAccess,
    User,
)
from app.utils.crypto import encrypt
from app.utils.ids import new_user_id
from app.utils.security import hash_password

from tests.infra.fake_adapter import (
    FULL_CAPS_GEM_FLASH,
    FULL_CAPS_GEM_PRO,
    FULL_CAPS_GPT_IMAGE_2,
)


ADMIN_PW = "test-admin-password"
USER_PW = "user-pw-1234"


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["access_token"]


async def login_admin(client: httpx.AsyncClient) -> str:
    return await login(client, "admin", ADMIN_PW)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_user(
    *,
    username: str | None = None,
    tier: str = "premium",
    role: str = "user",
    today_count: int = 0,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
    status: str = "active",
) -> User:
    """Insert a fresh user directly via SQLAlchemy. Returns the row."""
    username = username or f"u_{secrets.token_hex(4)}"
    user = User(
        id=new_user_id(),
        username=username,
        password_hash=hash_password(USER_PW),
        role=role,
        tier=tier,
        status=status,
        today_count=today_count,
        override_soft_quota=override_soft_quota,
        override_hard_quota=override_hard_quota,
    )
    async with get_session() as session:
        session.add(user)
    return user


async def login_user(
    client: httpx.AsyncClient,
    *,
    username: str | None = None,
    tier: str = "premium",
    today_count: int = 0,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
    role: str = "user",
) -> tuple[User, str]:
    """Create the user and log them in. Returns (user_row, jwt)."""
    user = await make_user(
        username=username,
        tier=tier,
        role=role,
        today_count=today_count,
        override_soft_quota=override_soft_quota,
        override_hard_quota=override_hard_quota,
    )
    token = await login(client, user.username, USER_PW)
    return user, token


async def install_fake_provider(
    *,
    provider_id: str = "fake-1",
    label: str = "Fake Studio",
    cost: float = 0.001,
    balance: float = 9999.0,
    max_concurrency: int = 100,
    tier_access: tuple[str, ...] = ("vip", "premium", "standard", "free"),
    models: tuple[str, ...] | None = None,
) -> str:
    """Insert a fake-adapter provider with the full capability set.

    Returns the provider_id so callers can chain.
    """
    models = models or (
        "gpt-image-2", "gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"
    )
    caps_map = {
        "gpt-image-2": FULL_CAPS_GPT_IMAGE_2,
        "gemini-3-pro-image-preview": FULL_CAPS_GEM_PRO,
        "gemini-3.1-flash-image-preview": FULL_CAPS_GEM_FLASH,
    }
    async with get_session() as session:
        existing = (
            await session.execute(select(Provider).where(Provider.id == provider_id))
        ).scalar_one_or_none()
        if existing is not None:
            return provider_id
        session.add(
            Provider(
                id=provider_id,
                label=label,
                adapter_type="fake",
                base_url="http://fake.local",
                api_key_enc=encrypt("fake-key"),
                cost_per_image_cny=cost,
                initial_balance_cny=balance,
                balance_cny=balance,
                max_concurrency=max_concurrency,
                rpm_limit=6000,
                enabled=1,
                circuit_state="healthy",
            )
        )
        for m in models:
            session.add(
                ProviderModel(
                    provider_id=provider_id,
                    model_id=m,
                    capabilities_json=json.dumps(caps_map[m]),
                    enabled=1,
                )
            )
        for t in tier_access:
            session.add(ProviderTierAccess(provider_id=provider_id, tier=t))
    return provider_id
