"""T-PEN-NN spec cases (文生图平台测试方案 §5.9)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.fake_turnstile import patch_turnstile
from tests.infra.seeds import auth, install_fake_provider, login_user


pytestmark = [pytest.mark.pen]


def _payload(**o):
    base = {"model": "gpt-image-2", "prompt": "p", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(o)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-PEN-01 · soft-quota precheck → captcha_required
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_pen_01_precheck_requires_captcha(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Free soft=8, hard=10 → user at soft boundary
    user, token = await login_user(
        seeded_app, tier="free", today_count=8
    )
    r = await seeded_app.post(
        "/api/jobs/precheck",
        headers=auth(token),
        json={"model": "gpt-image-2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["captcha_required"] is True
    assert (body.get("reason") or "").lower().startswith("soft")


# ---------------------------------------------------------------------------
# T-PEN-04 · soft penalty failure does NOT debit provider balance
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.prov
async def test_t_pen_04_soft_failure_no_ledger(
    seeded_app: httpx.AsyncClient, monkeypatch
):
    """If the soft penalty rolls a failure, we shouldn't bill the provider."""
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    patch_turnstile(monkeypatch)

    # Force the soft-penalty roll to ALWAYS fail
    from app.domain import soft_penalty as sp

    monkeypatch.setattr(sp, "_random", lambda: 0.0, raising=False)

    user, token = await login_user(
        seeded_app, tier="free", today_count=8
    )
    r = await seeded_app.post(
        "/api/jobs",
        headers=auth(token),
        files={
            "payload": (
                None, _payload(captcha_token="OK-pen04"), "application/json"
            )
        },
    )
    assert r.status_code == 200, r.text
    h = r.json()["hash_id"]

    # Wait for terminal status
    for _ in range(60):
        r = await seeded_app.get(f"/api/jobs/{h}", headers=auth(token))
        if r.status_code == 200 and r.json()["status"] in ("SUCCEEDED", "FAILED"):
            break
        await asyncio.sleep(0.1)

    # Check ledger: no entries for this hash
    from app.db.engine import get_session
    from app.db.models import BillingLedger, Job
    from sqlalchemy import select

    async with get_session() as s:
        job = (await s.execute(select(Job).where(Job.hash_id == h))).scalar_one()
        rows = (
            await s.execute(
                select(BillingLedger).where(BillingLedger.job_id == job.id)
            )
        ).scalars().all()
    # If failure was the soft-penalty kind, no ledger row should exist.
    if job.status == "FAILED":
        assert len(rows) == 0
