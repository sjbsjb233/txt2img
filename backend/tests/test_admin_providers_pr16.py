"""Tests for the PR-16 additions to the admin/providers router:

- ``GET /api/admin/providers`` now returns live ``metrics`` per model
  + ``current_concurrency`` + ``recent_calls_60s``.
- ``POST /api/admin/providers/<id>/reset-circuit`` flips circuit_state
  back to ``healthy``.
- ``POST /api/admin/providers/<id>/test`` runs a one-shot probe via
  the configured adapter, without touching the ledger or metrics.

Tests in this file deliberately don't replicate the PR-06 CRUD coverage
in ``test_admin_providers.py`` — they only exercise what PR-16 adds.
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provider_payload(**overrides) -> dict:
    base = {
        "provider_id": "bltcy",
        "label": "BLTCY",
        "adapter_type": "gemini_v1beta",
        "base_url": "https://api.bltcy.ai/v1beta",
        "api_key": "sk-bltcy-secret-FQBR1234",
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 5.0,
        "enabled": True,
        "max_concurrency": 70,
        "rpm_limit": 600,
        "supported_models": [
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {"n_max": 1, "image_size": ["1K"]},
                "enabled": True,
            }
        ],
        "tier_access": ["vip", "premium"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# List response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_metrics_block(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Even with no traffic, list responses must include the metrics shape."""
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201, create.text

    resp = await seeded_app.get(
        "/api/admin/providers", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert "metrics" in item
    assert "current_concurrency" in item
    assert "recent_calls_60s" in item
    assert isinstance(item["metrics"], list)
    assert len(item["metrics"]) == 1
    m = item["metrics"][0]
    assert m["model_id"] == "gemini-3.1-flash-image-preview"
    # No traffic → 1.0 default; calls=0; latency percentiles None.
    assert m["calls"] == 0
    assert m["success_rate"] == 1.0
    assert m["p50_ms"] is None
    assert m["p95_ms"] is None


@pytest.mark.asyncio
async def test_list_reflects_recorded_metric(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.domain.metrics_engine import get_metrics_engine

    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201, create.text

    metrics = get_metrics_engine()
    metrics.record_call(
        "bltcy",
        "gemini-3.1-flash-image-preview",
        ok=True,
        latency_ms=420.0,
    )
    metrics.record_call(
        "bltcy",
        "gemini-3.1-flash-image-preview",
        ok=False,
        latency_ms=10000.0,
        error_kind="UPSTREAM_TIMEOUT",
    )

    resp = await seeded_app.get(
        "/api/admin/providers", headers=_auth(token)
    )
    item = resp.json()[0]
    metric = item["metrics"][0]
    assert metric["calls"] == 2
    # 1 of 2 succeeded.
    assert metric["success_rate"] == 0.5
    # p50 only over successful calls.
    assert metric["p50_ms"] == 420.0


# ---------------------------------------------------------------------------
# Reset circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_circuit_returns_healthy(
    seeded_app: httpx.AsyncClient,
) -> None:
    from datetime import datetime, timedelta, timezone
    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import update

    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201

    # Force OPEN state directly in DB so we don't have to flap an
    # adapter five times to get the breaker there.
    cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=300)
    async with get_session() as session:
        await session.execute(
            update(Provider)
            .where(Provider.id == "bltcy")
            .values(circuit_state="open", cooldown_until=cooldown_until)
        )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/reset-circuit",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["circuit_state"] == "healthy"
    assert body["cooldown_until"] is None


@pytest.mark.asyncio
async def test_reset_circuit_unknown_provider_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/providers/missing/reset-circuit",
        headers=_auth(token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_test_uses_default_model_when_none_supplied(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """``POST /test`` with no body picks the provider's first enabled model."""
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedResponse

    captured: dict[str, str] = {}

    async def fake_generate(self, provider, request):
        captured["model"] = request.model
        captured["provider_id"] = provider.id
        return NormalizedResponse(images=[], image_count=1)

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test",
        headers=_auth(token),
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["image_count"] == 1
    assert body["model_id"] == "gemini-3.1-flash-image-preview"
    assert captured["model"] == "gemini-3.1-flash-image-preview"
    assert captured["provider_id"] == "bltcy"


@pytest.mark.asyncio
async def test_provider_test_unknown_model_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test",
        headers=_auth(token),
        json={"model_id": "not-on-this-provider"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_provider_test_does_not_touch_ledger(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    from app.adapters import gemini_v1beta as gemini_mod
    from app.db.engine import get_session
    from app.db.models import BillingLedger, Provider
    from app.schemas.normalized import NormalizedResponse
    from sqlalchemy import select

    async def fake_generate(self, provider, request):
        return NormalizedResponse(images=[], image_count=1)

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test",
        headers=_auth(token),
        json={},
    )
    assert resp.status_code == 200, resp.text

    # Balance untouched, ledger empty.
    async with get_session() as session:
        bal = (
            await session.execute(
                select(Provider.balance_cny).where(Provider.id == "bltcy")
            )
        ).scalar_one()
        ledger_rows = (
            await session.execute(select(BillingLedger))
        ).scalars().all()
    assert bal == pytest.approx(5.0)
    assert ledger_rows == []


@pytest.mark.asyncio
async def test_provider_test_reports_upstream_error_kind(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import StandardError, StandardErrorKind

    async def fake_generate(self, provider, request):
        raise StandardError(
            StandardErrorKind.RATE_LIMITED, "throttled by upstream"
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test",
        headers=_auth(token),
        json={},
    )
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["ok"] is False
    assert body["error_kind"] == "RATE_LIMITED"
