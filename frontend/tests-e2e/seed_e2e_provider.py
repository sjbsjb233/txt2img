"""Seed an admin-owned ``openai_v1`` provider for the Playwright e2e.

The provider's URL is intentionally pointed at a black-hole port so any
job submission would fail — the e2e never submits, it only inspects the
Create-page UI.  What we *do* need is for ``GET /api/models`` to surface
the new ``thinking`` chip-row and ``size_allow_custom: true`` so the
``Custom…`` button shows up.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx


BACKEND = os.environ.get("E2E_BACKEND_URL", "http://127.0.0.1:18901")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "test-admin-password")


PROVIDER_PAYLOAD = {
    "provider_id": "e2e-fake",
    "label": "E2E Fake (do not use)",
    "adapter_type": "openai_v1",
    # Black-hole URL: nothing resolves there, so accidental submits fail.
    "base_url": "http://127.0.0.1:1/v1",
    "api_key": "sk-not-a-real-key-for-e2e",
    "cost_per_image_cny": 0.001,
    "initial_balance_cny": 1000.0,
    "supported_models": [
        {
            "model_id": "gpt-image-2",
            "capabilities": {
                "n_max": 1,
                "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                "size_allow_custom": True,  # <-- enables the modal
                "quality": ["low", "medium", "high", "auto"],
                "output_format": ["png"],
                "background": ["auto", "opaque"],
                "moderation": ["auto", "low"],
                "thinking": ["off", "low", "medium", "high"],  # <-- new
                "max_reference_images": 0,
                "max_prompt_chars": 4000,
                "supports_mask": False,
                "stream": False,
                "partial_images_max": 0,
            },
        }
    ],
    "tier_access": ["vip", "premium", "standard", "free"],
}


async def main() -> int:
    async with httpx.AsyncClient(base_url=BACKEND, timeout=10.0) as c:
        # 1. Log in as admin
        r = await c.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        # 2. Drop any leftover provider with the same id (idempotent re-runs)
        listed = await c.get("/api/admin/providers", headers=auth)
        for p in listed.json() if listed.status_code == 200 else []:
            if p.get("id") == PROVIDER_PAYLOAD["provider_id"]:
                await c.delete(
                    f"/api/admin/providers/{p['id']}", headers=auth
                )
                break

        # 3. Create the e2e provider
        r = await c.post(
            "/api/admin/providers", headers=auth, json=PROVIDER_PAYLOAD
        )
        if r.status_code not in (200, 201):
            print(
                f"FAILED to seed provider: {r.status_code} {r.text}",
                file=sys.stderr,
            )
            return 1

    print("seeded provider:", PROVIDER_PAYLOAD["provider_id"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
