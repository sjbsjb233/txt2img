"""§3 of the testing doc — POST /api/jobs binding to a batch."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from tests._batch_helpers import (
    auth,
    fetch_batch_row,
    login_admin,
    login_user,
    register_batch,
    slot_dict,
)


# ---------------------------------------------------------------------------
# Provider seed (copied from test_jobs_api so this file is self-contained)
# ---------------------------------------------------------------------------


def _gpt_provider_payload() -> dict:
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
    admin = await login_admin(client)
    resp = await client.post(
        "/api/admin/providers", headers=auth(admin), json=_gpt_provider_payload()
    )
    assert resp.status_code == 201, resp.text


def _job_payload(**overrides: Any) -> str:
    base = {
        "model": "gpt-image-2",
        "prompt": "p",
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
        "background": "auto",
    }
    base.update(overrides)
    return json.dumps(base)


async def _post_job(client, token, payload_kwargs):
    return await client.post(
        "/api/jobs",
        headers=auth(token),
        files=[("payload", (None, _job_payload(**payload_kwargs), "application/json"))],
    )


# ---------------------------------------------------------------------------
# Bind happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_basic(seeded_app):
    """Doc §3.test_bind_basic — bind to a submitting batch."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=1)]
    )
    resp = await _post_job(
        seeded_app, token, {"batch_id": batch["batch_id"], "n": 1}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["batch_id"] == batch["batch_id"]
    row = await fetch_batch_row(batch["batch_id"])
    assert row.submitted_count == 1
    # last_activity_at advanced past created_at.
    assert row.last_activity_at >= row.created_at


@pytest.mark.asyncio
async def test_bind_increments_submitted_count(seeded_app):
    """Doc §3.test_bind_increments_submitted_count."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    for i in range(4):
        resp = await _post_job(
            seeded_app,
            token,
            {"batch_id": batch["batch_id"], "n": 1, "set_id": batch["spec"]["slots"][0].get("set_id")}
            if False
            else {"batch_id": batch["batch_id"], "n": 1},
        )
        assert resp.status_code == 200, f"job {i} failed: {resp.text}"
    row = await fetch_batch_row(batch["batch_id"])
    assert row.submitted_count == 4


@pytest.mark.asyncio
async def test_bind_unknown_batch(seeded_app):
    """Doc §3.test_bind_unknown_batch."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    resp = await _post_job(
        seeded_app, token, {"batch_id": "bat_doesnotex"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "batch_id"


@pytest.mark.asyncio
async def test_bind_foreign_batch(seeded_app):
    """Doc §3.test_bind_foreign_batch."""
    await _seed_gpt_provider(seeded_app)
    alice = await login_user(seeded_app)
    bob = await login_user(seeded_app, username="bob")
    batch = await register_batch(seeded_app, bob)
    resp = await _post_job(seeded_app, alice, {"batch_id": batch["batch_id"]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bind_batch_not_submitting(seeded_app):
    """Doc §3.test_bind_batch_not_submitting — finalize first → 409."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=1)]
    )
    finalize = await seeded_app.post(
        f"/api/batches/{batch['batch_id']}/finalize_submission",
        headers=auth(token),
    )
    assert finalize.status_code == 200, finalize.text
    resp = await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "BATCH_INVALID_STATE"


@pytest.mark.asyncio
async def test_bind_batch_full(seeded_app):
    """Doc §3.test_bind_batch_full — third Job after total=2 hits BATCH_FULL."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=2)]
    )
    for _ in range(2):
        await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})
    resp = await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "BATCH_FULL"


@pytest.mark.asyncio
async def test_bind_does_not_auto_promote_status(seeded_app):
    """Doc §3.test_bind_does_not_auto_promote_status."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=2)]
    )
    for _ in range(2):
        await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})
    row = await fetch_batch_row(batch["batch_id"])
    assert row.status == "submitting"


@pytest.mark.asyncio
async def test_bind_format_validation(seeded_app):
    """Doc §3.test_bind_format_validation — bad regex rejected by Pydantic."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    resp = await _post_job(seeded_app, token, {"batch_id": "not-bat-format"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bind_concurrent_increment(seeded_app):
    """Doc §3.test_bind_concurrent_increment — concurrent POSTs all count.

    Capped at 4 here because the 5th submission inside a 60-second
    window trips ``_recent_burst`` and requires a captcha token; that
    is orthogonal to the concurrency-safety property under test. The
    atomic ``UPDATE … SET submitted_count = submitted_count + 1`` in
    :func:`_bind_to_batch` is what keeps every concurrent claim from
    racing the same value — without it SQLite would let the read-
    modify-write of two concurrent transactions both write +1.
    """
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )

    async def one():
        return await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})

    results = await asyncio.gather(*(one() for _ in range(4)))
    successes = sum(1 for r in results if r.status_code == 200)
    assert successes == 4, [r.status_code for r in results]
    row = await fetch_batch_row(batch["batch_id"])
    assert row.submitted_count == 4


@pytest.mark.asyncio
async def test_bind_atomic_under_overlapping_full(seeded_app):
    """Atomic claim guards BATCH_FULL — only ``total_job_count`` succeed."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app, username="atomic_user")
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=2)]
    )

    async def one():
        return await _post_job(seeded_app, token, {"batch_id": batch["batch_id"]})

    # Fire 4 concurrent POSTs against a 2-slot batch — 2 should land,
    # 2 should bounce off as BATCH_FULL.
    results = await asyncio.gather(*(one() for _ in range(4)))
    successes = [r for r in results if r.status_code == 200]
    fulls = [
        r
        for r in results
        if r.status_code == 422
        and r.json().get("detail", {}).get("code") == "BATCH_FULL"
    ]
    assert len(successes) == 2, [r.status_code for r in results]
    assert len(fulls) == 2, [r.json() for r in results]
    row = await fetch_batch_row(batch["batch_id"])
    assert row.submitted_count == 2


@pytest.mark.asyncio
async def test_bind_with_set_id(seeded_app):
    """Doc §3.test_bind_with_set_id — sibling Jobs share batch_id+set_id."""
    await _seed_gpt_provider(seeded_app)
    token = await login_user(seeded_app)
    batch = await register_batch(
        seeded_app, token, slots=[slot_dict(stable_idx=1, image_count=4)]
    )
    set_id = "set_AbCdEfGhIj"
    for _ in range(4):
        resp = await _post_job(
            seeded_app,
            token,
            {
                "batch_id": batch["batch_id"],
                "set_id": set_id,
                "n": 1,
            },
        )
        assert resp.status_code == 200, resp.text
    # DB confirmation
    from sqlalchemy import select

    from app.db.engine import get_session
    from app.db.models import Job

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job).where(
                    Job.batch_id == batch["batch_id"], Job.set_id == set_id
                )
            )
        ).scalars().all()
    assert len(rows) == 4
