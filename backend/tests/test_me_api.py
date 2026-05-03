"""Tests for the user-facing ``/api/me/*`` surface.

Covers profile updates, preference deep-merge, password change with
session revocation, active sessions listing/revocation, and the
account deletion request flow. Each test boots through ``seeded_app``
so it sees the full app stack (migrations, seed, deps, audit).
"""

from __future__ import annotations

import httpx
import pytest


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_get_does_not_leak_quota_or_tier(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get("/api/me", headers=_headers(token))
    body = resp.json()
    for forbidden in ("tier", "today_count", "soft_quota", "hard_quota"):
        assert forbidden not in body
    # Profile fields must be present.
    for required in ("email", "created_at", "last_login_at", "password_changed_at"):
        assert required in body


@pytest.mark.asyncio
async def test_patch_me_display_name_persists(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me",
        headers=_headers(token),
        json={"display_name": "Mei Chen"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Mei Chen"

    # Survives a fresh GET.
    again = await seeded_app.get("/api/me", headers=_headers(token))
    assert again.json()["display_name"] == "Mei Chen"


@pytest.mark.asyncio
async def test_patch_me_rejects_blank_display_name(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me",
        headers=_headers(token),
        json={"display_name": "   "},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_DISPLAY_NAME"


@pytest.mark.asyncio
async def test_patch_me_rejects_invalid_email(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me",
        headers=_headers(token),
        json={"email": "not-an-email"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_EMAIL"


@pytest.mark.asyncio
async def test_patch_me_rejects_unknown_field(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me",
        headers=_headers(token),
        json={"tier": "vip"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preferences_lazy_create_returns_defaults(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/me/preferences", headers=_headers(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generation"]["default_aspect_ratio"] == "1:1"
    assert body["generation"]["default_batch_size"] == 1
    assert body["appearance"]["theme"] == "system"
    assert body["locale"]["language"] == "en"


@pytest.mark.asyncio
async def test_preferences_partial_patch_deep_merge(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    # First set theme=dark and density=compact.
    r1 = await seeded_app.patch(
        "/api/me/preferences",
        headers=_headers(token),
        json={"appearance": {"theme": "dark", "density": "compact"}},
    )
    assert r1.status_code == 200
    assert r1.json()["appearance"] == {
        "theme": "dark",
        "density": "compact",
        "sidebar_default": "expanded",
    }
    # Now patch only generation.batch — appearance must be preserved.
    r2 = await seeded_app.patch(
        "/api/me/preferences",
        headers=_headers(token),
        json={"generation": {"default_batch_size": 4}},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["generation"]["default_batch_size"] == 4
    assert body["appearance"]["theme"] == "dark"
    assert body["appearance"]["density"] == "compact"


@pytest.mark.asyncio
async def test_preferences_invalid_batch_returns_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me/preferences",
        headers=_headers(token),
        json={"generation": {"default_batch_size": 7}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PREFERENCE"


@pytest.mark.asyncio
async def test_preferences_unknown_field_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.patch(
        "/api/me/preferences",
        headers=_headers(token),
        json={"generation": {"tier": "vip"}},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_wrong_current_returns_401(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/me/password",
        headers=_headers(token),
        json={"current_password": "wrong", "new_password": "Newpassword1!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "WRONG_CURRENT_PASSWORD"


@pytest.mark.asyncio
async def test_password_policy_rejects_weak_password(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        "/api/me/password",
        headers=_headers(token),
        json={
            "current_password": "test-admin-password",
            "new_password": "shortone",  # < 10 chars
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "PASSWORD_POLICY"


@pytest.mark.asyncio
async def test_password_change_revokes_other_sessions_only(
    seeded_app: httpx.AsyncClient,
) -> None:
    """The current device must keep working; older devices should 401."""
    other = await _login_admin(seeded_app)  # session A — will be revoked
    current = await _login_admin(seeded_app)  # session B — used for the call

    resp = await seeded_app.post(
        "/api/me/password",
        headers=_headers(current),
        json={
            "current_password": "test-admin-password",
            "new_password": "Newpassword123!",
        },
    )
    assert resp.status_code == 204

    # Current session still works.
    me_now = await seeded_app.get("/api/me", headers=_headers(current))
    assert me_now.status_code == 200

    # Other session is dead.
    me_old = await seeded_app.get("/api/me", headers=_headers(other))
    assert me_old.status_code == 401


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_marks_current(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/me/sessions", headers=_headers(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_session_id"] is not None
    currents = [s for s in body["sessions"] if s["is_current"]]
    assert len(currents) == 1


@pytest.mark.asyncio
async def test_sessions_cannot_revoke_current(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/me/sessions", headers=_headers(token)
    )
    current_id = resp.json()["current_session_id"]
    bad = await seeded_app.delete(
        f"/api/me/sessions/{current_id}", headers=_headers(token)
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "CANNOT_REVOKE_CURRENT"


@pytest.mark.asyncio
async def test_revoke_others_kicks_other_devices(
    seeded_app: httpx.AsyncClient,
) -> None:
    other = await _login_admin(seeded_app)
    current = await _login_admin(seeded_app)

    resp = await seeded_app.post(
        "/api/me/sessions/revoke-others", headers=_headers(current)
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_count"] >= 1

    # The "other" token is now dead.
    me_old = await seeded_app.get("/api/me", headers=_headers(other))
    assert me_old.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_current_session(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    out = await seeded_app.post(
        "/api/auth/logout", headers=_headers(token)
    )
    assert out.status_code == 200
    # Token should no longer authenticate.
    me = await seeded_app.get("/api/me", headers=_headers(token))
    assert me.status_code == 401


# ---------------------------------------------------------------------------
# Deletion request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deletion_request_pending_then_409(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    first = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_headers(token),
        json={"reason": "no longer needed"},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["status"] == "pending"

    second = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_headers(token),
        json={"reason": "again"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DELETION_REQUEST_PENDING"


@pytest.mark.asyncio
async def test_deletion_request_withdraw_unblocks_resubmit(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    first = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_headers(token),
        json={"reason": None},
    )
    assert first.status_code == 201

    withdraw = await seeded_app.delete(
        "/api/me/deletion-request", headers=_headers(token)
    )
    assert withdraw.status_code == 204

    again = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_headers(token),
        json={"reason": "fresh attempt"},
    )
    assert again.status_code == 201
