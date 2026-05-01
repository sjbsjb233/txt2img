"""Tests for the admin audit endpoint (PR-17)."""

from __future__ import annotations

import httpx
import pytest


ADMIN_PASSWORD = "test-admin-password"


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_audit_returns_recent_writes(seeded_app):
    """Make any write that emits an audit row, then read the feed."""
    token = await _login_admin(seeded_app)
    # A config patch is the cheapest write that produces an audit row.
    patch = await seeded_app.patch(
        "/api/admin/config",
        json={"thumbnail.quality": 80},
        headers=_auth(token),
    )
    assert patch.status_code == 200, patch.text

    audit = await seeded_app.get("/api/admin/audit", headers=_auth(token))
    assert audit.status_code == 200
    data = audit.json()
    assert data["total"] >= 1
    assert any(r["action"] == "config.update" for r in data["items"])
    # Username is resolved on the server side.
    assert all(r["actor_username"] == "admin" for r in data["items"])


@pytest.mark.asyncio
async def test_audit_action_prefix_filter(seeded_app):
    token = await _login_admin(seeded_app)
    await seeded_app.patch(
        "/api/admin/config",
        json={"thumbnail.quality": 90},
        headers=_auth(token),
    )
    # Match config.* — should hit the row above.
    resp = await seeded_app.get(
        "/api/admin/audit",
        params={"action": "config.*"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(it["action"].startswith("config.") for it in items)


@pytest.mark.asyncio
async def test_audit_unknown_actor_returns_empty(seeded_app):
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/audit",
        params={"actor": "u_doesnotexist"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_audit_pagination(seeded_app):
    token = await _login_admin(seeded_app)
    # Several writes to grow the feed.
    for q in (60, 70, 80, 90, 95):
        resp = await seeded_app.patch(
            "/api/admin/config",
            json={"thumbnail.quality": q},
            headers=_auth(token),
        )
        assert resp.status_code == 200

    page1 = await seeded_app.get(
        "/api/admin/audit",
        params={"page": 1, "page_size": 2},
        headers=_auth(token),
    )
    assert page1.status_code == 200
    assert len(page1.json()["items"]) == 2

    # Total ≥ 5 (config writes) — exact count depends on fixture.
    assert page1.json()["total"] >= 5


@pytest.mark.asyncio
async def test_audit_requires_admin(seeded_app):
    """Non-admin users get 401/403; anonymous → 401."""
    resp = await seeded_app.get("/api/admin/audit")
    assert resp.status_code == 401
