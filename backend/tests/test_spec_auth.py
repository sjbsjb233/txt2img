"""T-AUTH-NN spec cases (文生图平台测试方案 §5.1).

Mock-only AUTH coverage. Hybrid (T-AUTH-09 / T-AUTH-10) require a live
Playwright session and are kept out of this file — see
``tests/e2e/test_spec_auth_e2e.py`` for the gated browser counterparts.
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.config import get_settings
from tests.infra.fake_turnstile import patch_turnstile
from tests.infra.seeds import (
    ADMIN_PW,
    USER_PW,
    auth,
    install_fake_provider,
    login,
    login_admin,
    login_user,
)


pytestmark = [pytest.mark.auth]


# ---------------------------------------------------------------------------
# T-AUTH-01 · health probe needs no token
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_01_health_no_auth(seeded_app: httpx.AsyncClient):
    r = await seeded_app.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True or body.get("status") == "ok"


# ---------------------------------------------------------------------------
# T-AUTH-02 · unauthenticated business calls → 401
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_02_unauth_business_calls_401(seeded_app: httpx.AsyncClient):
    for path in ("/api/me", "/api/models"):
        r = await seeded_app.get(path)
        assert r.status_code == 401, (path, r.status_code, r.text)
        body = r.json()
        # FastAPI's HTTPBearer returns its own 'Not authenticated' detail
        # when no header is sent — accept either that or our explicit
        # UNAUTHORIZED code shape.
        assert "detail" in body
    r = await seeded_app.post("/api/jobs/precheck", json={"model": "gpt-image-2"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# T-AUTH-03 · wrong password → 401 + login_attempt(success=0) row
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_03_wrong_password_records_failure(seeded_app: httpx.AsyncClient):
    r = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong"},
    )
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["code"] == "UNAUTHORIZED"

    # Verify a row landed.
    from app.db.engine import get_session
    from app.db.models import LoginAttempt
    from sqlalchemy import select

    async with get_session() as s:
        rows = (
            await s.execute(
                select(LoginAttempt).where(LoginAttempt.username == "admin")
            )
        ).scalars().all()
    assert any(r.success == 0 for r in rows)


# ---------------------------------------------------------------------------
# T-AUTH-04 · 3 failures → captcha-required for that user
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_04_three_failures_require_captcha(seeded_app: httpx.AsyncClient):
    for _ in range(3):
        r = await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
        )
        assert r.status_code == 401
    r = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["captcha_required"] is True


# ---------------------------------------------------------------------------
# T-AUTH-05 · FORCE_CAPTCHA env → captcha required regardless
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_auth_05_force_captcha_env_forces(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "FORCE_CAPTCHA", True)
    try:
        r = await seeded_app.post(
            "/api/auth/captcha-check", json={"username": "nobody"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["captcha_required"] is True
        assert body["reason"] == "force_captcha"
    finally:
        monkeypatch.setattr(s, "FORCE_CAPTCHA", False)


# ---------------------------------------------------------------------------
# T-AUTH-06 · captcha pass + correct password resets failure window
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_06_captcha_pass_clears_failures(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    patch_turnstile(monkeypatch)
    # Trigger captcha requirement
    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
        )
    # Now log in with token
    r = await seeded_app.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PW,
            "captcha_token": "OK-good",
        },
    )
    assert r.status_code == 200, r.text

    # After success, captcha-check should no longer require captcha
    r2 = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    assert r2.json()["captcha_required"] is False


# ---------------------------------------------------------------------------
# T-AUTH-07 · captcha invalid → 412 CAPTCHA_INVALID
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_07_captcha_invalid(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    patch_turnstile(monkeypatch)
    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
        )
    r = await seeded_app.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PW,
            "captcha_token": "FAIL",
        },
    )
    assert r.status_code == 412
    assert r.json()["detail"]["code"] == "CAPTCHA_INVALID"


# ---------------------------------------------------------------------------
# T-AUTH-08 · JWT carries no tier / quota fields
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_auth_08_jwt_payload_minimal(seeded_app: httpx.AsyncClient):
    r = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    token = r.json()["access_token"]
    from app.utils.security import decode_access_token

    payload = decode_access_token(token)
    sensitive = {
        "tier",
        "today_count",
        "soft_quota",
        "hard_quota",
        "override_soft_quota",
        "override_hard_quota",
    }
    assert sensitive.isdisjoint(payload.keys())
    # Only documented claim names allowed.
    assert set(payload.keys()).issubset({"sub", "u", "r", "iat", "exp", "impersonator"})
