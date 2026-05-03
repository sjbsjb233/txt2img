"""Smoke tests for ``GET /api/admin/adapters``.

The route is read-only — we cover the auth gate and the happy path that
the two built-in adapters surface in the response. Population of
``in_use_by_providers`` is tested in PR-06 once provider rows exist.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/adapters")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_sees_built_in_adapters(seeded_app: httpx.AsyncClient) -> None:
    """Bootstrap admin can list adapters; both built-ins are present."""
    login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    resp = await seeded_app.get(
        "/api/admin/adapters",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    types = {entry["adapter_type"] for entry in body}
    assert "openai_v1" in types
    assert "gemini_v1beta" in types

    by_type = {entry["adapter_type"]: entry for entry in body}
    assert "gpt-image-2" in by_type["openai_v1"]["supported_models"]
    assert (
        "gemini-3.1-flash-image-preview"
        in by_type["gemini_v1beta"]["supported_models"]
    )
    # in_use_by_providers must be empty until PR-06 inserts provider rows.
    assert by_type["openai_v1"]["in_use_by_providers"] == []
    assert by_type["gemini_v1beta"]["in_use_by_providers"] == []

    # Each adapter advertises a self-describing capability schema
    # consumed by the provider editor UI.
    for atype, entry in by_type.items():
        assert "capability_schema" in entry, (
            f"adapter {atype} missing capability_schema in /api/admin/adapters"
        )
        assert isinstance(entry["capability_schema"], list)
        for f in entry["capability_schema"]:
            assert "k" in f and "kind" in f
            assert f["kind"] in ("list", "int", "bool")

    # Spot-check that openai_v1 and gemini_v1beta surface their
    # signature-only fields and don't leak the other's.
    openai_keys = {f["k"] for f in by_type["openai_v1"]["capability_schema"]}
    gemini_keys = {f["k"] for f in by_type["gemini_v1beta"]["capability_schema"]}
    assert "size" in openai_keys and "quality" in openai_keys
    assert "aspect_ratio" not in openai_keys
    assert "aspect_ratio" in gemini_keys and "thinking_level" in gemini_keys
    assert "size" not in gemini_keys
    assert "supports_mask" not in gemini_keys


@pytest.mark.asyncio
async def test_non_admin_user_forbidden(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A regular ``role='user'`` account gets 403."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_regular_test",
                username="not_admin",
                password_hash=hash_password("hunter2-pass"),
                role="user",
                tier="free",
                today_reset_date="2026-01-01",
            )
        )

    login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "not_admin", "password": "hunter2-pass"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    resp = await seeded_app.get(
        "/api/admin/adapters",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "FORBIDDEN"
