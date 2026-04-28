"""Sessions API: CRUD, ownership isolation, image_count aggregation.

Boots the FastAPI app via the ``seeded_app`` fixture so each test sees
the real lifespan (migrations + seed + adapter discovery). Authentication
goes through ``/api/auth/login`` so the tests exercise the same dep
chain as production.
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _create_user_and_login(
    client: httpx.AsyncClient, username: str = "alice"
) -> str:
    """Insert a non-admin user directly via the DB and log in.

    PR-15 introduces admin-side user creation. Until then tests
    fabricate users via the DB so the sessions endpoints can be
    exercised against a non-admin caller.
    """
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.ids import new_user_id
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id=new_user_id(),
                username=username,
                password_hash=hash_password("pw1234567"),
                role="user",
                tier="free",
            )
        )
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "pw1234567"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/sessions")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.post("/api/sessions", json={"name": "x"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Create / list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_201(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/sessions",
        json={"name": "Editorial Cover"},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("sess_")
    assert body["name"] == "Editorial Cover"
    assert body["image_count"] == 0
    assert "created_at" in body and "updated_at" in body


@pytest.mark.asyncio
async def test_list_returns_user_sessions_only(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A user must only see sessions they own."""
    admin_token = await _login_admin(seeded_app)
    alice_token = await _create_user_and_login(seeded_app, "alice_iso")

    # Admin creates one, alice creates two.
    a = await seeded_app.post(
        "/api/sessions",
        json={"name": "Admin Stuff"},
        headers=_auth(admin_token),
    )
    assert a.status_code == 201
    b = await seeded_app.post(
        "/api/sessions",
        json={"name": "Alice One"},
        headers=_auth(alice_token),
    )
    assert b.status_code == 201
    c = await seeded_app.post(
        "/api/sessions",
        json={"name": "Alice Two"},
        headers=_auth(alice_token),
    )
    assert c.status_code == 201

    resp = await seeded_app.get("/api/sessions", headers=_auth(alice_token))
    assert resp.status_code == 200
    body = resp.json()
    names = sorted(s["name"] for s in body["sessions"])
    assert names == ["Alice One", "Alice Two"]


@pytest.mark.asyncio
async def test_list_orders_by_updated_at_desc(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Sessions return newest-touched first (created counts as touch)."""
    token = await _login_admin(seeded_app)
    first = await seeded_app.post(
        "/api/sessions", json={"name": "first"}, headers=_auth(token)
    )
    second = await seeded_app.post(
        "/api/sessions", json={"name": "second"}, headers=_auth(token)
    )
    assert first.status_code == 201 and second.status_code == 201

    resp = await seeded_app.get("/api/sessions", headers=_auth(token))
    names = [s["name"] for s in resp.json()["sessions"]]
    # Newest first; the second insert is later than the first.
    assert names == ["second", "first"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_blank_name(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    # Leading whitespace stripped → empty → 422 from the validator.
    resp = await seeded_app.post(
        "/api/sessions", json={"name": "   "}, headers=_auth(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_strips_whitespace_from_name(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/sessions",
        json={"name": "  Trimmed  "},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Trimmed"


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_renames_session(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/sessions", json={"name": "Before"}, headers=_auth(token)
    )
    sid = create.json()["id"]

    resp = await seeded_app.patch(
        f"/api/sessions/{sid}",
        json={"name": "After"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "After"


@pytest.mark.asyncio
async def test_patch_empty_body_returns_400(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/sessions", json={"name": "x"}, headers=_auth(token)
    )
    sid = create.json()["id"]

    resp = await seeded_app.patch(
        f"/api/sessions/{sid}", json={}, headers=_auth(token)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_patch_other_users_session_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Cross-tenant patch leaks no info beyond 404."""
    admin_token = await _login_admin(seeded_app)
    alice_token = await _create_user_and_login(seeded_app, "alice_xtenant")

    create = await seeded_app.post(
        "/api/sessions", json={"name": "Admin"}, headers=_auth(admin_token)
    )
    sid = create.json()["id"]

    resp = await seeded_app.patch(
        f"/api/sessions/{sid}",
        json={"name": "Hijack"},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_returns_ok_and_404_thereafter(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/sessions", json={"name": "transient"}, headers=_auth(token)
    )
    sid = create.json()["id"]

    resp = await seeded_app.delete(f"/api/sessions/{sid}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    again = await seeded_app.delete(f"/api/sessions/{sid}", headers=_auth(token))
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_delete_other_users_session_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin_token = await _login_admin(seeded_app)
    alice_token = await _create_user_and_login(seeded_app, "alice_del")

    create = await seeded_app.post(
        "/api/sessions", json={"name": "Admin"}, headers=_auth(admin_token)
    )
    sid = create.json()["id"]

    resp = await seeded_app.delete(
        f"/api/sessions/{sid}", headers=_auth(alice_token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_preserves_jobs_and_clears_pointer(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Deleting a session must not cascade into the jobs themselves."""
    from app.db.engine import get_session
    from app.db.jobs_repository import JobsRepository
    from app.db.models import Job, SessionJob, User
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/sessions",
        json={"name": "with-jobs"},
        headers=_auth(token),
    )
    assert create.status_code == 201
    sid = create.json()["id"]

    # Look up the admin user so we know its id.
    async with get_session() as session:
        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()

    repo = JobsRepository()
    async with get_session() as session:
        created = await repo.insert_queued(
            user_id=admin.id,
            tier_at_submit="vip",
            model="gpt-image-2",
            params_json="{}",
            session_id=sid,
            session=session,
        )
        # Bind the job to the session via the join table — Sessions
        # endpoints in PR-13 will do this automatically; tests fabricate
        # the relationship directly.
        session.add(SessionJob(session_id=sid, job_id=created.job_id))

    delete = await seeded_app.delete(
        f"/api/sessions/{sid}", headers=_auth(token)
    )
    assert delete.status_code == 200

    async with get_session() as session:
        # Job survives.
        job = (
            await session.execute(select(Job).where(Job.id == created.job_id))
        ).scalar_one()
        assert job.session_id is None

        # Join row is gone.
        join_rows = (
            await session.execute(
                select(SessionJob).where(SessionJob.session_id == sid)
            )
        ).all()
        assert join_rows == []


# ---------------------------------------------------------------------------
# image_count aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_count_in_list_response(
    seeded_app: httpx.AsyncClient,
) -> None:
    """``image_count`` must reflect the number of bound jobs.

    Today the count is per-job (not per-image); when PR-13/14 land
    they'll keep the join table at one row per job, so this is the
    correct measure for "how many entries are in this group". The
    field name follows the design doc §5.2 ``GET /api/models``
    contract.
    """
    from app.db.engine import get_session
    from app.db.jobs_repository import JobsRepository
    from app.db.models import SessionJob, User
    from sqlalchemy import select

    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/sessions",
        json={"name": "counting"},
        headers=_auth(token),
    )
    sid = create.json()["id"]

    async with get_session() as session:
        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()

    repo = JobsRepository()
    for _ in range(3):
        async with get_session() as session:
            created = await repo.insert_queued(
                user_id=admin.id,
                tier_at_submit="vip",
                model="gpt-image-2",
                params_json="{}",
                session_id=sid,
                session=session,
            )
            session.add(SessionJob(session_id=sid, job_id=created.job_id))

    resp = await seeded_app.get("/api/sessions", headers=_auth(token))
    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["sessions"]}
    assert by_id[sid]["image_count"] == 3
