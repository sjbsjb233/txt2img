"""Tests for the /api/client-logs ingestion endpoint."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> None:
    from app.api.client_logs import reset_rate_limit_for_tests

    reset_rate_limit_for_tests()
    yield
    reset_rate_limit_for_tests()


@pytest.mark.asyncio
async def test_anonymous_can_post_basic_batch(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/client-logs",
        json={
            "items": [
                {"level": "error", "msg": "boom", "route": "/create"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] == 1
    assert body["dropped"] == 0


@pytest.mark.asyncio
async def test_authenticated_post_records_user_id(
    seeded_app: httpx.AsyncClient,
) -> None:
    login = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await seeded_app.post(
        "/api/client-logs",
        json={
            "items": [
                {"level": "info", "msg": "page mount", "route": "/dashboard"},
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1


@pytest.mark.asyncio
async def test_empty_items_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/client-logs",
        json={"items": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_huge_batch_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    items = [
        {"level": "info", "msg": f"line {i}"}
        for i in range(101)
    ]
    resp = await seeded_app.post(
        "/api/client-logs", json={"items": items}
    )
    assert resp.status_code in (413, 422)


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_quota(
    seeded_app: httpx.AsyncClient,
) -> None:
    # Anonymous limit defaults to 30; send 31 to trip it.
    for _ in range(30):
        ok = await seeded_app.post(
            "/api/client-logs",
            json={"items": [{"level": "info", "msg": "tick"}]},
        )
        assert ok.status_code == 200
    over = await seeded_app.post(
        "/api/client-logs",
        json={"items": [{"level": "info", "msg": "tock"}]},
    )
    assert over.status_code == 429


@pytest.mark.asyncio
async def test_oversized_item_dropped_not_failed(
    seeded_app: httpx.AsyncClient,
) -> None:
    big = "x" * 1900
    resp = await seeded_app.post(
        "/api/client-logs",
        json={
            "items": [
                {"level": "info", "msg": big, "stack": "y" * 7800},
                {"level": "info", "msg": "ok"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # The first item still fits the 8KB default; both should land.
    assert body["accepted"] >= 1


@pytest.mark.asyncio
async def test_bad_json_returns_400(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/client-logs",
        content=b"not-json-at-all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_text_body_is_parsed_for_beacon(
    seeded_app: httpx.AsyncClient,
) -> None:
    """``navigator.sendBeacon`` posts text/plain payloads."""
    body = (
        '{"items": [{"level": "warn", "msg": "beacon test"}]}'
    )
    resp = await seeded_app.post(
        "/api/client-logs",
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1
