"""T-USER-NN spec cases (文生图平台测试方案 §5.2)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import (
    ADMIN_PW,
    USER_PW,
    auth,
    install_fake_provider,
    login,
    login_admin,
    login_user,
    make_user,
)


pytestmark = [pytest.mark.user]


# ---------------------------------------------------------------------------
# T-USER-01 · admin can create a fresh user (argon2 password_hash)
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.admin
async def test_t_user_01_admin_creates_user(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/users",
        headers=auth(admin),
        json={
            "username": "alice_t01",
            "password": "alicepw1234",
            "tier": "free",
            "role": "user",
        },
    )
    assert r.status_code == 201, r.text

    from app.db.engine import get_session
    from app.db.models import User
    from sqlalchemy import select

    async with get_session() as s:
        u = (
            await s.execute(select(User).where(User.username == "alice_t01"))
        ).scalar_one()
    assert u.status == "active"
    assert u.password_hash.startswith("$argon2id$")


# ---------------------------------------------------------------------------
# T-USER-02 · duplicate username → 422
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_user_02_dup_username_rejected(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    body = {
        "username": "dupy",
        "password": "alicepw1234",
        "tier": "free",
    }
    r1 = await seeded_app.post("/api/admin/users", headers=auth(admin), json=body)
    assert r1.status_code == 201
    r2 = await seeded_app.post("/api/admin/users", headers=auth(admin), json=body)
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "INVALID_PARAMETER"


# ---------------------------------------------------------------------------
# T-USER-03 · normal user → admin endpoint = 403
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_user_03_user_to_admin_forbidden(seeded_app: httpx.AsyncClient):
    _, token = await login_user(seeded_app)
    r = await seeded_app.get("/api/admin/users", headers=auth(token))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# T-USER-04 · disabled user → all business calls 403 ACCOUNT_DISABLED
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.admin
async def test_t_user_04_disabled_user_blocked(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app)
    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        f"/api/admin/users/{user.id}/disable", headers=auth(admin)
    )
    assert r.status_code == 200, r.text

    r2 = await seeded_app.get("/api/me", headers=auth(token))
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "ACCOUNT_DISABLED"


# ---------------------------------------------------------------------------
# T-USER-05 · soft-deleted username can be reused
# ---------------------------------------------------------------------------
@pytest.mark.p2
@pytest.mark.admin
async def test_t_user_05_deleted_username_reusable(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, username="reusable")
    r = await seeded_app.delete(
        f"/api/admin/users/{user.id}", headers=auth(admin)
    )
    assert r.status_code == 200, r.text

    r2 = await seeded_app.post(
        "/api/admin/users",
        headers=auth(admin),
        json={"username": "reusable", "password": "newpw1234"},
    )
    assert r2.status_code == 201, r2.text


# ---------------------------------------------------------------------------
# T-USER-06 · 30-day usage rollup surfaces real numbers
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_user_06_30d_usage_rollup(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, tier="vip")

    # Insert succeeded jobs directly via repo (faster than going through executor)
    from app.db.engine import get_session
    from app.db.models import Job
    from datetime import datetime, timezone

    from app.utils.ids import new_job_hash_id, new_job_internal_id

    async with get_session() as s:
        for i in range(12):
            s.add(
                Job(
                    id=new_job_internal_id(),
                    hash_id=new_job_hash_id(),
                    user_id=user.id,
                    tier_at_submit=user.tier,
                    seq_no=i + 1,
                    set_id=None,
                    model="gpt-image-2",
                    params_json="{}",
                    flags_json="{}",
                    status="SUCCEEDED",
                    created_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                )
            )

    r = await seeded_app.get(
        f"/api/admin/users/{user.id}", headers=auth(admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["jobs_30d_total"] >= 12
    assert body["jobs_30d_success"] >= 12


# ---------------------------------------------------------------------------
# T-USER-07 · /api/me + login response contain no tier/quota fields
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_user_07_response_omits_quota_fields(seeded_app: httpx.AsyncClient):
    user, token = await login_user(seeded_app, tier="free")
    me = await seeded_app.get("/api/me", headers=auth(token))
    body = me.json()
    forbidden = {"tier", "today_count", "soft_quota", "hard_quota"}
    assert forbidden.isdisjoint(body.keys())

    login_resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": user.username, "password": USER_PW},
    )
    user_field = login_resp.json()["user"]
    assert forbidden.isdisjoint(user_field.keys())


# ---------------------------------------------------------------------------
# T-USER-08 · admin reset-password invalidates the old password
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_user_08_password_reset(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    user, _ = await login_user(seeded_app, username="rotating")
    r = await seeded_app.post(
        f"/api/admin/users/{user.id}/reset-password",
        headers=auth(admin),
        json={"new_password": "rotated1234"},
    )
    assert r.status_code == 200, r.text

    bad = await seeded_app.post(
        "/api/auth/login",
        json={"username": "rotating", "password": USER_PW},
    )
    assert bad.status_code == 401
    good = await seeded_app.post(
        "/api/auth/login",
        json={"username": "rotating", "password": "rotated1234"},
    )
    assert good.status_code == 200
