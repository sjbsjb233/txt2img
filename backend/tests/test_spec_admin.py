"""T-ADMIN-NN spec cases (文生图平台测试方案 §5.16)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.jobs import insert_terminal_job
from tests.infra.seeds import auth, install_fake_provider, login_admin, login_user


pytestmark = [pytest.mark.admin]


# ---------------------------------------------------------------------------
# T-ADMIN-01 · scoring weights must sum to 1
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_admin_01_weights_sum_validation(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    r = await seeded_app.patch(
        "/api/admin/config",
        headers=auth(admin),
        json={"updates": {
            "provider_scoring.weights.cost": 0.10,
            "provider_scoring.weights.success": 0.10,
            "provider_scoring.weights.latency": 0.10,
            "provider_scoring.weights.load": 0.10,
            "provider_scoring.weights.freshness": 0.55,  # sum=0.95
        }},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# T-ADMIN-03 · cleanup suggestions surface real categories
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_admin_03_cleanup_suggestions(seeded_app: httpx.AsyncClient):
    from datetime import datetime, timedelta, timezone

    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, tier="vip")
    # Insert an old job
    await insert_terminal_job(
        user.id,
        tier="vip",
        seq_no=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=40),
    )
    r = await seeded_app.get(
        "/api/admin/cleanup/suggestions", headers=auth(admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    suggestions = body.get("suggestions", [])
    # Each suggestion has period_label / rule / job_count / image_count / disk_bytes
    assert suggestions, "expected at least one suggestion when old jobs exist"
    s0 = suggestions[0]
    for k in ("period_label", "rule", "job_count", "image_count",
              "disk_bytes", "disk_human"):
        assert k in s0, s0
    # At least one suggestion should be a meaningful "older than" rule
    assert any(s["rule"].get("kind") == "older_than_days" for s in suggestions)


# ---------------------------------------------------------------------------
# T-ADMIN-06 · audit log records admin disable
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_admin_06_audit_writes(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, tier="vip", username="aud06")
    r = await seeded_app.post(
        f"/api/admin/users/{user.id}/disable", headers=auth(admin)
    )
    assert r.status_code == 200, r.text

    audit = await seeded_app.get(
        "/api/admin/audit", headers=auth(admin)
    )
    assert audit.status_code == 200, audit.text
    items = audit.json().get("items") or audit.json().get("audit") or []
    assert any(
        it.get("target_id") == user.id and "disable" in (it.get("action") or "")
        for it in items
    )


# ---------------------------------------------------------------------------
# T-ADMIN-07 · impersonate token has 30-min ttl
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_admin_07_impersonate_ttl(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, tier="vip", username="imp07")
    r = await seeded_app.post(
        f"/api/admin/users/{user.id}/impersonate", headers=auth(admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body.get("token") or body.get("access_token")
    assert token

    from app.utils.security import decode_access_token

    payload = decode_access_token(token)
    ttl = payload["exp"] - payload["iat"]
    # ~30 min target, allow ±2min jitter
    assert 1700 <= ttl <= 1900, ttl
    assert payload.get("impersonator")


# ---------------------------------------------------------------------------
# T-ADMIN-09 · regular user → admin SSE 403
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.err
async def test_t_admin_09_user_admin_sse_forbidden(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.get("/api/admin/sse", headers=auth(token))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# T-ADMIN-10 · bulk patch user tier
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_admin_10_bulk_tier_change(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    A, _ = await login_user(seeded_app, tier="standard", username="b10a")
    B, _ = await login_user(seeded_app, tier="standard", username="b10b")
    r = await seeded_app.post(
        "/api/admin/users/bulk",
        headers=auth(admin),
        json={"ids": [A.id, B.id], "patch": {"tier": "premium"}},
    )
    assert r.status_code == 200, r.text

    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    async with get_session() as s:
        a = (await s.execute(select(User).where(User.id == A.id))).scalar_one()
        b = (await s.execute(select(User).where(User.id == B.id))).scalar_one()
    assert a.tier == "premium"
    assert b.tier == "premium"


# ---------------------------------------------------------------------------
# T-ADMIN-11 · adapter list shows in_use_by_providers
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_admin_11_adapter_in_use(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)
    r = await seeded_app.get("/api/admin/adapters", headers=auth(admin))
    assert r.status_code == 200, r.text
    items = r.json()
    fake = next(a for a in items if a.get("adapter_type") == "fake")
    assert "fake-1" in (fake.get("in_use_by_providers") or [])
