"""End-to-end tests for the request-id middleware."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_health_response_carries_request_id(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get("/api/health")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert rid, "X-Request-ID echo header must be set"
    # 32-char hex (uuid4.hex) or whatever the client injected.
    assert len(rid) >= 8


@pytest.mark.asyncio
async def test_inbound_request_id_is_preserved(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get(
        "/api/health",
        headers={"X-Request-ID": "test-rid-123"},
    )
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert rid == "test-rid-123"


@pytest.mark.asyncio
async def test_bogus_request_id_is_sanitised(
    seeded_app: httpx.AsyncClient,
) -> None:
    # Newlines / spaces / spaghetti — middleware should strip and clamp.
    raw = "  bogus !@#$%^ <script>".strip()
    resp = await seeded_app.get(
        "/api/health",
        headers={"X-Request-ID": raw},
    )
    rid = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert rid is not None
    assert "<" not in rid
    assert " " not in rid
    assert "!" not in rid


@pytest.mark.asyncio
async def test_failed_login_still_gets_id(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong"},
    )
    assert resp.status_code == 401
    rid = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert rid
