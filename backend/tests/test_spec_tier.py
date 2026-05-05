"""T-TIER-NN spec cases (文生图平台测试方案 §5.3)."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.fake_turnstile import patch_turnstile
from tests.infra.jobs import insert_terminal_job
from tests.infra.seeds import (
    ADMIN_PW,
    auth,
    install_fake_provider,
    login_admin,
    login_user,
)


pytestmark = [pytest.mark.tier]


def _job_payload(**overrides):
    base = {
        "model": "gpt-image-2",
        "prompt": "a tall ship at sea",
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
        "background": "auto",
    }
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-TIER-01 · default 4 tiers match spec §3.1 numbers
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_01_defaults(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    r = await seeded_app.get("/api/admin/tiers", headers=auth(admin))
    assert r.status_code == 200
    by_tier = {t["tier"]: t for t in r.json()["tiers"]}
    assert by_tier["vip"] == {
        "tier": "vip",
        "weight": 8,
        "max_concurrency": 4,
        "max_queue": 10,
        "soft_quota": 100,
        "hard_quota": 200,
        "slo_p95_ms": 30_000,
    }
    assert by_tier["free"]["weight"] == 1
    assert by_tier["free"]["soft_quota"] == 8
    assert by_tier["free"]["hard_quota"] == 10


# ---------------------------------------------------------------------------
# T-TIER-02 · admin PATCH triggers hot reload — no restart
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_02_patch_hot_reload(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)

    # Tighten free.hard_quota=2 first.
    r = await seeded_app.patch(
        "/api/admin/tiers/free",
        headers=auth(admin),
        json={"hard_quota": 2, "soft_quota": 1},
    )
    assert r.status_code == 200, r.text

    # Free user with today_count=2 should hit hard immediately
    user, token = await login_user(seeded_app, tier="free", today_count=2)
    r = await seeded_app.post(
        "/api/jobs/precheck",
        headers=auth(token),
        json={"model": "gpt-image-2"},
    )
    # precheck doesn't enforce hard quota; but submitting should
    files = {"payload": (None, _job_payload(), "application/json")}
    r2 = await seeded_app.post(
        "/api/jobs", headers=auth(token), files=files
    )
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "HARD_QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# T-TIER-03 · hard quota → 429 HARD_QUOTA_EXCEEDED
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_03_hard_quota_blocks(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="free", today_count=10)
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "HARD_QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# T-TIER-04 · soft quota does NOT block, attaches flag
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_04_soft_quota_attaches_flag(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    patch_turnstile(monkeypatch)
    # Free soft=8, hard=10
    user, token = await login_user(seeded_app, tier="free", today_count=8)
    # Provide captcha because soft penalty requires it
    files = {
        "payload": (
            None,
            _job_payload(captcha_token="OK-soft"),
            "application/json",
        )
    }
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 200, r.text
    hash_id = r.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as s:
        row = (
            await s.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    flags = json.loads(row.flags_json)
    assert flags.get("SOFT_QUOTA_EXCEEDED") is True


# ---------------------------------------------------------------------------
# T-TIER-05 · per-user override beats tier default
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_tier_05_user_override_beats_tier(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(
        seeded_app,
        tier="free",
        today_count=3,
        override_hard_quota=3,
        override_soft_quota=3,
    )
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "HARD_QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# T-TIER-06 · daily reset (logical) — increment then reset row date
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_tier_06_today_count_resets_on_new_day(
    seeded_app: httpx.AsyncClient,
):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Insert user with stale today_reset_date — should auto-reset on next
    # quota check (per quota_guard.today_count logic).
    from datetime import date, timedelta

    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import update, select

    user, token = await login_user(seeded_app, tier="free", today_count=10)
    yesterday = date.today() - timedelta(days=2)
    async with get_session() as s:
        await s.execute(
            update(User).where(User.id == user.id).values(today_reset_date=yesterday)
        )

    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    # After auto-reset today_count=0, so submission allowed.
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# T-TIER-07 · USER_BUSY when capacity (concurrency + queue) full
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_07_user_busy(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Standard tier: max_concurrency=1, max_queue=3 → capacity 4
    user, token = await login_user(seeded_app, tier="standard")
    # Pre-insert 4 active (QUEUED) jobs
    from app.db.engine import get_session
    from app.db.models import Job
    from app.utils.ids import new_job_hash_id, new_job_internal_id

    async with get_session() as s:
        for i in range(4):
            s.add(
                Job(
                    id=new_job_internal_id(),
                    hash_id=new_job_hash_id(),
                    user_id=user.id,
                    tier_at_submit="standard",
                    seq_no=i + 1,
                    model="gpt-image-2",
                    params_json="{}",
                    flags_json="{}",
                    status="QUEUED",
                )
            )
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "USER_BUSY"


# ---------------------------------------------------------------------------
# T-TIER-08 · NO_PROVIDER_AVAILABLE does not consume quota
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_tier_08_no_provider_doesnt_burn_quota(
    seeded_app: httpx.AsyncClient,
):
    # No provider installed → should fail with NO_PROVIDER_AVAILABLE
    register_fake_adapter()
    FakeAdapter.reset()
    user, token = await login_user(seeded_app, tier="free", today_count=0)
    # First, /api/models should be empty
    r = await seeded_app.get("/api/models", headers=auth(token))
    assert r.status_code == 200
    # Submitting with a model nobody serves should yield NO_PROVIDER_AVAILABLE
    files = {"payload": (None, _job_payload(), "application/json")}
    sub = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    # Could be 422 (model unsupported) or 503 (no provider) per design §6.4
    assert sub.status_code in (422, 503)
    if sub.status_code == 503:
        assert sub.json()["detail"]["code"] == "NO_PROVIDER_AVAILABLE"

    # today_count must remain 0
    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    async with get_session() as s:
        u = (
            await s.execute(select(User).where(User.id == user.id))
        ).scalar_one()
    assert u.today_count == 0
