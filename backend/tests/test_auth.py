"""Auth flow tests: captcha gating, login, /api/me, error codes.

These tests exercise the route layer end-to-end through the FastAPI
lifespan (so migrations + seed have run), but Cloudflare siteverify is
patched at the module level so we never reach the network. The bootstrap
admin from the seed (username=``admin``, password=``test-admin-password``
per ``conftest._set_env_for_tests``) is the single user we authenticate
as unless a test creates its own.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# captcha-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captcha_check_clean_user_returns_not_required(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["captcha_required"] is False
    assert body["reason"] is None


@pytest.mark.asyncio
async def test_captcha_check_required_after_three_failures(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Three bad-password attempts in 5 min flips captcha-required on."""
    for _ in range(3):
        bad = await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert bad.status_code == 401

    resp = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    body = resp.json()
    assert body["captcha_required"] is True
    assert body["reason"] == "failure_threshold_exceeded"
    assert body["captcha_provider"] == "turnstile"


@pytest.mark.asyncio
async def test_captcha_check_force_global_overrides(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Toggle ``emergency.force_captcha_global`` true → all checks required."""
    from app.db.engine import get_session
    from app.db.models import Config

    async with get_session() as session:
        row = (
            await session.execute(
                select(Config).where(
                    Config.key == "emergency.force_captcha_global"
                )
            )
        ).scalar_one()
        row.value_json = json.dumps(True)

    resp = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "anyone"}
    )
    body = resp.json()
    assert body["captcha_required"] is True
    assert body["reason"] == "force_captcha"


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_returns_jwt_with_minimal_payload(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Bootstrap admin can log in; JWT contains only sub/u/r/iat/exp."""
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    token = body["access_token"]
    assert token

    # Decode the JWT and verify payload shape.
    from app.utils.security import decode_access_token

    payload = decode_access_token(token)
    # ``jti`` was added so the auth dependency can match the token to a
    # row in ``auth_sessions`` and reject revoked devices.
    assert set(payload.keys()) == {"sub", "u", "r", "iat", "exp", "jti"}
    assert payload["u"] == "admin"
    assert payload["r"] == "admin"
    assert payload["sub"].startswith("u_")
    assert isinstance(payload["jti"], str) and payload["jti"]

    # Response payload is the §2.3 minimal shape — no tier / quota / etc.
    user = body["user"]
    assert set(user.keys()) == {"id", "username", "role", "display_name"}
    assert user["role"] == "admin"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "incorrect"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_login_unknown_user_returns_401_same_code(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Missing user looks identical to wrong password — no enumeration."""
    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "ghost", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_login_disabled_user_returns_403_account_disabled(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A user with status=disabled who *passes* password gets 403."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_disabled001",
                username="disabled_alice",
                password_hash=hash_password("alicepw1"),
                role="user",
                tier="free",
                status="disabled",
            )
        )

    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "disabled_alice", "password": "alicepw1"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_login_captcha_missing_when_required_returns_412(
    seeded_app: httpx.AsyncClient,
) -> None:
    """After 3 failures + correct password, missing captcha → 412."""
    # 3 password failures to flip captcha-required on for this username.
    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )

    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["code"] == "CAPTCHA_REQUIRED"


@pytest.mark.asyncio
async def test_login_captcha_invalid_returns_412(
    seeded_app: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required captcha + bad token → 412 CAPTCHA_INVALID."""
    monkeypatch.setattr(
        "app.api.auth.turnstile.verify",
        _mock_turnstile_verify(False),
    )

    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )

    resp = await seeded_app.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-admin-password",
            "captcha_token": "definitely-bogus",
        },
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["code"] == "CAPTCHA_INVALID"


@pytest.mark.asyncio
async def test_login_captcha_valid_succeeds_after_failures(
    seeded_app: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required captcha + valid token + correct password → 200 + reset."""
    monkeypatch.setattr(
        "app.api.auth.turnstile.verify", _mock_turnstile_verify(True)
    )

    for _ in range(3):
        await seeded_app.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )

    resp = await seeded_app.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-admin-password",
            "captcha_token": "valid-cf-token",
        },
    )
    assert resp.status_code == 200, resp.text

    # After success, captcha should no longer be required for the same user.
    chk = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    assert chk.json()["captcha_required"] is False


# ---------------------------------------------------------------------------
# /api/me + auth on protected routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_only_identity_fields(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    # No tier / today_count / soft_quota / hard_quota — those are admin-
    # only. Profile metadata (email, created_at, last_login_at,
    # password_changed_at) was added with the /settings page and is
    # safe to expose to the user themselves.
    assert set(body.keys()) == {
        "id",
        "username",
        "role",
        "display_name",
        "email",
        "created_at",
        "last_login_at",
        "password_changed_at",
    }
    assert "tier" not in body
    assert "today_count" not in body
    assert "soft_quota" not in body
    assert "hard_quota" not in body


@pytest.mark.asyncio
async def test_me_without_token_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_me_with_malformed_header_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get(
        "/api/me", headers={"Authorization": "NotBearer xxx"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_me_with_invalid_token_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.get(
        "/api/me", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_me_with_expired_token_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A token whose ``exp`` is in the past must be rejected."""
    from app.utils.security import issue_access_token

    token = issue_access_token(
        "u_someid000000",
        "admin",
        "admin",
        expires_in_days=-1,  # expired one day ago
    )
    resp = await seeded_app.get(
        "/api/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_for_disabled_user_returns_403(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Disable a user mid-session → next /me call surfaces ACCOUNT_DISABLED."""
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id="u_dynduser0001",
                username="dyn_user",
                password_hash=hash_password("dynpw123"),
                role="user",
                tier="free",
                status="active",
            )
        )

    login_resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "dyn_user", "password": "dynpw123"},
    )
    token = login_resp.json()["access_token"]

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.username == "dyn_user"))
        ).scalar_one()
        user.status = "disabled"

    resp = await seeded_app.get(
        "/api/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_logout_returns_ok_and_requires_auth(
    seeded_app: httpx.AsyncClient,
) -> None:
    no_auth = await seeded_app.post("/api/auth/logout")
    assert no_auth.status_code == 401

    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Public route inventory: /health, /auth/login, /auth/captcha-check are
# unauthenticated; everything else under /api requires a valid token.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_is_public(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_captcha_check_is_public(
    seeded_app: httpx.AsyncClient,
) -> None:
    resp = await seeded_app.post(
        "/api/auth/captcha-check", json={"username": "admin"}
    )
    assert resp.status_code == 200


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


def _mock_turnstile_verify(result: bool):
    async def _stub(token: str, *, remote_ip: str | None = None) -> bool:
        return result

    return _stub
