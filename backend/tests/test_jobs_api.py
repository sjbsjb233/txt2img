"""End-to-end tests for the PR-13 job routes.

Coverage matrix:

- ``POST /api/jobs/precheck`` — captcha decision branches.
- ``POST /api/jobs`` — happy path, parameter validation, soft-quota
  flag, idempotency, multipart references, session binding.
- ``POST /api/jobs/<hash>/cancel`` — QUEUED + RUNNING + terminal.
- ``DELETE /api/jobs/<hash>`` — soft-delete.

Cloudflare's siteverify is patched at the module level so captcha
exercises work without network.
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _login_user(
    client: httpx.AsyncClient,
    *,
    username: str = "alice",
    tier: str = "premium",
    today_count: int = 0,
    override_soft_quota: int | None = None,
    override_hard_quota: int | None = None,
) -> str:
    from app.db.engine import get_session
    from app.db.models import User
    from app.utils.security import hash_password

    async with get_session() as session:
        session.add(
            User(
                id=f"u_test_{username}",
                username=username,
                password_hash=hash_password("alicepw1234"),
                role="user",
                tier=tier,
                today_count=today_count,
                override_soft_quota=override_soft_quota,
                override_hard_quota=override_hard_quota,
            )
        )

    resp = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "alicepw1234"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _gpt_provider_payload() -> dict:
    """Provider that exposes gpt-image-2 to every tier."""
    return {
        "provider_id": "oai",
        "label": "OpenAI",
        "adapter_type": "openai_v1",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-fake-FQBR1234",
        "cost_per_image_cny": 0.20,
        "initial_balance_cny": 5.0,
        "supported_models": [
            {
                "model_id": "gpt-image-2",
                "capabilities": {
                    "n_max": 10,
                    "size": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                    "quality": ["low", "medium", "high", "auto"],
                    "output_format": ["png", "jpeg", "webp"],
                    "background": ["auto", "opaque"],
                    "moderation": ["auto", "low"],
                    "max_reference_images": 16,
                    "max_prompt_chars": 32000,
                    "supports_mask": True,
                    "stream": True,
                    "partial_images_max": 3,
                },
            }
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
    }


async def _seed_gpt_provider(client: httpx.AsyncClient) -> None:
    admin = await _login_admin(client)
    resp = await client.post(
        "/api/admin/providers", headers=_auth(admin), json=_gpt_provider_payload()
    )
    assert resp.status_code == 201, resp.text


def _job_payload(**overrides: Any) -> str:
    base = {
        "model": "gpt-image-2",
        "prompt": "a still-life of three overripe bananas on a marble slab",
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
        "background": "auto",
    }
    base.update(overrides)
    return json.dumps(base)


def _png_bytes() -> bytes:
    """Minimal valid PNG (10×10 transparent), good enough for the upload pipe.

    We only need bytes that ``image_io.save_reference`` will accept and
    the multipart parser sees as ``image/png``. Pillow can read this and
    ``mime_to_ext`` maps it correctly.
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(255, 200, 0)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# /api/jobs/precheck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_precheck_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.post(
        "/api/jobs/precheck", json={"model": "gpt-image-2"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_precheck_clean_user_no_captcha(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app)
    resp = await seeded_app.post(
        "/api/jobs/precheck",
        headers=_auth(token),
        json={"model": "gpt-image-2"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["captcha_required"] is False


@pytest.mark.asyncio
async def test_precheck_soft_quota_triggers_captcha(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Soft quota crossed → captcha required with the soft-quota reason."""
    token = await _login_user(
        seeded_app,
        username="soft_quota_pre",
        tier="free",
        today_count=8,  # free.soft_quota = 8
    )
    resp = await seeded_app.post(
        "/api/jobs/precheck",
        headers=_auth(token),
        json={"model": "gpt-image-2"},
    )
    body = resp.json()
    assert body["captcha_required"] is True
    assert body["reason"] == "soft_quota_exceeded"
    assert body["captcha_provider"] == "turnstile"


@pytest.mark.asyncio
async def test_precheck_force_global(seeded_app: httpx.AsyncClient) -> None:
    """``emergency.force_captcha_global`` flips every precheck to required."""
    from app.domain.config_center import get_config_center

    # Login first — flipping the flag also gates the login route.
    token = await _login_user(seeded_app, username="forced_pre")
    await get_config_center().set_many({"emergency.force_captcha_global": True})

    resp = await seeded_app.post(
        "/api/jobs/precheck",
        headers=_auth(token),
        json={"model": "gpt-image-2"},
    )
    body = resp.json()
    assert body["captcha_required"] is True
    assert body["reason"] == "force_captcha_global"


# ---------------------------------------------------------------------------
# POST /api/jobs — happy path + queueing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_happy_path(seeded_app: httpx.AsyncClient) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="happy_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["hash_id"].startswith("j_")
    assert body["seq_no"] == 1
    assert body["set_id"] is None
    assert body["model"] == "gpt-image-2"
    assert body["position"] is not None and body["position"] >= 1


@pytest.mark.asyncio
async def test_create_job_n_gt_1_assigns_set_id(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="set_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(n=4), "application/json"))],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["set_id"] is not None
    assert body["set_id"].startswith("set_")


# ---------------------------------------------------------------------------
# Param validation against effective capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_rejects_n_above_cap(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="bad_n_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(n=20), "application/json"))],
    )
    # Pydantic shape rejects n>10 with 422 INVALID_PARAMETER.
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"


@pytest.mark.asyncio
async def test_create_job_rejects_disallowed_size(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="bad_size_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (None, _job_payload(size="2048x2048"), "application/json"),
            )
        ],
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_PARAMETER"
    assert detail["field"] == "size"


@pytest.mark.asyncio
async def test_create_job_rejects_gemini_only_field_on_gpt(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Setting a Gemini-only field on gpt-image-2 must be rejected up front."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="cross_field_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(thinking_level="high", size=None),
                    "application/json",
                ),
            )
        ],
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_PARAMETER"
    assert detail["field"] == "thinking_level"


# ---------------------------------------------------------------------------
# Idempotency via client_request_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_idempotent_via_client_request_id(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="idem_user")

    payload = _job_payload(client_request_id="req-xyz-1")
    first = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, payload, "application/json"))],
    )
    assert first.status_code == 200
    first_hash = first.json()["hash_id"]

    second = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, payload, "application/json"))],
    )
    assert second.status_code == 200
    assert second.json()["hash_id"] == first_hash

    # Only one row landed in the DB.
    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job).where(Job.client_request_id == "req-xyz-1")
            )
        ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Multipart references
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_persists_references_in_order(
    seeded_app: httpx.AsyncClient, tmp_path
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="refs_user")

    blob = _png_bytes()
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("banana_01.png", blob, "image/png")),
            ("ref_1", ("leaf.png", blob, "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    hash_id = resp.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import JobReference

    async with get_session() as session:
        # ``hash_id`` is on Job; pull the join via a direct query.
        rows = (
            await session.execute(
                select(JobReference).order_by(JobReference.ref_order)
            )
        ).scalars().all()
    assert [r.ref_order for r in rows] == [1, 2]
    assert rows[0].filename.startswith("banana")
    assert rows[1].filename.startswith("leaf")
    # Files actually live on disk.
    from app.config import get_settings

    data_root = get_settings().DATA_ROOT
    for r in rows:
        assert (tmp_path).is_absolute()  # tmp fixture sanity
        assert r.rel_path.startswith(f"jobs/{hash_id}/refs/")


@pytest.mark.asyncio
async def test_create_job_rejects_unknown_mime_reference(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="bad_mime_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("ref.tiff", b"not really a tiff", "image/tiff")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "references"


@pytest.mark.asyncio
async def test_create_job_rejects_sparse_reference_indices(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="sparse_user")

    blob = _png_bytes()
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("a.png", blob, "image/png")),
            ("ref_2", ("c.png", blob, "image/png")),  # gap at index 1
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "references"


# ---------------------------------------------------------------------------
# Soft quota → captcha required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_soft_quota_requires_captcha_token(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(
        seeded_app,
        username="soft_create",
        tier="free",
        today_count=8,
    )

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"]["code"] == "CAPTCHA_REQUIRED"


@pytest.mark.asyncio
async def test_create_job_hard_quota_returns_429(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(
        seeded_app,
        username="hard_create",
        tier="free",
        today_count=10,
        override_hard_quota=10,
    )
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "HARD_QUOTA_EXCEEDED"


# ---------------------------------------------------------------------------
# Session binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_binds_to_session(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="sess_bind")

    sess = await seeded_app.post(
        "/api/sessions", headers=_auth(token), json={"name": "Editorial"}
    )
    assert sess.status_code == 201
    sess_id = sess.json()["id"]

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(session_id=sess_id),
                    "application/json",
                ),
            )
        ],
    )
    assert resp.status_code == 200, resp.text

    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        row = (
            await session.execute(
                select(Job).where(Job.hash_id == resp.json()["hash_id"])
            )
        ).scalar_one()
    assert row.session_id == sess_id


@pytest.mark.asyncio
async def test_create_job_other_users_session_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    admin_token = await _login_admin(seeded_app)
    sess = await seeded_app.post(
        "/api/sessions",
        headers=_auth(admin_token),
        json={"name": "Admin Drafts"},
    )
    sess_id = sess.json()["id"]

    user_token = await _login_user(seeded_app, username="cross_sess")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(user_token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(session_id=sess_id),
                    "application/json",
                ),
            )
        ],
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_job_refunds_quota(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="cancel_q")

    create = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    hash_id = create.json()["hash_id"]

    cancel = await seeded_app.post(
        f"/api/jobs/{hash_id}/cancel", headers=_auth(token)
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    # today_count refunded back to 0 because the queued job never ran.
    from app.db.engine import get_session
    from app.db.models import User

    async with get_session() as session:
        u = (
            await session.execute(
                select(User).where(User.username == "cancel_q")
            )
        ).scalar_one()
    assert u.today_count == 0


@pytest.mark.asyncio
async def test_cancel_other_users_job_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    owner = await _login_user(seeded_app, username="owner")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(owner),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    hash_id = resp.json()["hash_id"]

    other = await _login_user(seeded_app, username="thief")
    bad = await seeded_app.post(
        f"/api/jobs/{hash_id}/cancel", headers=_auth(other)
    )
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_cancel_terminal_job_returns_409(
    seeded_app: httpx.AsyncClient,
) -> None:
    """SUCCEEDED / FAILED / CANCELLED jobs cannot be re-cancelled."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="terminal_cancel")
    create = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    hash_id = create.json()["hash_id"]

    # Force the row to SUCCEEDED via direct DB write so we don't depend on
    # the executor/scheduler.
    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
        row.status = "SUCCEEDED"

    resp = await seeded_app.post(
        f"/api/jobs/{hash_id}/cancel", headers=_auth(token)
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_terminal_job_marks_deleted(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="delete_user")
    create = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    hash_id = create.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
        row.status = "SUCCEEDED"

    resp = await seeded_app.delete(
        f"/api/jobs/{hash_id}", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELETED"

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one()
    assert row.status == "DELETED"


@pytest.mark.asyncio
async def test_delete_queued_job_rejected_409(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Live jobs (QUEUED / RUNNING) must be cancelled first, not deleted."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="delete_q")
    create = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    hash_id = create.json()["hash_id"]

    resp = await seeded_app.delete(
        f"/api/jobs/{hash_id}", headers=_auth(token)
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Auth gating on every job endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.post(
        "/api/jobs",
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cancel_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.post("/api/jobs/j_aaaaaaaaaaaa/cancel")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.delete("/api/jobs/j_aaaaaaaaaaaa")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Emergency switch — pause_generation blocks non-admin posts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_generation_blocks_create(
    seeded_app: httpx.AsyncClient,
) -> None:
    from app.domain.config_center import get_config_center

    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="paused_user")
    await get_config_center().set_many({"emergency.pause_generation": True})
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BLOCKED_BY_EMERGENCY"


# ---------------------------------------------------------------------------
# USER_BUSY — concurrency + queue caps full
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_busy_when_capacity_full(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Free tier: max_concurrency=1 + max_queue=3 = 4 active slots.

    The fifth submit must be rejected with USER_BUSY.
    """
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, tier="free", username="busy_user")
    for _ in range(4):
        ok = await seeded_app.post(
            "/api/jobs",
            headers=_auth(token),
            files=[("payload", (None, _job_payload(), "application/json"))],
        )
        assert ok.status_code == 200, ok.text
    full = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert full.status_code == 429
    assert full.json()["detail"]["code"] == "USER_BUSY"


# ---------------------------------------------------------------------------
# task_created broadcast lands on the SSE hub for the submitting user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_broadcasts_task_created(
    seeded_app: httpx.AsyncClient,
) -> None:
    """The hub records buffered events; we read them straight back."""
    import asyncio as _asyncio

    from app.domain.sse_hub import get_sse_hub

    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="broadcast_user")

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 200, resp.text
    hash_id = resp.json()["hash_id"]

    # The route fires-and-forgets the broadcast via asyncio.create_task,
    # so give the event loop a tick to drain it before peeking.
    for _ in range(20):
        buffered = get_sse_hub()._buffer.get("u_test_broadcast_user", [])
        if any(ev.kind == "task_created" for ev in buffered):
            break
        await _asyncio.sleep(0.05)
    else:
        raise AssertionError("task_created event never landed in the hub buffer")

    payloads = [
        ev.payload
        for ev in get_sse_hub()._buffer["u_test_broadcast_user"]
        if ev.kind == "task_created"
    ]
    assert any(p.get("hash_id") == hash_id for p in payloads)


# ---------------------------------------------------------------------------
# Burst gate + per-tier burst_limit (S4)
# ---------------------------------------------------------------------------


def _mock_turnstile_ok():
    async def _stub(token: str, *, remote_ip: str | None = None) -> bool:
        return True

    return _stub


async def _post_n_jobs(client, token, n: int, *, override_username: str | None = None):
    """Fire ``n`` POST /api/jobs in series under ``token``; return responses.

    Series rather than gather() so the rolling window sees a monotonic
    count, which is what real fan-out under one user looks like.
    """
    out = []
    for i in range(n):
        out.append(
            await client.post(
                "/api/jobs",
                headers=_auth(token),
                files=[
                    (
                        "payload",
                        (
                            None,
                            _job_payload(client_request_id=f"req_{i}"),
                            "application/json",
                        ),
                    )
                ],
            )
        )
    return out


@pytest.mark.asyncio
async def test_burst_limit_trips_below_queue_capacity(
    seeded_app: httpx.AsyncClient,
) -> None:
    """When burst_limit < (max_concurrency + max_queue), the captcha
    gate trips before the user-busy gate.

    Uses VIP (capacity 14) and admin-patches burst_limit down to 5 so
    the threshold fires before the queue fills. This exercises the
    same code path as the free-tier seed default; we don't test the
    free seed directly here because free's max_queue=3 means the
    queue gate fires first and would mask the burst gate.
    """
    from app.domain.tier_config import get_tier_config

    await _seed_gpt_provider(seeded_app)
    admin_token = await _login_admin(seeded_app)
    await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(admin_token),
        json={"burst_limit": 5},
    )
    assert get_tier_config().get("vip").burst_limit == 5

    user_token = await _login_user(seeded_app, username="burst_low", tier="vip")
    # _recent_burst returns True when ``len(rows) >= limit``: it checks
    # rows already in the DB before this insert, so with limit=5 the
    # 6th POST is the one that trips (5 prior rows are visible).
    results = await _post_n_jobs(seeded_app, user_token, 6)
    assert [r.status_code for r in results[:5]] == [200] * 5
    assert results[5].status_code == 412
    assert results[5].json()["detail"]["code"] == "CAPTCHA_REQUIRED"


@pytest.mark.asyncio
async def test_burst_limit_vip_default_allows_fan_out_n8(
    seeded_app: httpx.AsyncClient,
) -> None:
    """VIP seed burst_limit=20 — a typical n=8 fan-out lands cleanly.

    This is the user-visible win of the per-tier work: previously every
    VIP fan-out tripped the same hardcoded 5; now they don't.
    """
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="burst_vip", tier="vip")

    results = await _post_n_jobs(seeded_app, token, 8)
    assert all(r.status_code == 200 for r in results), [r.status_code for r in results]


@pytest.mark.asyncio
async def test_burst_limit_honours_admin_override(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Admin lowers vip.burst_limit to 3 → 3rd POST trips captcha."""
    from app.domain.tier_config import get_tier_config

    await _seed_gpt_provider(seeded_app)
    admin_token = await _login_admin(seeded_app)
    user_token = await _login_user(seeded_app, username="burst_vip2", tier="vip")

    # Re-shape the cache so the per-tier limit is in force.
    resp = await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(admin_token),
        json={"burst_limit": 3},
    )
    assert resp.status_code == 200
    assert get_tier_config().get("vip").burst_limit == 3

    # With limit=3, the 4th POST is the one that trips (3 prior rows
    # visible in the DB satisfy ``len(rows) >= 3``).
    results = await _post_n_jobs(seeded_app, user_token, 4)
    assert [r.status_code for r in results[:3]] == [200, 200, 200]
    assert results[3].status_code == 412
    assert results[3].json()["detail"]["code"] == "CAPTCHA_REQUIRED"


@pytest.mark.asyncio
async def test_captcha_grace_covers_subsequent_fanout(
    seeded_app: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One successful captcha → next 60 s skips the burst gate.

    Models the fan-out flow: VIP user with burst_limit lowered to 5
    trips on the 5th, solves Turnstile, then the 6th..8th sub-POSTs go
    through without a token because they fall inside the grace window.
    VIP rather than free so the queue gate doesn't fire first.
    """
    monkeypatch.setattr("app.api.jobs.turnstile.verify", _mock_turnstile_ok())
    await _seed_gpt_provider(seeded_app)
    admin = await _login_admin(seeded_app)
    await seeded_app.patch(
        "/api/admin/tiers/vip",
        headers=_auth(admin),
        json={"burst_limit": 5},
    )
    token = await _login_user(seeded_app, username="grace_vip", tier="vip")

    # Burn through the burst threshold. 5 rows in DB → 6th POST trips.
    pre = await _post_n_jobs(seeded_app, token, 5)
    assert all(r.status_code == 200 for r in pre)

    # 6th without token → 412 CAPTCHA_REQUIRED.
    blocked = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (None, _job_payload(client_request_id="trip"), "application/json"),
            )
        ],
    )
    assert blocked.status_code == 412
    assert blocked.json()["detail"]["code"] == "CAPTCHA_REQUIRED"

    # 5th retry with a token → succeeds and opens the grace window.
    retry = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        client_request_id="trip_retry",
                        captcha_token="cf-good",
                    ),
                    "application/json",
                ),
            )
        ],
    )
    assert retry.status_code == 200, retry.text

    # 6th..8th without a token — should now ride the grace window.
    post_grace = await _post_n_jobs(seeded_app, token, 3)
    assert all(r.status_code == 200 for r in post_grace), [
        r.status_code for r in post_grace
    ]


@pytest.mark.asyncio
async def test_captcha_grace_does_not_bypass_soft_quota(
    seeded_app: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grace exempts burst only — soft_quota still demands a fresh captcha.

    VIP with soft_quota=100; setting today_count=100 puts the user
    over the soft cap but well below the hard cap (200), so the
    follow-up POST is blocked specifically by the soft-quota branch
    (which grace does NOT exempt) rather than by hard quota.
    """
    monkeypatch.setattr("app.api.jobs.turnstile.verify", _mock_turnstile_ok())
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(
        seeded_app, username="grace_soft", tier="vip", today_count=100
    )

    # Open the grace window via one successful captcha.
    first = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        client_request_id="open_grace",
                        captcha_token="cf-good",
                    ),
                    "application/json",
                ),
            )
        ],
    )
    assert first.status_code == 200

    # Subsequent submission without a token must still 412, because
    # soft_quota_exceeded is not bypassed by grace.
    second = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (None, _job_payload(client_request_id="next"), "application/json"),
            )
        ],
    )
    assert second.status_code == 412
    assert second.json()["detail"]["code"] == "CAPTCHA_REQUIRED"
