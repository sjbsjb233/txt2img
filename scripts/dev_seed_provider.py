"""One-shot dev seed: create a fake openai_v1 provider exposing gpt-image-2
with size_allow_custom=true, and grant the free tier access to it. Run
after the backend lifespan has already migrated the schema and bootstrapped
the admin/tier rows, OR standalone (we run alembic + bootstrap ourselves).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../backend")

# Ensure required env so config.get_settings() validates.
os.environ.setdefault("JWT_SECRET", "dev-jwt-secret-change-in-prod-1234567890abcdef")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin12345")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:////home/user/txt2img/data/txt2img.db")
os.environ.setdefault("CORS_ORIGINS", "*")

# Ensure the data dir exists.
os.makedirs("/home/user/txt2img/data", exist_ok=True)


async def main() -> None:
    from app.db import seed
    from app.db.engine import get_session, init_engine
    from app.db.migrate import upgrade_to_head
    from app.db.models import (
        Provider,
        ProviderModel,
        ProviderModelTierAccess,
        ProviderTierAccess,
        Tier,
        User,
    )
    from app.utils.crypto import encrypt
    from sqlalchemy import select, delete

    upgrade_to_head()
    init_engine()
    await seed.bootstrap()

    async with get_session() as s:
        # Wipe any existing dev provider rows so this script is idempotent.
        await s.execute(delete(ProviderModelTierAccess).where(
            ProviderModelTierAccess.provider_id == "dev-openai"
        ))
        await s.execute(delete(ProviderTierAccess).where(
            ProviderTierAccess.provider_id == "dev-openai"
        ))
        await s.execute(delete(ProviderModel).where(
            ProviderModel.provider_id == "dev-openai"
        ))
        await s.execute(delete(Provider).where(Provider.id == "dev-openai"))

    capabilities = {
        "size": ["1024x1024", "1024x1536", "1536x1024", "auto"],
        "quality": ["low", "medium", "high"],
        "output_format": ["png", "jpeg", "webp"],
        "background": ["auto", "transparent", "opaque"],
        "moderation": ["low", "auto"],
        "thinking": ["disabled", "low", "medium", "high"],
        "n_max": 4,
        "max_reference_images": 8,
        "max_prompt_chars": 32000,
        "supports_transparent_bg": True,
        "supports_mask": True,
        "size_allow_custom": True,
    }

    async with get_session() as s:
        s.add(Provider(
            id="dev-openai",
            label="DEV OpenAI",
            adapter_type="openai_v1",
            base_url="https://example.com",
            api_key_enc=encrypt("sk-dev-fake"),
            cost_per_image_cny=0.1,
            initial_balance_cny=10000.0,
            balance_cny=10000.0,
            enabled=1,
        ))
        s.add(ProviderModel(
            provider_id="dev-openai",
            model_id="gpt-image-2",
            capabilities_json=json.dumps(capabilities),
            enabled=1,
        ))
        for tier in ("free", "standard", "premium", "vip"):
            s.add(ProviderTierAccess(provider_id="dev-openai", tier=tier))

        # Make sure a regular test user exists with a known password.
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        existing = (await s.execute(select(User).where(User.username == "tester"))).scalar_one_or_none()
        if existing is None:
            import secrets as _secrets
            s.add(User(
                id=_secrets.token_urlsafe(12),
                username="tester",
                password_hash=ph.hash("tester12345"),
                role="user",
                tier="standard",
                status="active",
            ))

    print("Seeded dev-openai provider with gpt-image-2 (size_allow_custom=true).")


if __name__ == "__main__":
    asyncio.run(main())
