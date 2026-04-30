"""Tests for the admin SSE channel + diagnostics route."""

from __future__ import annotations

import httpx
import pytest


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    return resp.json()["access_token"]


async def _login_user(client: httpx.AsyncClient) -> str:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_norm_ssetest",
                username="bob_sse",
                password_hash=hash_password("bobpw1234"),
                role="user",
                tier="free",
            )
        )
    resp = await client.post(
        "/api/auth/login",
        json={"username": "bob_sse", "password": "bobpw1234"},
    )
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_clients_endpoint_admin_only(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get("/api/admin/sse/clients")
    assert resp.status_code == 401

    user_token = await _login_user(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/sse/clients", headers=_auth(user_token)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_clients_endpoint_returns_summary(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/sse/clients", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_clients"] == 0
    assert body["users_with_clients"] == 0
    assert body["max_per_user"] >= 1
    assert body["heartbeat_seconds"] >= 1
    assert isinstance(body["clients"], list)
