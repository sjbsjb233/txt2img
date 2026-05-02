"""E2E coverage for the four emergency switches (design doc §13.6).

Each switch is exercised through the full HTTP path so we lock in the
exact response codes and bodies operators rely on:

1. ``emergency.pause_generation`` — POST /api/jobs returns 403
   ``BLOCKED_BY_EMERGENCY``.  Existing RUNNING jobs are *not* killed
   (covered indirectly by the rest of the suite).
2. ``emergency.pause_image_access`` — GET /api/jobs/<h>/images/<n>/{thumb,original}
   returns 403 for non-admin users; admin still passes.
3. ``emergency.block_new_member_login`` — non-admin /api/auth/login
   returns 401 ``BLOCKED_BY_EMERGENCY``; admin login still works.
4. ``emergency.force_captcha_global`` — every captcha-check returns
   ``required: true`` regardless of failure history.

After each test the config flag is flipped off so the next test in the
file starts clean.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.config_center import get_config_center

from .conftest import (
    auth_header,
    job_payload,
    login_admin,
    login_user,
    run_scheduler_until_idle,
    seed_provider,
    submit_job,
    wait_for_status,
)


@pytest.mark.asyncio
async def test_pause_generation_blocks_creation(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    await seed_provider(seeded_app, provider_id="oai_pause", label="Pause")
    token = await login_user(seeded_app, username="pause_user")

    cc = get_config_center()
    await cc.set_many({"emergency.pause_generation": True})
    try:
        resp = await submit_job(seeded_app, token)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"
        assert "oai_pause" not in stub_adapter.calls
    finally:
        await cc.set_many({"emergency.pause_generation": False})

    # After flipping off, submission works again.
    resp = await submit_job(seeded_app, token)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_block_new_member_login_lets_admin_in(
    seeded_app: httpx.AsyncClient,
) -> None:
    cc = get_config_center()
    await cc.set_many({"emergency.block_new_member_login": True})
    try:
        # Non-admin login fails …
        from .conftest import create_user

        await create_user(username="blocked_user", tier="premium")
        bad = await seeded_app.post(
            "/api/auth/login",
            json={"username": "blocked_user", "password": "e2epassword"},
        )
        assert bad.status_code == 401, bad.text
        assert bad.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"

        # … but admin still gets in.
        ok = await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert ok.status_code == 200, ok.text
    finally:
        await cc.set_many({"emergency.block_new_member_login": False})


@pytest.mark.asyncio
async def test_force_captcha_global(seeded_app: httpx.AsyncClient) -> None:
    cc = get_config_center()
    # Login under normal conditions first so we have a working token to
    # call /api/jobs/precheck.
    token = await login_user(seeded_app, username="force_capt_user")
    await cc.set_many({"emergency.force_captcha_global": True})
    try:
        # captcha-check (login) returns required.
        check = await seeded_app.post(
            "/api/auth/captcha-check",
            json={"username": "force_capt_user"},
        )
        assert check.status_code == 200
        assert check.json()["captcha_required"] is True

        # /api/jobs/precheck for an authenticated user also returns required.
        pc = await seeded_app.post(
            "/api/jobs/precheck",
            headers=auth_header(token),
            json={"model": "gpt-image-2"},
        )
        assert pc.json()["captcha_required"] is True
    finally:
        await cc.set_many({"emergency.force_captcha_global": False})


@pytest.mark.asyncio
async def test_pause_image_access_blocks_user_keeps_admin(
    seeded_app: httpx.AsyncClient, stub_adapter
) -> None:
    await seed_provider(seeded_app, provider_id="oai_imgs", label="Imgs")
    token = await login_user(seeded_app, username="img_user")
    admin_token = await login_admin(seeded_app)

    # Run a happy-path job for the user.
    resp = await submit_job(seeded_app, token)
    assert resp.status_code == 200
    user_hash = resp.json()["hash_id"]

    # Admin runs their own job too — to assert the bypass we need an
    # image owned by the admin (the user-facing route filters by owner;
    # admin's bypass only matters on jobs they themselves can read).
    resp = await submit_job(seeded_app, admin_token)
    assert resp.status_code == 200
    admin_hash = resp.json()["hash_id"]

    await run_scheduler_until_idle(seeded_app, timeout=10.0)
    await wait_for_status(
        seeded_app, token, user_hash, expected={"SUCCEEDED"}, timeout=10.0
    )
    await wait_for_status(
        seeded_app,
        admin_token,
        admin_hash,
        expected={"SUCCEEDED"},
        timeout=10.0,
    )

    cc = get_config_center()
    await cc.set_many({"emergency.pause_image_access": True})
    try:
        # Regular user is blocked.
        thumb = await seeded_app.get(
            f"/api/jobs/{user_hash}/images/1/thumb",
            headers=auth_header(token),
        )
        assert thumb.status_code == 403, thumb.text
        assert thumb.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"

        # Admin still accesses their own image during the pause.
        admin_thumb = await seeded_app.get(
            f"/api/jobs/{admin_hash}/images/1/thumb",
            headers=auth_header(admin_token),
        )
        assert admin_thumb.status_code == 200
    finally:
        await cc.set_many({"emergency.pause_image_access": False})

    # After flip-off the regular user can fetch it again.
    again = await seeded_app.get(
        f"/api/jobs/{user_hash}/images/1/thumb", headers=auth_header(token)
    )
    assert again.status_code == 200
