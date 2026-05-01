"""Coverage for the four emergency switches (PR-17 / design doc §13.6).

Verifies the runtime side-effects of each ``emergency.*`` config key:

* ``pause_generation``       → ``POST /api/jobs`` returns 403 BLOCKED_BY_EMERGENCY
* ``pause_image_access``     → image-stream routes return 403 for non-admins
* ``block_new_member_login`` → non-admin login returns 401 BLOCKED_BY_EMERGENCY
* ``force_captcha_global``   → captcha-check returns required:true

The tests intentionally exercise the switches through the public API
rather than poking the EmergencyConfig wrapper directly, so any
regression that disconnects the switch from its consumer surfaces here.
"""

from __future__ import annotations

import httpx
import pytest


ADMIN_PASSWORD = "test-admin-password"
USER_PASSWORD = "user-password-1234"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login(
    client: httpx.AsyncClient, username: str, password: str
) -> httpx.Response:
    return await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await _login(client, "admin", ADMIN_PASSWORD)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(*, username: str = "alice") -> str:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.ids import new_user_id
    from app.utils.security import hash_password

    uid = new_user_id()
    async with get_session() as session:
        session.add(
            User(
                id=uid,
                username=username,
                password_hash=hash_password(USER_PASSWORD),
                role="user",
                tier="free",
                status="active",
            )
        )
    return uid


async def _set_switch(
    client: httpx.AsyncClient, token: str, key: str, value: bool
) -> None:
    resp = await client.patch(
        "/api/admin/config", json={key: value}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# pause_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_generation_blocks_post_jobs(seeded_app):
    admin_token = await _login_admin(seeded_app)
    await _seed_user()
    user_token_resp = await _login(seeded_app, "alice", USER_PASSWORD)
    assert user_token_resp.status_code == 200
    user_token = user_token_resp.json()["access_token"]

    await _set_switch(
        seeded_app, admin_token, "emergency.pause_generation", True
    )

    # The job-create endpoint accepts multipart with a payload form
    # field. We pass a minimal one knowing the emergency check fires
    # before validation.
    files = {
        "payload": (
            None,
            '{"model":"gemini-3.1-flash-image-preview","prompt":"hi"}',
            "application/json",
        )
    }
    resp = await seeded_app.post(
        "/api/jobs", files=files, headers=_auth(user_token)
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["detail"]["code"] == "BLOCKED_BY_EMERGENCY"


# ---------------------------------------------------------------------------
# pause_image_access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_image_access_blocks_user_thumbs(seeded_app):
    """Even unknown hash_ids should 403 instead of 404 once the switch is on.

    The guard runs before path resolution, so no DB or filesystem hit
    happens for a paused stream.
    """
    admin_token = await _login_admin(seeded_app)
    await _seed_user()
    user_token = (await _login(seeded_app, "alice", USER_PASSWORD)).json()[
        "access_token"
    ]

    await _set_switch(
        seeded_app, admin_token, "emergency.pause_image_access", True
    )

    resp = await seeded_app.get(
        "/api/jobs/j_aaaaaaaaaaaa/images/1/thumb",
        headers=_auth(user_token),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"


@pytest.mark.asyncio
async def test_pause_image_access_admin_bypass(seeded_app):
    admin_token = await _login_admin(seeded_app)
    await _set_switch(
        seeded_app, admin_token, "emergency.pause_image_access", True
    )

    # Admin shouldn't get 403 — they get 404 because the hash_id is
    # made up. The point is the guard didn't fire.
    resp = await seeded_app.get(
        "/api/jobs/j_aaaaaaaaaaaa/images/1/thumb",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# block_new_member_login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_new_member_login_for_users(seeded_app):
    admin_token = await _login_admin(seeded_app)
    await _seed_user()

    await _set_switch(
        seeded_app, admin_token, "emergency.block_new_member_login", True
    )

    resp = await _login(seeded_app, "alice", USER_PASSWORD)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"


@pytest.mark.asyncio
async def test_block_new_member_login_admin_bypass(seeded_app):
    admin_token = await _login_admin(seeded_app)
    await _set_switch(
        seeded_app, admin_token, "emergency.block_new_member_login", True
    )

    # Admin can still log in even with the switch on — that's the
    # entire point of the bypass.
    resp = await _login(seeded_app, "admin", ADMIN_PASSWORD)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# force_captcha_global
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_captcha_global_marks_required(seeded_app):
    admin_token = await _login_admin(seeded_app)
    await _seed_user()

    # Without the switch, a fresh user with no failed attempts should
    # not need captcha.
    pre = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "alice"}
    )
    assert pre.status_code == 200
    assert pre.json()["captcha_required"] is False

    await _set_switch(
        seeded_app, admin_token, "emergency.force_captcha_global", True
    )

    post = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "alice"}
    )
    assert post.status_code == 200
    assert post.json()["captcha_required"] is True
