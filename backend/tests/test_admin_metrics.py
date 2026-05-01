"""Tests for ``/api/admin/metrics`` (PR-17)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


async def _seed_jobs(
    n: int,
    *,
    user_id: str,
    status: str = "SUCCEEDED",
    created_at: datetime | None = None,
) -> list[str]:
    """Insert ``n`` Job rows for the given user."""
    from app.db.engine import get_session
    from app.db.models import Job, User
    from app.utils.ids import new_job_hash_id, new_job_internal_id
    from sqlalchemy import select

    when = created_at or datetime.now(timezone.utc)
    ids: list[str] = []
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        for i in range(n):
            user.last_seq_no += 1
            job_id = new_job_internal_id()
            ids.append(job_id)
            session.add(
                Job(
                    id=job_id,
                    hash_id=new_job_hash_id(),
                    user_id=user_id,
                    tier_at_submit=user.tier,
                    seq_no=user.last_seq_no,
                    model="gemini-3.1-flash-image-preview",
                    params_json="{}",
                    flags_json="{}",
                    status=status,
                    created_at=when,
                    started_at=when,
                    finished_at=when + timedelta(seconds=2 + i),
                    cost_cny=0.1,
                )
            )
    return ids


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_returns_required_blocks(seeded_app):
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/metrics/overview", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in (
        "active_users_today",
        "jobs_today",
        "images_today",
        "success_rate_24h",
        "queue_state",
        "worker_pool",
        "disk_usage",
        "providers_summary",
    ):
        assert key in data
    assert data["worker_pool"]["max"] >= 1
    # Lanes for all four tiers should be present (even if zero).
    for tier in ("vip", "premium", "standard", "free"):
        assert tier in data["queue_state"]
        assert "queued" in data["queue_state"][tier]
        assert "running" in data["queue_state"][tier]


@pytest.mark.asyncio
async def test_overview_jobs_today_counts(seeded_app):
    """Jobs created in the current Beijing day land in jobs_today."""
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == "admin")
            )
        ).scalar_one()
        uid = admin.id
    await _seed_jobs(3, user_id=uid)
    resp = await seeded_app.get(
        "/api/admin/metrics/overview", headers=_auth(token)
    )
    data = resp.json()
    assert data["jobs_today"] >= 3


@pytest.mark.asyncio
async def test_overview_requires_admin(seeded_app):
    # Forge a token-less request so the guard fires the same way it
    # would for any non-admin caller.
    resp = await seeded_app.get("/api/admin/metrics/overview")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Timeseries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeseries_jobs_count(seeded_app):
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == "admin")
            )
        ).scalar_one()
        uid = admin.id

    await _seed_jobs(5, user_id=uid)

    resp = await seeded_app.get(
        "/api/admin/metrics/timeseries",
        params={"metric": "jobs_count", "range": "24h", "bucket": "1h"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["metric"] == "jobs_count"
    assert data["range"] == "24h"
    assert data["bucket"] == "1h"
    # 24h / 1h = 24 buckets
    assert len(data["points"]) == 24
    # At least one point must hold >= 5 jobs.
    nonzero = [p for p in data["points"] if (p["value"] or 0) >= 5]
    assert nonzero


@pytest.mark.asyncio
async def test_timeseries_invalid_combo_rejected(seeded_app):
    token = await _login_admin(seeded_app)
    # 30d / 1m would request 43200 points — way over the cap.
    resp = await seeded_app.get(
        "/api/admin/metrics/timeseries",
        params={"metric": "jobs_count", "range": "30d", "bucket": "1m"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_timeseries_unknown_metric_rejected(seeded_app):
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/metrics/timeseries",
        params={"metric": "totally-unknown", "range": "24h", "bucket": "1h"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_timeseries_success_rate(seeded_app):
    """``success_rate`` should produce a value in [0,1] for buckets with
    samples and ``None`` for empty buckets."""
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    async with get_session() as session:
        admin = (
            await session.execute(
                select(User).where(User.username == "admin")
            )
        ).scalar_one()
        uid = admin.id
    await _seed_jobs(3, user_id=uid, status="SUCCEEDED")
    await _seed_jobs(1, user_id=uid, status="FAILED")

    resp = await seeded_app.get(
        "/api/admin/metrics/timeseries",
        params={"metric": "success_rate", "range": "24h", "bucket": "1h"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    points = resp.json()["points"]
    populated = [p for p in points if p["value"] is not None]
    assert populated  # at least one bucket has data
    for p in populated:
        assert 0.0 <= p["value"] <= 1.0
