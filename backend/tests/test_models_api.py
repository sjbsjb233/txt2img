"""End-to-end tests for ``GET /api/models`` (PR-13).

Boots the real app via ``seeded_app``, registers one provider through
the admin route to seed capabilities, and asserts the merged-capability
shape the Create page consumes.
"""

from __future__ import annotations

import httpx
import pytest


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _gemini_provider_payload() -> dict:
    """Single-provider gemini config covering both gemini models."""
    return {
        "provider_id": "bltcy",
        "label": "BLTCY",
        "adapter_type": "gemini_v1beta",
        "base_url": "https://api.bltcy.ai/v1beta",
        "api_key": "sk-bltcy-secret-FQBR1234",
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 3.88,
        "supported_models": [
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {
                    "n_max": 1,
                    "image_size": ["1K", "2K", "4K"],
                    "aspect_ratio": ["1:1", "16:9", "9:16"],
                    "thinking_level": ["minimal", "high"],
                    "include_thoughts": True,
                    "google_search": True,
                    "image_search": True,
                    "max_reference_images": 14,
                },
            },
            {
                "model_id": "gemini-3-pro-image-preview",
                "capabilities": {
                    "n_max": 1,
                    "image_size": ["1K", "2K", "4K"],
                    "aspect_ratio": ["1:1"],
                    "google_search": True,
                },
            },
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
    }


def _openai_provider_payload(provider_id: str = "oai") -> dict:
    return {
        "provider_id": provider_id,
        "label": "OpenAI",
        "adapter_type": "openai_v1",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-fake-openai-1234",
        "cost_per_image_cny": 0.20,
        "initial_balance_cny": 5.0,
        "supported_models": [
            {
                "model_id": "gpt-image-2",
                "capabilities": {
                    "n_max": 10,
                    "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                    "quality": ["low", "medium", "high", "auto"],
                    "output_format": ["png", "jpeg", "webp"],
                    "background": ["auto", "opaque"],
                    "moderation": ["auto", "low"],
                    "max_reference_images": 16,
                    "max_prompt_chars": 32000,
                    "supports_mask": True,
                    "stream": True,
                    "partial_images_max": 3,
                },
            },
        ],
        "tier_access": ["vip", "premium", "standard"],
    }


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/models")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# No providers configured: list still surfaces the model display table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_returns_unavailable_models_with_reason(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Fresh DB has zero providers; the catalogue still lists models."""
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/models", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_id = {m["model_id"]: m for m in body["models"]}
    assert "gpt-image-2" in by_id
    assert by_id["gpt-image-2"]["available"] is False
    assert by_id["gpt-image-2"]["available_reason"] == "no_provider_for_tier"
    # No control surface to render.
    assert by_id["gpt-image-2"]["capabilities"]["size"] is None
    # Defaults still ship so the frontend can pre-fill on click.
    assert by_id["gpt-image-2"]["defaults"]["n"] == 4
    assert body["sessions"] == []


# ---------------------------------------------------------------------------
# Capabilities surface for a configured provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_surface_capabilities_from_provider(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    assert create.status_code == 201, create.text

    resp = await seeded_app.get("/api/models", headers=_auth(token))
    body = resp.json()
    by_id = {m["model_id"]: m for m in body["models"]}

    flash = by_id["gemini-3.1-flash-image-preview"]
    assert flash["available"] is True
    caps = flash["capabilities"]
    assert caps["aspect_ratio"] == ["1:1", "16:9", "9:16"]
    assert caps["thinking_level"] == ["minimal", "high"]
    assert caps["google_search"] is True
    assert caps["image_search"] is True
    assert caps["n_max"] == 1
    # Field with no opinion in the cap json is left as null so the
    # frontend hides the control entirely.
    assert caps["size"] is None

    pro = by_id["gemini-3-pro-image-preview"]
    assert pro["available"] is True
    pro_caps = pro["capabilities"]
    assert pro_caps["thinking_level"] is None  # 3-pro doesn't expose it
    assert pro_caps["image_search"] is None


