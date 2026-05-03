"""Tests for /api/admin/approvals/deletion/* — the admin queue.

Covers the full roundtrip:
    user submits a deletion request →
    admin sees it in the pending list →
    admin approves →
    target user is disabled, sessions revoked,
    target's existing token starts returning 403.
"""

from __future__ import annotations

import httpx
import pytest


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_user(
    admin_token: str, client: httpx.AsyncClient, username: str
) -> str:
    body = {
        "username": username,
        "password": "Userpassword123!",
        "role": "user",
        "tier": "free",
        "display_name": "User Under Test",
        "override_soft_quota": None,
        "override_hard_quota": None,
    }
    resp = await client.post(
        "/api/admin/users", headers=_h(admin_token), json=body
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_deletion_requests_pending(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    await _create_user(admin, seeded_app, "alice")
    user_token = await _login(seeded_app, "alice", "Userpassword123!")

    # Alice submits a deletion request.
    submit = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_h(user_token),
        json={"reason": "leaving the project"},
    )
    assert submit.status_code == 201

    listing = await seeded_app.get(
        "/api/admin/approvals/deletion?status=pending",
        headers=_h(admin),
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total_pending"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["user"]["username"] == "alice"
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["reason"] == "leaving the project"


@pytest.mark.asyncio
async def test_admin_list_filter_by_status(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")

    listing = await seeded_app.get(
        "/api/admin/approvals/deletion?status=approved",
        headers=_h(admin),
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Approve flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_approve_disables_user_and_revokes_sessions(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    await _create_user(admin, seeded_app, "bob")
    user_token = await _login(seeded_app, "bob", "Userpassword123!")

    submit = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_h(user_token),
        json={"reason": "no longer needed"},
    )
    request_id = submit.json()["id"]

    # Confirm bob can call /api/me before approval.
    me_before = await seeded_app.get(
        "/api/me", headers=_h(user_token)
    )
    assert me_before.status_code == 200

    # Admin approves.
    approve = await seeded_app.post(
        f"/api/admin/approvals/deletion/{request_id}/approve",
        headers=_h(admin),
        json={"admin_note": "verified by support"},
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body["status"] == "approved"
    assert body["admin_note"] == "verified by support"
    assert body["resolved_by"]  # admin id

    # Bob's existing token returns 401 because the auth_session row
    # was revoked as part of approval. (The disabled-status check
    # comes after the jti revocation check; either way the user is
    # signed out everywhere.)
    me_after = await seeded_app.get("/api/me", headers=_h(user_token))
    assert me_after.status_code == 401

    # And a fresh login attempt hits the disabled gate.
    relogin = await seeded_app.post(
        "/api/auth/login",
        json={"username": "bob", "password": "Userpassword123!"},
    )
    assert relogin.status_code == 403
    assert relogin.json()["detail"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_admin_approve_pending_only(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    await _create_user(admin, seeded_app, "carol")
    user_token = await _login(seeded_app, "carol", "Userpassword123!")
    submit = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_h(user_token),
        json={"reason": None},
    )
    rid = submit.json()["id"]

    # Approve once.
    first = await seeded_app.post(
        f"/api/admin/approvals/deletion/{rid}/approve",
        headers=_h(admin),
        json={},
    )
    assert first.status_code == 200

    # Approving again returns 409.
    again = await seeded_app.post(
        f"/api/admin/approvals/deletion/{rid}/approve",
        headers=_h(admin),
        json={},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "DELETION_REQUEST_NOT_PENDING"


# ---------------------------------------------------------------------------
# Reject flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reject_keeps_user_active(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    await _create_user(admin, seeded_app, "dave")
    user_token = await _login(seeded_app, "dave", "Userpassword123!")
    submit = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_h(user_token),
        json={"reason": "accidentally clicked"},
    )
    rid = submit.json()["id"]

    rej = await seeded_app.post(
        f"/api/admin/approvals/deletion/{rid}/reject",
        headers=_h(admin),
        json={"admin_note": "user changed their mind"},
    )
    assert rej.status_code == 200
    body = rej.json()
    assert body["status"] == "rejected"

    # Dave's token still works.
    me = await seeded_app.get("/api/me", headers=_h(user_token))
    assert me.status_code == 200

    # Re-submission is allowed since the previous one is rejected (not pending).
    resubmit = await seeded_app.post(
        "/api/me/deletion-request",
        headers=_h(user_token),
        json={"reason": "actually still want to leave"},
    )
    assert resubmit.status_code == 201


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_cannot_approve_unknown_request(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    resp = await seeded_app.post(
        "/api/admin/approvals/deletion/adr_doesnotexist/approve",
        headers=_h(admin),
        json={},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_trigger_lifecycle_sweep(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Manual sweep endpoint returns the usual report dict."""
    admin = await _login(seeded_app, "admin", "test-admin-password")
    resp = await seeded_app.post(
        "/api/admin/approvals/deletion/sweep",
        headers=_h(admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "soft_deleted" in body
    assert "purged" in body


@pytest.mark.asyncio
async def test_non_admin_blocked(
    seeded_app: httpx.AsyncClient,
) -> None:
    admin = await _login(seeded_app, "admin", "test-admin-password")
    await _create_user(admin, seeded_app, "eve")
    user_token = await _login(seeded_app, "eve", "Userpassword123!")
    resp = await seeded_app.get(
        "/api/admin/approvals/deletion",
        headers=_h(user_token),
    )
    assert resp.status_code == 403
