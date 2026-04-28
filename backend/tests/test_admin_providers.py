"""End-to-end tests for ``/api/admin/providers/*``.

These boot the FastAPI app via the ``seeded_app`` fixture so adapter
discovery, config seeding, bootstrap admin and DB engine all match
production. We exercise the full CRUD lifecycle, encryption /
masking guarantees, validation paths, and the topup ↔ ledger
interaction. ``in_use_by_providers`` on the adapters route is also
covered here because it's a function of the ``providers`` table.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_user(client: httpx.AsyncClient) -> str:
    """Create a non-admin user and return their access token."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_norm_provtest",
                username="bob_norm",
                password_hash=hash_password("bobpw1234"),
                role="user",
                tier="free",
            )
        )

    resp = await client.post(
        "/api/auth/login",
        json={"username": "bob_norm", "password": "bobpw1234"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _gemini_provider_payload(**overrides) -> dict:
    """Standard valid payload for ``POST /api/admin/providers``.

    Uses ``gemini_v1beta`` because it supports several models and lets
    us exercise the supported_models validation.
    """
    base = {
        "provider_id": "bltcy",
        "label": "BLTCY",
        "adapter_type": "gemini_v1beta",
        "base_url": "https://api.bltcy.ai/v1beta",
        "api_key": "sk-bltcy-secret-FQBR1234",
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 3.88,
        "enabled": True,
        "note": "primary gemini relay",
        "max_concurrency": 70,
        "rpm_limit": 600,
        "supported_models": [
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {
                    "n_max": 1,
                    "image_size": ["1K", "2K", "4K"],
                    "aspect_ratio": ["1:1", "16:9"],
                    "thinking_level": ["minimal", "high"],
                    "google_search": True,
                    "image_search": True,
                    "max_reference_images": 14,
                },
                "enabled": True,
            },
            {
                "model_id": "gemini-3-pro-image-preview",
                "capabilities": {
                    "n_max": 1,
                    "image_size": ["1K", "2K", "4K"],
                },
                "enabled": True,
            },
        ],
        "tier_access": ["vip", "premium", "standard"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/providers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_forbidden_for_user(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/providers", headers=_auth(token)
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_create_forbidden_for_user(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create — happy path + persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_provider_happy_path(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["id"] == "bltcy"
    assert body["adapter_type"] == "gemini_v1beta"
    assert body["balance_cny"] == 3.88
    assert body["initial_balance_cny"] == 3.88
    assert body["circuit_state"] == "healthy"
    # Cleartext key never leaks; only the masked form is exposed.
    assert "api_key" not in body
    assert body["api_key_masked"] == "sk-***1234"
    assert {m["model_id"] for m in body["supported_models"]} == {
        "gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview",
    }
    # Tier list is sorted in the view.
    assert body["tier_access"] == ["premium", "standard", "vip"]


@pytest.mark.asyncio
async def test_created_api_key_stored_encrypted(
    seeded_app: httpx.AsyncClient,
) -> None:
    """The DB column must NOT contain the cleartext key."""
    from app.db.engine import get_session
    from app.db.models import Provider
    from app.utils.crypto import decrypt

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == "bltcy"))
        ).scalar_one()

    # Stored value is opaque ciphertext + v1: prefix.
    assert row.api_key_enc.startswith("v1:")
    assert "sk-bltcy-secret-FQBR1234" not in row.api_key_enc
    assert "FQBR" not in row.api_key_enc
    # Round-trip via the helper recovers the cleartext.
    assert decrypt(row.api_key_enc) == "sk-bltcy-secret-FQBR1234"


@pytest.mark.asyncio
async def test_create_writes_audit_log(seeded_app: httpx.AsyncClient) -> None:
    from app.db.engine import get_session
    from app.db.models import AuditLog

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    async with get_session() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "provider.create")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_id == "bltcy"


# ---------------------------------------------------------------------------
# Create — validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_unknown_adapter_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(adapter_type="not_a_real_adapter"),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"
    assert resp.json()["detail"]["field"] == "adapter_type"


@pytest.mark.asyncio
async def test_create_model_unsupported_by_adapter_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Pinning an OpenAI-only model on a Gemini adapter must fail."""
    token = await _login_admin(seeded_app)
    payload = _gemini_provider_payload()
    payload["supported_models"] = [
        {"model_id": "gpt-image-2", "capabilities": {}}
    ]
    resp = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=payload
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "supported_models"


@pytest.mark.asyncio
async def test_create_invalid_provider_id_format(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(provider_id="With Spaces"),
    )
    # Pydantic shape rejection — FastAPI returns 422 with its own
    # validator envelope; we just assert non-2xx + that the field
    # provider_id is mentioned in the body somewhere.
    assert resp.status_code in (400, 422)
    assert "provider_id" in resp.text


@pytest.mark.asyncio
async def test_create_invalid_tier_in_access(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(tier_access=["vip", "platinum"]),
    )
    assert resp.status_code == 422
    assert "tier_access" in resp.text


@pytest.mark.asyncio
async def test_create_duplicate_provider_id_returns_409(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    first = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    assert first.status_code == 201
    second = await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "ALREADY_EXISTS"


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_masks_api_keys(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.get(
        "/api/admin/providers", headers=_auth(token)
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert "api_key" not in items[0]
    assert items[0]["api_key_masked"].endswith("1234")


@pytest.mark.asyncio
async def test_get_single_provider(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.get(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "bltcy"


@pytest.mark.asyncio
async def test_get_unknown_provider_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/providers/missing", headers=_auth(token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Patch (basic fields + api_key rotation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_basic_fields(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy",
        headers=_auth(token),
        json={"label": "BLTCY-NEW", "max_concurrency": 100},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "BLTCY-NEW"
    assert body["max_concurrency"] == 100
    # Untouched fields preserved.
    assert body["base_url"] == "https://api.bltcy.ai/v1beta"


@pytest.mark.asyncio
async def test_patch_rotates_api_key_in_place(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Sending ``api_key`` re-encrypts; the masked form changes accordingly."""
    from app.db.engine import get_session
    from app.db.models import Provider
    from app.utils.crypto import decrypt

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    new_key = "sk-rotated-secret-XYZW9999"
    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy",
        headers=_auth(token),
        json={"api_key": new_key},
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_masked"].endswith("9999")

    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == "bltcy"))
        ).scalar_one()
    assert decrypt(row.api_key_enc) == new_key