@pytest.mark.asyncio
async def test_models_unions_capabilities_across_providers(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Two providers covering the same model → list union, bool OR, max."""
    token = await _login_admin(seeded_app)
    # Provider A: 1K + 2K, no image_search.
    payload_a = _gemini_provider_payload()
    payload_a["provider_id"] = "relay_a"
    payload_a["supported_models"] = [
        {
            "model_id": "gemini-3.1-flash-image-preview",
            "capabilities": {
                "n_max": 1,
                "image_size": ["1K", "2K"],
                "aspect_ratio": ["1:1"],
                "image_search": False,
            },
        }
    ]
    payload_a["tier_access"] = ["vip", "premium", "standard"]
    payload_b = _gemini_provider_payload()
    payload_b["provider_id"] = "relay_b"
    # Provider B: 4K only, image_search opt-in, n_max=2.
    payload_b["supported_models"] = [
        {
            "model_id": "gemini-3.1-flash-image-preview",
            "capabilities": {
                "n_max": 2,
                "image_size": ["4K"],
                "aspect_ratio": ["16:9"],
                "image_search": True,
            },
        }
    ]
    payload_b["tier_access"] = ["vip", "premium", "standard"]

    a = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=payload_a
    )
    b = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=payload_b
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text

    resp = await seeded_app.get("/api/models", headers=_auth(token))
    flash = next(
        m
        for m in resp.json()["models"]
        if m["model_id"] == "gemini-3.1-flash-image-preview"
    )
    caps = flash["capabilities"]
    # List union: 1K, 2K from A + 4K from B (B contributes 4K only).
    assert set(caps["image_size"]) == {"1K", "2K", "4K"}
    # Aspect union.
    assert set(caps["aspect_ratio"]) == {"1:1", "16:9"}
    # Numeric max.
    assert caps["n_max"] == 2
    # Boolean OR.
    assert caps["image_search"] is True


# ---------------------------------------------------------------------------
# Tier scoping: the user's tier must be on the access list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_respects_tier_access(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A free user sees nothing if no provider is open to free tier."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    admin_token = await _login_admin(seeded_app)
    payload = _openai_provider_payload()
    # Tier access excludes free.
    payload["tier_access"] = ["vip", "premium", "standard"]
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(admin_token), json=payload
    )
    assert create.status_code == 201

    # Create a free user.
    async with get_session() as session:
        session.add(
            User(
                id="u_free_models",
                username="free_models",
                password_hash=hash_password("freepw1234"),
                role="user",
                tier="free",
            )
        )

    free_login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "free_models", "password": "freepw1234"},
    )
    assert free_login.status_code == 200, free_login.text
    free_token = free_login.json()["access_token"]

    resp = await seeded_app.get("/api/models", headers=_auth(free_token))
    assert resp.status_code == 200
    body = resp.json()
    by_id = {m["model_id"]: m for m in body["models"]}
    # gpt-image-2 is in the display table but no provider serves free → unavailable.
    assert by_id["gpt-image-2"]["available"] is False
    assert by_id["gpt-image-2"]["available_reason"] == "no_provider_for_tier"


# ---------------------------------------------------------------------------
# Sessions list inlined for the picker
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UI schema (Create-page renderer driver, design v2 §3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_includes_ui_schema_for_every_descriptor(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Every model row carries a ``ui_schema`` list (possibly empty)."""
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/models", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    for descriptor in resp.json()["models"]:
        assert isinstance(descriptor.get("ui_schema"), list)


@pytest.mark.asyncio
async def test_models_ui_schema_present_even_without_provider(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A model with no provider configured still ships its ui_schema —
    the panel should render fields, just with everything greyed."""
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/models", headers=_auth(token))
    by_id = {m["model_id"]: m for m in resp.json()["models"]}

    gpt = by_id["gpt-image-2"]
    assert gpt["available"] is False
    keys = {f["k"] for f in gpt["ui_schema"]}
    assert {"n_max", "size", "quality"} <= keys


@pytest.mark.asyncio
async def test_models_ui_schema_independent_of_user_tier(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Two users on different tiers see the same ``ui_schema`` for a
    model — only ``capabilities`` differs by tier."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    admin_token = await _login_admin(seeded_app)
    payload = _openai_provider_payload()
    payload["tier_access"] = ["vip", "premium", "standard"]
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(admin_token), json=payload
    )
    assert create.status_code == 201

    async with get_session() as session:
        session.add(
            User(
                id="u_free_uischema",
                username="free_uischema",
                password_hash=hash_password("freepw1234"),
                role="user",
                tier="free",
            )
        )

    free_login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "free_uischema", "password": "freepw1234"},
    )
    free_token = free_login.json()["access_token"]

    admin_resp = await seeded_app.get(
        "/api/models", headers=_auth(admin_token)
    )
    free_resp = await seeded_app.get(
        "/api/models", headers=_auth(free_token)
    )
    admin_gpt = next(
        m for m in admin_resp.json()["models"] if m["model_id"] == "gpt-image-2"
    )
    free_gpt = next(
        m for m in free_resp.json()["models"] if m["model_id"] == "gpt-image-2"
    )
    # Admin (premium tier) reaches the configured provider; free user
    # does not. Capabilities differ; ui_schema does not.
    assert admin_gpt["available"] is True
    assert free_gpt["available"] is False
    assert admin_gpt["ui_schema"] == free_gpt["ui_schema"]


@pytest.mark.asyncio
async def test_models_includes_sessions_for_picker(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    s1 = await seeded_app.post(
        "/api/sessions", headers=_auth(token), json={"name": "Editorial"}
    )
    s2 = await seeded_app.post(
        "/api/sessions", headers=_auth(token), json={"name": "Brand Refresh"}
    )
    assert s1.status_code == 201
    assert s2.status_code == 201

    resp = await seeded_app.get("/api/models", headers=_auth(token))
    body = resp.json()
    names = [s["name"] for s in body["sessions"]]
    # Newest-first.
    assert names == ["Brand Refresh", "Editorial"]
    for entry in body["sessions"]:
        assert entry["image_count"] == 0
        assert entry["id"].startswith("sess_")
