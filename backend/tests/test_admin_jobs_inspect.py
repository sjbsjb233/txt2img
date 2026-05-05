"""Tests for ``app.api.admin.jobs`` (Admin Job Inspector)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import select


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(
    *, username: str, password: str, tier: str = "free", status: str = "active"
) -> str:
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
                password_hash=hash_password(password),
                role="user",
                tier=tier,
                status=status,
            )
        )
    return uid


async def _seed_job(
    *,
    user_id: str,
    status: str = "SUCCEEDED",
    model: str = "gemini-3.1-flash-image-preview",
    provider_used: str | None = "bltcy",
    cost_cny: float = 0.123,
    retries: int = 0,
    status_reason: str | None = None,
    params: dict[str, Any] | None = None,
    flags: dict[str, Any] | None = None,
) -> str:
    import json
    from app.db.engine import get_session
    from app.db.models import Job, User
    from app.utils.ids import new_job_hash_id, new_job_internal_id

    job_id = new_job_internal_id()
    hash_id = new_job_hash_id()
    now = datetime.now(timezone.utc)

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        seq = (user.last_seq_no or 0) + 1
        user.last_seq_no = seq
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit=user.tier,
                seq_no=seq,
                model=model,
                params_json=json.dumps(params or {"prompt": "a cat"}),
                flags_json=json.dumps(flags or {}),
                status=status,
                status_reason=status_reason,
                provider_used=provider_used,
                retries=retries,
                cost_cny=cost_cny,
                created_at=now,
                queued_at=now,
                dispatched_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
        )
    return hash_id


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_requires_auth(seeded_app: httpx.AsyncClient) -> None:
    resp = await seeded_app.get("/api/admin/jobs/j_AAAAAAAAAAAA/inspect")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_inspect_requires_admin(seeded_app: httpx.AsyncClient) -> None:
    await _seed_user(username="alice", password="alicepw1")
    token = await _login(seeded_app, "alice", "alicepw1")
    resp = await seeded_app.get(
        "/api/admin/jobs/j_AAAAAAAAAAAA/inspect", headers=_auth(token)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_inspect_404_unknown_hash(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/jobs/j_DOESNOTEXIST/inspect", headers=_auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inspect_404_invalid_hash(seeded_app: httpx.AsyncClient) -> None:
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        "/api/admin/jobs/not-a-hash/inspect", headers=_auth(token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_returns_admin_only_fields(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="bob", password="bobpw123")
    hash_id = await _seed_job(
        user_id=uid,
        status="SUCCEEDED",
        provider_used="bltcy",
        cost_cny=0.42,
        retries=2,
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hash_id"] == hash_id
    assert body["provider_used"] == "bltcy"
    assert body["retries"] == 2
    assert body["cost_cny"] == pytest.approx(0.42)
    assert body["user_username"] == "bob"
    assert "lifecycle" in body
    assert body["lifecycle"]["queued_at"] is not None
    # Terminal job → cacheable hint
    assert resp.headers.get("cache-control", "").startswith("private")


@pytest.mark.asyncio
async def test_inspect_running_job_is_not_cacheable(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="charlie", password="charlie1")
    hash_id = await _seed_job(user_id=uid, status="RUNNING", provider_used=None)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_inspect_writes_audit_log(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="dave", password="davepw123")
    hash_id = await _seed_job(user_id=uid)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200

    from app.db.engine import get_session
    from app.db.models import AuditLog

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "admin.job.inspect"
                    )
                )
            ).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].target_id == hash_id


@pytest.mark.asyncio
async def test_inspect_failed_job_includes_status_reason(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="eve", password="evepw1234")
    hash_id = await _seed_job(
        user_id=uid,
        status="FAILED",
        provider_used=None,
        status_reason="ALL_PROVIDERS_FAILED",
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["status_reason"] == "ALL_PROVIDERS_FAILED"
    assert body["error"] == "ALL_PROVIDERS_FAILED"


# ---------------------------------------------------------------------------
# Upstream raw log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_log_404_when_missing(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="frank", password="frankpw1")
    hash_id = await _seed_job(user_id=uid)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/upstream/1", headers=_auth(token)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upstream_log_writes_audit_row(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="grace2", password="grace2pw")
    hash_id = await _seed_job(user_id=uid)
    from app.services import image_io

    image_io.write_upstream_log(
        hash_id, 1, {"provider_id": "bltcy", "ok": True}
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/upstream/1", headers=_auth(token)
    )
    assert resp.status_code == 200

    from app.db.engine import get_session
    from app.db.models import AuditLog

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "admin.job.upstream_log"
                    )
                )
            ).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].target_id == hash_id


@pytest.mark.asyncio
async def test_inspect_surfaces_nested_upstream_status_and_body(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Executor writes upstream_status / upstream_body_excerpt under
    the nested ``error`` dict; inspector should surface both.
    """
    uid = await _seed_user(username="izzy", password="izzypw123")
    hash_id = await _seed_job(
        user_id=uid, status="FAILED", provider_used=None
    )
    from app.services import image_io

    image_io.write_upstream_log(
        hash_id,
        1,
        {
            "provider_id": "bltcy",
            "ok": False,
            "started_at": "2026-05-05T07:30:01Z",
            "latency_ms": 9.9,
            "error": {
                "kind": "RATE_LIMITED",
                "message": "rate limit hit",
                "upstream_status": 429,
                "upstream_body_excerpt": "Too Many Requests",
            },
            "provider_snapshot": {
                "circuit_state": "healthy",
                "max_concurrency": 8,
                "rpm_limit": 60,
            },
        },
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 1
    a = body["attempts"][0]
    assert a["upstream_status"] == 429
    assert a["upstream_body_excerpt"] == "Too Many Requests"
    assert a["error_kind"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_upstream_log_redacts_sensitive_keys(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="grace", password="gracepw1")
    hash_id = await _seed_job(user_id=uid)
    # Drop a fake attempt log on disk.
    from app.services import image_io

    image_io.write_upstream_log(
        hash_id,
        1,
        {
            "provider_id": "bltcy",
            "ok": False,
            "started_at": "2025-01-01T00:00:00Z",
            "latency_ms": 12.3,
            "request_headers": {
                "Authorization": "Bearer sk-leaktest1234567890ABCDEF",
            },
            "error": {
                "kind": "AUTH",
                "message": "401 sk-leaktest1234567890ABCDEF returned",
            },
        },
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/upstream/1", headers=_auth(token)
    )
    assert resp.status_code == 200
    text = resp.text
    assert "sk-leaktest" not in text
    assert "REDACTED" in text


# ---------------------------------------------------------------------------
# Re-queue
# ---------------------------------------------------------------------------


async def _seed_provider(
    *,
    provider_id: str = "bltcy_oai_pro",
    label: str = "Bltcy OAI Pro",
    max_concurrency: int = 8,
    rpm_limit: int = 60,
    circuit_state: str = "healthy",
) -> None:
    from app.db.engine import get_session
    from app.db.models import Provider

    async with get_session() as session:
        session.add(
            Provider(
                id=provider_id,
                label=label,
                adapter_type="openai",
                base_url="https://example.com",
                api_key_enc="enc:dummy",
                cost_per_image_cny=0.1,
                initial_balance_cny=100.0,
                balance_cny=99.0,
                enabled=1,
                max_concurrency=max_concurrency,
                rpm_limit=rpm_limit,
                circuit_state=circuit_state,
            )
        )


@pytest.mark.asyncio
async def test_inspect_falls_back_to_provider_static_when_snapshot_missing(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Legacy job: attempt log has no ``provider_snapshot`` block.
    Inspector should fill ``max_concurrency`` / ``rpm_limit`` /
    ``circuit_state`` from the live Provider row instead of returning
    a row of em-dashes.
    """
    uid = await _seed_user(username="legacy1", password="legacy1pw")
    await _seed_provider()
    hash_id = await _seed_job(
        user_id=uid, status="SUCCEEDED", provider_used="bltcy_oai_pro"
    )
    from app.services import image_io

    image_io.write_upstream_log(
        hash_id,
        1,
        {
            "provider_id": "bltcy_oai_pro",
            "ok": True,
            "started_at": "2026-05-05T05:47:48Z",
            "latency_ms": 169000,
            # Note: no ``provider_snapshot`` field — pre-Phase-2 shape.
        },
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 1
    snap = body["attempts"][0]["provider_snapshot"]
    assert snap["max_concurrency"] == 8
    assert snap["rpm_limit"] == 60
    assert snap["circuit_state"] == "healthy"
    # Dynamic fields stay null because we genuinely don't know.
    assert snap["success_rate_5m"] is None
    assert snap["p50_latency_ms"] is None


@pytest.mark.asyncio
async def test_inspect_synthesises_minimal_routing_trace_when_missing(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Legacy job: no routing.json on disk. The endpoint should still
    pin the chosen provider in the ``scored`` list and report the
    ``routing`` degraded section so the UI can show a disclaimer.
    """
    uid = await _seed_user(username="legacy2", password="legacy2pw")
    await _seed_provider()
    hash_id = await _seed_job(
        user_id=uid, status="SUCCEEDED", provider_used="bltcy_oai_pro"
    )
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routing"] is not None
    assert body["routing"]["scored"][0]["provider_id"] == "bltcy_oai_pro"
    assert body["routing"]["scored"][0]["chosen"] is True
    assert "routing" in body["degraded_sections"]


@pytest.mark.asyncio
async def test_inspect_marks_user_state_synthetic_when_timeline_missing(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Legacy job: no timeline.jsonl user_state record. Endpoint falls
    back to live DB row + tier defaults but flags the section as
    synthetic so admins know not to read historical meaning into it.
    """
    uid = await _seed_user(username="legacy3", password="legacy3pw")
    hash_id = await _seed_job(user_id=uid)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.get(
        f"/api/admin/jobs/{hash_id}/inspect", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_state_at_submit"] is not None
    assert "user_state_synthetic" in body["degraded_sections"]


@pytest.mark.asyncio
async def test_requeue_clones_into_fresh_queued_job(
    seeded_app: httpx.AsyncClient,
) -> None:
    uid = await _seed_user(username="henry", password="henrypw1")
    src_hash = await _seed_job(user_id=uid, status="FAILED", provider_used=None)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        f"/api/admin/jobs/{src_hash}/requeue", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_hash = body["new_hash_id"]
    assert new_hash != src_hash
    assert body["source_hash_id"] == src_hash

    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        new_row = (
            await session.execute(select(Job).where(Job.hash_id == new_hash))
        ).scalar_one()
    assert new_row.status == "QUEUED"
    assert new_row.user_id == uid


@pytest.mark.asyncio
async def test_requeue_rejects_non_terminal(seeded_app: httpx.AsyncClient) -> None:
    uid = await _seed_user(username="ivy", password="ivypw1234")
    hash_id = await _seed_job(user_id=uid, status="RUNNING", provider_used=None)
    token = await _login_admin(seeded_app)
    resp = await seeded_app.post(
        f"/api/admin/jobs/{hash_id}/requeue", headers=_auth(token)
    )
    assert resp.status_code == 422