@pytest.mark.asyncio
async def test_patch_empty_body_returns_400(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy", headers=_auth(token), json={}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_unknown_provider_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/admin/providers/nope",
        headers=_auth(token),
        json={"label": "x"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete (with cascade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cascades_models_and_tier_access(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import (
        Provider,
        ProviderModel,
        ProviderTierAccess,
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.delete(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert resp.status_code == 200

    async with get_session() as session:
        assert (
            await session.execute(
                select(Provider).where(Provider.id == "bltcy")
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(ProviderModel).where(
                    ProviderModel.provider_id == "bltcy"
                )
            )
        ).all() == []
        assert (
            await session.execute(
                select(ProviderTierAccess).where(
                    ProviderTierAccess.provider_id == "bltcy"
                )
            )
        ).all() == []


@pytest.mark.asyncio
async def test_delete_unknown_returns_404(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.delete(
        "/api/admin/providers/missing", headers=_auth(token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-model patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_provider_model_capabilities(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy/models/gemini-3-pro-image-preview",
        headers=_auth(token),
        json={
            "capabilities": {
                "n_max": 1,
                "image_size": ["2K", "4K"],
                "google_search": False,
            }
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"] == "gemini-3-pro-image-preview"
    assert body["capabilities"]["image_size"] == ["2K", "4K"]
    assert body["capabilities"]["google_search"] is False
    assert body["enabled"] is True


@pytest.mark.asyncio
async def test_patch_provider_model_disable(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy/models/gemini-3-pro-image-preview",
        headers=_auth(token),
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_patch_unknown_provider_model_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy/models/no-such-model",
        headers=_auth(token),
        json={"enabled": False},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tier-access patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_tier_access_replaces_set(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),  # vip + premium + standard
    )

    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy/tier-access",
        headers=_auth(token),
        json={"tiers": ["vip"]},
    )
    assert resp.status_code == 200
    assert resp.json()["tiers"] == ["vip"]

    # Confirm the GET reflects the replacement.
    again = await seeded_app.get(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert again.json()["tier_access"] == ["vip"]


@pytest.mark.asyncio
async def test_patch_tier_access_empty_list_clears_all(
    seeded_app: httpx.AsyncClient,
) -> None:
    """An empty list intentionally locks the provider out of every tier."""
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy/tier-access",
        headers=_auth(token),
        json={"tiers": []},
    )
    assert resp.status_code == 200
    again = await seeded_app.get(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert again.json()["tier_access"] == []


# ---------------------------------------------------------------------------
# Topup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topup_endpoint_increments_balance(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/topup",
        headers=_auth(token),
        json={"amount_cny": 5.12},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["amount_cny"] == 5.12
    assert body["balance_after"] == pytest.approx(3.88 + 5.12)
    assert body["promoted_from_drained"] is False


@pytest.mark.asyncio
async def test_topup_promotes_drained_to_healthy(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Setting state=drained directly, then topup, must reset to healthy."""
    from app.db.engine import get_session
    from app.db.models import Provider

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == "bltcy"))
        ).scalar_one()
        row.circuit_state = "drained"
        row.balance_cny = 0.0

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/topup",
        headers=_auth(token),
        json={"amount_cny": 10.0},
    )
    assert resp.status_code == 200
    assert resp.json()["promoted_from_drained"] is True

    again = await seeded_app.get(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert again.json()["circuit_state"] == "healthy"


@pytest.mark.asyncio
async def test_topup_zero_amount_rejected(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/topup",
        headers=_auth(token),
        json={"amount_cny": 0},
    )
    # Pydantic catches this with a 422 (gt=0 constraint).
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_topup_unknown_provider_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers/missing/topup",
        headers=_auth(token),
        json={"amount_cny": 1.0},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/admin/adapters now reflects in_use_by_providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapters_endpoint_shows_in_use_after_create(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    resp = await seeded_app.get(
        "/api/admin/adapters", headers=_auth(token)
    )
    by_type = {e["adapter_type"]: e for e in resp.json()}
    assert by_type["gemini_v1beta"]["in_use_by_providers"] == ["bltcy"]
    # Other adapter still unused.
    assert by_type["openai_v1"]["in_use_by_providers"] == []


# ---------------------------------------------------------------------------
# Review-driven hardening (Copilot PR #34)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_explicit_null_on_non_nullable_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    """PATCH must reject ``{"label": null}`` rather than overwriting with NULL."""
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    for field in [
        "label",
        "base_url",
        "cost_per_image_cny",
        "enabled",
        "max_concurrency",
        "rpm_limit",
    ]:
        resp = await seeded_app.patch(
            "/api/admin/providers/bltcy",
            headers=_auth(token),
            json={field: None},
        )
        assert resp.status_code == 422, f"{field}: {resp.text}"
        assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"
        assert resp.json()["detail"]["field"] == field


@pytest.mark.asyncio
async def test_patch_note_can_be_cleared_with_null(
    seeded_app: httpx.AsyncClient,
) -> None:
    """``note`` is the only nullable column — null clears it."""
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    resp = await seeded_app.patch(
        "/api/admin/providers/bltcy",
        headers=_auth(token),
        json={"note": None},
    )
    assert resp.status_code == 200
    assert resp.json()["note"] is None


@pytest.mark.asyncio
async def test_get_provider_with_unreadable_key_returns_500(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A row whose ciphertext can't decrypt must surface a server error,
    not a degraded ``api_key_masked='<unreadable>'`` payload."""
    from app.db.engine import get_session
    from app.db.models import Provider

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )
    # Corrupt the ciphertext to force a decrypt failure.
    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == "bltcy"))
        ).scalar_one()
        row.api_key_enc = "v1:not-real-ciphertext"

    resp = await seeded_app.get(
        "/api/admin/providers/bltcy", headers=_auth(token)
    )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_list_providers_one_query_per_join(
    seeded_app: httpx.AsyncClient,
) -> None:
    """List must bulk-fetch joins: O(1) queries regardless of row count.

    We seed three providers and assert the SQL log contains exactly one
    SELECT against ``provider_models`` and one against
    ``provider_tier_access``, regardless of provider count.
    """
    from sqlalchemy import event

    from app.db.engine import get_engine

    token = await _login_admin(seeded_app)

    # Seed three providers with distinct ids.
    for idx, pid in enumerate(("p_one", "p_two", "p_three")):
        payload = _gemini_provider_payload(provider_id=pid)
        resp = await seeded_app.post(
            "/api/admin/providers", headers=_auth(token), json=payload
        )
        assert resp.status_code == 201, resp.text

    # Capture every SQL statement on the underlying sync engine for the
    # duration of the LIST call.
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        resp = await seeded_app.get(
            "/api/admin/providers", headers=_auth(token)
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert resp.status_code == 200
    assert len(resp.json()) == 3

    # One SELECT for providers, one for provider_models, one for
    # provider_tier_access.  Other statements (BEGIN / COMMIT / PRAGMA)
    # are tolerated.
    pm_selects = [s for s in statements if "FROM provider_models" in s]
    pta_selects = [s for s in statements if "FROM provider_tier_access" in s]
    assert len(pm_selects) == 1, statements
    assert len(pta_selects) == 1, statements


@pytest.mark.asyncio
async def test_topup_provider_no_redundant_select(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Topup endpoint must run only one SELECT (inside the ledger) and
    one UPDATE — no duplicated ``providers`` SELECT from a pre-load."""
    from sqlalchemy import event

    from app.db.engine import get_engine

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json=_gemini_provider_payload(),
    )

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        resp = await seeded_app.post(
            "/api/admin/providers/bltcy/topup",
            headers=_auth(token),
            json={"amount_cny": 1.0},
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert resp.status_code == 200
    provider_selects = [
        s for s in statements
        if "FROM providers" in s and "SELECT" in s
    ]
    # The ledger does one pre-state SELECT for the promoted detection;
    # the route layer no longer re-loads on top of it.
    assert len(provider_selects) == 1, statements
