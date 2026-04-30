"""End-to-end tests for the admin user-management router (PR-15).

Boots the FastAPI app via lifespan so seed + ConfigCenter hydrate the
same way they do in production. Each test owns its own fresh DB via
``seeded_app``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


ADMIN_PASSWORD = "test-admin-password"


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_admin(client: httpx.AsyncClient) -> str:
    return await _login(client, "admin", ADMIN_PASSWORD)


async def _seed_user(
    *,
    username: str,
    password: str,
    role: str = "user",
    tier: str = "free",
    status: str = "active",
    user_id: str | None = None,
    last_login_at: datetime | None = None,
    today_count: int = 0,
) -> str:
    """Insert a user directly via DB so tests don't need to round-trip
    through the create endpoint just to set up state.
    """
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.ids import new_user_id
    from app.utils.security import hash_password

    uid = user_id or new_user_id()
    async with get_session() as session:
        session.add(
            User(
                id=uid,
                username=username,
                password_hash=hash_password(password),
                role=role,
                tier=tier,
                status=status,
                last_login_at=last_login_at,
                today_count=today_count,
            )
        )
    return uid


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_job(
    *,
    user_id: str,
    status: str = "SUCCEEDED",
    model: str = "gemini-3.1-flash-image-preview",
    provider_used: str | None = "bltcy",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    cost_cny: float = 0.1,
) -> str:
    """Insert a Job row + bump seq_no on the user."""
    from app.db.engine import get_session
    from app.db.models import Job, User
    from app.utils.ids import new_job_hash_id, new_job_internal_id

    job_id = new_job_internal_id()
    hash_id = new_job_hash_id()

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        user.last_seq_no += 1
        seq = user.last_seq_no
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit=user.tier,
                seq_no=seq,
                model=model,
                params_json="{}",
                flags_json="{}",
                status=status,
                provider_used=provider_used,
                cost_cny=cost_cny,
                created_at=created_at or datetime.now(timezone.utc),
                started_at=started_at,
                finished_at=finished_at,
            )
        )
    return hash_id


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/users")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_list_users_requires_admin(seeded_app: httpx.AsyncClient) -> None:
    await _seed_user(username="alice_norm", password="alicepw1")
    token = await _login(seeded_app, "alice_norm", "alicepw1")
    resp = await seeded_app.get("/api/admin/users", headers=_auth(token))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_returns_admin_only_at_first(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/admin/users", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["username"] == "admin"
    item = body["items"][0]
    # Effective quotas resolve from tier (admin defaults to vip in seed)
    assert item["soft_quota_effective"] >= 1
    assert item["hard_quota_effective"] >= item["soft_quota_effective"]


@pytest.mark.asyncio
async def test_list_users_filters_and_sorts(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_user(
        username="alice", password="alicepw1", tier="vip",
        last_login_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    await _seed_user(
        username="bob", password="bobpw123", tier="premium",
        last_login_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    await _seed_user(
        username="carol", password="carolpw1", tier="free", status="disabled",
    )

    token = await _login_admin(seeded_app)
    # filter tier=premium
    resp = await seeded_app.get(
        "/api/admin/users?tier=premium", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {it["username"] for it in body["items"]} == {"bob"}

    # filter status=disabled (does NOT include the default exclusion)
    resp = await seeded_app.get(
        "/api/admin/users?status=disabled", headers=_auth(token)
    )
    assert {it["username"] for it in resp.json()["items"]} == {"carol"}

    # default sort: last_login_at desc — admin (just logged in) is first.
    resp = await seeded_app.get("/api/admin/users", headers=_auth(token))
    body = resp.json()
    usernames = [it["username"] for it in body["items"]]
    # admin logged in via _login_admin so last_login_at is now() — it should come first.
    assert usernames[0] == "admin"


@pytest.mark.asyncio
async def test_list_users_pagination(seeded_app: httpx.AsyncClient) -> None:
    for i in range(7):
        await _seed_user(username=f"user{i:02d}", password="passw0rd1")

    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/users?page=1&page_size=3", headers=_auth(token)
    )
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["page"] == 1
    assert body["page_size"] == 3
    # admin + 7 seeded = 8 total, none deleted
    assert body["total"] == 8


@pytest.mark.asyncio
async def test_list_users_search_q(seeded_app: httpx.AsyncClient) -> None:
    await _seed_user(username="alice", password="alicepw1")
    await _seed_user(username="alex", password="alicepw1")
    await _seed_user(username="bob", password="alicepw1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/admin/users?q=al", headers=_auth(token))
    names = {it["username"] for it in resp.json()["items"]}
    assert names == {"alice", "alex"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_happy_path(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={
            "username": "claire_v",
            "password": "claire_pwd_8+",
            "role": "user",
            "tier": "premium",
            "display_name": "Claire V.",
            "override_soft_quota": 60,
            "override_hard_quota": 120,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "claire_v"
    assert body["tier"] == "premium"
    assert body["override_soft_quota"] == 60
    assert body["override_hard_quota"] == 120
    assert body["soft_quota_effective"] == 60
    assert body["hard_quota_effective"] == 120
    assert body["display_name"] == "Claire V."
    assert body["jobs_30d_total"] == 0
    assert len(body["daily_usage"]) == 30


@pytest.mark.asyncio
async def test_create_user_short_password_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={"username": "shorty", "password": "abc"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_user_duplicate_username_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_user(username="taken", password="passw0rd1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={"username": "taken", "password": "anotherpwd"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"


@pytest.mark.asyncio
async def test_create_user_override_invariant(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={
            "username": "oof",
            "password": "passw0rd1",
            "override_soft_quota": 100,
            "override_hard_quota": 50,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_includes_30d_rollup(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="alicepw1", tier="premium")

    now = datetime.now(timezone.utc)
    await _seed_job(
        user_id=uid,
        status="SUCCEEDED",
        model="gpt-image-2",
        provider_used="bltcy",
        created_at=now - timedelta(hours=1),
        started_at=now - timedelta(hours=1, minutes=1),
        finished_at=now - timedelta(hours=1, minutes=0, seconds=50),
    )
    await _seed_job(
        user_id=uid,
        status="FAILED",
        model="gpt-image-2",
        provider_used="bltcy",
        created_at=now - timedelta(days=2),
    )
    # outside window
    await _seed_job(
        user_id=uid,
        status="SUCCEEDED",
        created_at=now - timedelta(days=40),
    )

    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/users/{uid}", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs_30d_total"] == 2
    assert body["jobs_30d_success"] == 1
    assert body["jobs_30d_failed"] == 1
    assert any(it["key"] == "gpt-image-2" for it in body["top_models"])
    assert any(it["key"] == "bltcy" for it in body["top_providers"])
    assert len(body["daily_usage"]) == 30


@pytest.mark.asyncio
async def test_get_user_not_found(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/users/u_doesnotexist", headers=_auth(token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_user_changes_tier(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="alice", password="alicepw1", tier="free")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={"tier": "vip", "display_name": "Alice"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "vip"
    assert body["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_patch_user_sets_overrides(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="alice", password="alicepw1", tier="free")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={"override_soft_quota": 30, "override_hard_quota": 60},
    )
    assert resp.status_code == 200
    assert resp.json()["soft_quota_effective"] == 30
    assert resp.json()["hard_quota_effective"] == 60


@pytest.mark.asyncio
async def test_patch_user_clears_override_with_null(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import User

    uid = await _seed_user(username="alice", password="alicepw1", tier="vip")
    async with get_session() as s:
        u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        u.override_soft_quota = 50

    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={"override_soft_quota": None},
    )
    assert resp.status_code == 200
    assert resp.json()["override_soft_quota"] is None


@pytest.mark.asyncio
async def test_patch_user_password_does_not_appear_in_audit(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import AuditLog

    uid = await _seed_user(username="alice", password="alicepw1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={"password": "brand-new-passw0rd"},
    )
    assert resp.status_code == 200

    async with get_session() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "user.update")
            )
        ).scalars().all()
    assert rows
    for row in rows:
        assert (row.payload_json or "").find("brand-new-passw0rd") == -1


@pytest.mark.asyncio
async def test_patch_user_empty_body_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="alicepw1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_user_status_deleted_not_allowed(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="alicepw1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        f"/api/admin/users/{uid}",
        headers=_auth(token),
        json={"status": "deleted"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Disable / enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_then_enable(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="alice", password="alicepw1")
    token = await _login_admin(seeded_app)

    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/disable", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"

    # disabled user can no longer log in
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alicepw1"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ACCOUNT_DISABLED"

    # re-enable
    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/enable", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_disable_self_forbidden(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    me = await seeded_app.get("/api/me", headers=_auth(token))
    admin_id = me.json()["id"]
    resp = await seeded_app.post(
        f"/api/admin/users/{admin_id}/disable", headers=_auth(token)
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_password_lets_user_login_with_new(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="oldpassword")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/reset-password",
        headers=_auth(token),
        json={"new_password": "brandNewPwd123"},
    )
    assert resp.status_code == 200

    # new password works
    new_token = await _login(seeded_app, "alice", "brandNewPwd123")
    assert new_token

    # old password rejected
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "alice", "password": "oldpassword"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_renames_for_reuse(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import User

    uid = await _seed_user(username="alice", password="passw0rd1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.delete(
        f"/api/admin/users/{uid}", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    async with get_session() as s:
        u = (
            await s.execute(select(User).where(User.id == uid))
        ).scalar_one()
        assert u.status == "deleted"
        assert u.username.startswith("alice__deleted_")

    # name "alice" can be reused
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={"username": "alice", "password": "freshpwd_1"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_delete_self_forbidden(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    me = await seeded_app.get("/api/me", headers=_auth(token))
    admin_id = me.json()["id"]
    resp = await seeded_app.delete(
        f"/api/admin/users/{admin_id}", headers=_auth(token)
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Impersonate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impersonate_returns_short_lived_token_for_target(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(
        username="alice", password="alicepw1", tier="premium"
    )
    admin_token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/impersonate", headers=_auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_user_id"] == uid
    assert body["target_username"] == "alice"
    assert 60 <= body["expires_in_seconds"] <= 60 * 60

    impersonate_token = body["access_token"]

    # The impersonate token authenticates as alice.
    me_resp = await seeded_app.get("/api/me", headers=_auth(impersonate_token))
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "alice"

    # But it is rejected at the admin boundary.
    admin_resp = await seeded_app.get(
        "/api/admin/users", headers=_auth(impersonate_token)
    )
    assert admin_resp.status_code == 403
    assert admin_resp.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_impersonate_self_forbidden(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    me = await seeded_app.get("/api/me", headers=_auth(token))
    admin_id = me.json()["id"]
    resp = await seeded_app.post(
        f"/api/admin/users/{admin_id}/impersonate", headers=_auth(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_impersonate_disabled_user_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(
        username="alice", password="alicepw1", status="disabled"
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/impersonate", headers=_auth(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_impersonate_audit_records_admin_actor(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import AuditLog, User

    uid = await _seed_user(username="alice", password="alicepw1")
    admin_token = await _login_admin(seeded_app)

    me = await seeded_app.get("/api/me", headers=_auth(admin_token))
    admin_id = me.json()["id"]

    resp = await seeded_app.post(
        f"/api/admin/users/{uid}/impersonate", headers=_auth(admin_token)
    )
    assert resp.status_code == 200

    async with get_session() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "user.impersonate")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_user_id == admin_id
    assert rows[0].target_id == uid


# ---------------------------------------------------------------------------
# Bulk patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_patch_changes_tier(seeded_app: httpx.AsyncClient) -> None:
    uids = []
    for i in range(3):
        uids.append(
            await _seed_user(
                username=f"u{i}", password="passw0rd1", tier="free"
            )
        )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users/bulk",
        headers=_auth(token),
        json={"ids": uids, "patch": {"tier": "premium"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3
    assert body["skipped_ids"] == []

    # confirm the tier change
    for uid in uids:
        detail = await seeded_app.get(
            f"/api/admin/users/{uid}", headers=_auth(token)
        )
        assert detail.json()["tier"] == "premium"


@pytest.mark.asyncio
async def test_bulk_patch_rejects_password_field(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="passw0rd1")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users/bulk",
        headers=_auth(token),
        json={"ids": [uid], "patch": {"password": "newpwd_12345"}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_patch_skips_self_and_missing(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="passw0rd1")
    token = await _login_admin(seeded_app)
    me = await seeded_app.get("/api/me", headers=_auth(token))
    admin_id = me.json()["id"]
    resp = await seeded_app.post(
        "/api/admin/users/bulk",
        headers=_auth(token),
        json={
            "ids": [uid, admin_id, "u_does_not_exist"],
            "patch": {"tier": "vip"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert admin_id in body["skipped_ids"]
    assert "u_does_not_exist" in body["skipped_ids"]


# ---------------------------------------------------------------------------
# Per-user job listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_jobs_listing_returns_admin_view(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="alicepw1")
    h1 = await _seed_job(user_id=uid, cost_cny=0.2, provider_used="bltcy")
    h2 = await _seed_job(user_id=uid, cost_cny=0.1, provider_used="azure")

    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/users/{uid}/jobs", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_hash = {it["hash_id"]: it for it in body["items"]}
    assert by_hash[h1]["provider_used"] == "bltcy"
    assert by_hash[h1]["cost_cny"] == 0.2
    assert by_hash[h2]["provider_used"] == "azure"


@pytest.mark.asyncio
async def test_user_jobs_listing_status_filter(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="alice", password="alicepw1")
    await _seed_job(user_id=uid, status="SUCCEEDED")
    await _seed_job(user_id=uid, status="FAILED")
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/users/{uid}/jobs?status=FAILED", headers=_auth(token)
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_user_jobs_listing_unknown_user_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/users/u_nope/jobs", headers=_auth(token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_writes_audit_row(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.db.engine import get_session
    from app.db.models import AuditLog

    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/admin/users",
        headers=_auth(token),
        json={"username": "audited", "password": "passw0rd1"},
    )
    assert resp.status_code == 201

    async with get_session() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "user.create")
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_kind == "user"
    assert "audited" in (rows[0].payload_json or "")
