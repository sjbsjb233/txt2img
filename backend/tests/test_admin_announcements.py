"""End-to-end tests for the announcements surface (PR-17 / design doc §11).

Covers both halves:

* Admin CRUD on ``/api/admin/announcements``
* User-facing ``/api/announcements/active`` + ``mark-as-read`` + cover

Each test boots the FastAPI app via the ``seeded_app`` fixture so seed
data, ConfigCenter and the announcement bus all hydrate the same way
they do in production.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import httpx
import pytest


ADMIN_PASSWORD = "test-admin-password"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login(
    client: httpx.AsyncClient, username: str, password: str
) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_admin(client: httpx.AsyncClient) -> str:
    return await _login(client, "admin", ADMIN_PASSWORD)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(
    *, username: str, tier: str = "free"
) -> str:
    """Insert a regular user via the DB so tests don't need API plumbing."""
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
                password_hash=hash_password("password-1234"),
                role="user",
                tier=tier,
                status="active",
            )
        )
    return uid


def _basic_text_payload(**overrides) -> dict:
    base = {
        "title": "Scheduled maintenance",
        "content_kind": "text",
        "content": "We'll be down on Sunday 02:00–03:00 UTC+8.",
        "audience_kind": "all",
        "starts_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "ends_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "dismissable": True,
        "priority": 5,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Admin: CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_text_announcement_returns_view(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload()
    resp = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    view = resp.json()
    assert view["id"].startswith("ann_")
    assert view["title"] == body["title"]
    assert view["content_kind"] == "text"
    assert view["content"] == body["content"]
    assert view["audience_kind"] == "all"
    assert view["audience_data"] is None
    assert view["dismissable"] is True
    assert view["priority"] == 5
    # Live because starts_at is in the past + ends_at in the future.
    assert view["is_live"] is True
    # Reach >= 1 because the bootstrap admin counts as an active user.
    assert view["reach"] >= 1
    assert view["read_count"] == 0


@pytest.mark.asyncio
async def test_create_rejects_react_content_kind(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload(content_kind="react")
    resp = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    # Pydantic Literal rejects unknown values with 422 — the exact code
    # is INVALID_PARAMETER per the §17 envelope.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tier_audience_must_supply_data(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload(audience_kind="tier", audience_data=None)
    resp = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_user_list_audience_validates_ids(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload(audience_kind="user_list", audience_data=["badid"])
    resp = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_image_announcement_requires_cover_upload(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload(content_kind="image")
    resp = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    # JSON-only image creates fail because the cover is mandatory.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_image_announcement_via_multipart(seeded_app):
    token = await _login_admin(seeded_app)
    import json as _json

    payload = _basic_text_payload(content_kind="image", content="placeholder")
    files = {
        "payload": (None, _json.dumps(payload), "application/json"),
        "cover": ("cover.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32), "image/png"),
    }
    resp = await seeded_app.post(
        "/api/admin/announcements",
        files=files,
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    view = resp.json()
    assert view["content_kind"] == "image"
    # The DB ``content`` becomes the rel-path under the announcements dir.
    assert view["content"].startswith(f"announcements/{view['id']}/cover.")

    # Admin cover preview fetches the bytes back.
    cover = await seeded_app.get(
        f"/api/admin/announcements/{view['id']}/cover", headers=_auth(token)
    )
    assert cover.status_code == 200
    assert cover.headers["content-type"] in ("image/png", "image/x-png")


@pytest.mark.asyncio
async def test_list_returns_newest_first(seeded_app):
    """Two consecutive inserts should both come back at the top of the list.

    SQLite stores ``created_at`` at second precision so two creates in
    the same test usually share a second. The route's secondary sort
    (by id desc) is then load-bearing — the test only asserts both
    appear before any seeded fixture rows, not which of them wins
    the tie.
    """
    import asyncio

    token = await _login_admin(seeded_app)
    a = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(title="A"),
        headers=_auth(token),
    )
    assert a.status_code == 201
    # Sleep just past one second so B's created_at is strictly greater
    # — this is the only way to make the order deterministic given
    # SQLite's CURRENT_TIMESTAMP precision.
    await asyncio.sleep(1.1)
    b = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(title="B"),
        headers=_auth(token),
    )
    assert b.status_code == 201

    listing = await seeded_app.get(
        "/api/admin/announcements", headers=_auth(token)
    )
    assert listing.status_code == 200
    titles = [it["title"] for it in listing.json()["items"]]
    assert titles[:2] == ["B", "A"]


@pytest.mark.asyncio
async def test_patch_changes_priority_and_audits(seeded_app):
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(),
        headers=_auth(token),
    )
    ann_id = create.json()["id"]

    patch = await seeded_app.patch(
        f"/api/admin/announcements/{ann_id}",
        json={"priority": 9, "title": "renamed"},
        headers=_auth(token),
    )
    assert patch.status_code == 200, patch.text
    updated = patch.json()
    assert updated["priority"] == 9
    assert updated["title"] == "renamed"

    # Confirm the audit log captured the create + patch.
    audit = await seeded_app.get(
        "/api/admin/audit",
        params={"action": "announcement.*"},
        headers=_auth(token),
    )
    assert audit.status_code == 200
    actions = [r["action"] for r in audit.json()["items"]]
    assert "announcement.create" in actions
    assert "announcement.update" in actions


@pytest.mark.asyncio
async def test_delete_removes_row(seeded_app):
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(),
        headers=_auth(token),
    )
    ann_id = create.json()["id"]

    delete = await seeded_app.delete(
        f"/api/admin/announcements/{ann_id}", headers=_auth(token)
    )
    assert delete.status_code == 200

    after = await seeded_app.get(
        f"/api/admin/announcements/{ann_id}", headers=_auth(token)
    )
    assert after.status_code == 404


# ---------------------------------------------------------------------------
# User-facing surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_for_audience_all(seeded_app):
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(title="hello"),
        headers=_auth(token),
    )
    ann_id = create.json()["id"]

    # Seed a regular user and log them in.
    await _seed_user(username="alice")
    user_token = await _login(seeded_app, "alice", "password-1234")

    active = await seeded_app.get(
        "/api/announcements/active", headers=_auth(user_token)
    )
    assert active.status_code == 200
    items = active.json()["items"]
    ids = [it["id"] for it in items]
    assert ann_id in ids

    # The user-facing payload omits operational fields.
    target = next(it for it in items if it["id"] == ann_id)
    assert "audience_kind" not in target
    assert "created_by" not in target
    assert "reach" not in target


@pytest.mark.asyncio
async def test_audience_tier_filters(seeded_app):
    token = await _login_admin(seeded_app)
    body = _basic_text_payload(
        audience_kind="tier", audience_data=["premium"]
    )
    create = await seeded_app.post(
        "/api/admin/announcements", json=body, headers=_auth(token)
    )
    assert create.status_code == 201
    ann_id = create.json()["id"]

    await _seed_user(username="freebie", tier="free")
    free_token = await _login(seeded_app, "freebie", "password-1234")
    await _seed_user(username="paid", tier="premium")
    premium_token = await _login(seeded_app, "paid", "password-1234")

    free_resp = await seeded_app.get(
        "/api/announcements/active", headers=_auth(free_token)
    )
    premium_resp = await seeded_app.get(
        "/api/announcements/active", headers=_auth(premium_token)
    )
    assert ann_id not in [it["id"] for it in free_resp.json()["items"]]
    assert ann_id in [it["id"] for it in premium_resp.json()["items"]]


@pytest.mark.asyncio
async def test_mark_read_drops_from_active(seeded_app):
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/announcements",
        json=_basic_text_payload(),
        headers=_auth(token),
    )
    ann_id = create.json()["id"]

    await _seed_user(username="bob")
    user_token = await _login(seeded_app, "bob", "password-1234")

    first = await seeded_app.get(
        "/api/announcements/active", headers=_auth(user_token)
    )
    assert ann_id in [it["id"] for it in first.json()["items"]]

    mark = await seeded_app.post(
        f"/api/announcements/{ann_id}/read", headers=_auth(user_token)
    )
    assert mark.status_code == 200

    second = await seeded_app.get(
        "/api/announcements/active", headers=_auth(user_token)
    )
    assert ann_id not in [it["id"] for it in second.json()["items"]]

    # Idempotent: a second mark-as-read returns 200, no DB error.
    again = await seeded_app.post(
        f"/api/announcements/{ann_id}/read", headers=_auth(user_token)
    )
    assert again.status_code == 200


@pytest.mark.asyncio
async def test_active_excludes_announcements_outside_window(seeded_app):
    token = await _login_admin(seeded_app)
    future_body = _basic_text_payload(
        starts_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        ends_at=(datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
    )
    fut = await seeded_app.post(
        "/api/admin/announcements", json=future_body, headers=_auth(token)
    )
    assert fut.status_code == 201
    fut_id = fut.json()["id"]

    past_body = _basic_text_payload(
        starts_at=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
        ends_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    )
    past = await seeded_app.post(
        "/api/admin/announcements", json=past_body, headers=_auth(token)
    )
    assert past.status_code == 201
    past_id = past.json()["id"]

    await _seed_user(username="carol")
    user_token = await _login(seeded_app, "carol", "password-1234")
    active = await seeded_app.get(
        "/api/announcements/active", headers=_auth(user_token)
    )
    ids = [it["id"] for it in active.json()["items"]]
    assert fut_id not in ids
    assert past_id not in ids


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_cannot_use_admin_routes(seeded_app):
    await _seed_user(username="dan")
    user_token = await _login(seeded_app, "dan", "password-1234")
    resp = await seeded_app.get(
        "/api/admin/announcements", headers=_auth(user_token)
    )
    assert resp.status_code == 403
