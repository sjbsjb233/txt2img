"""T-BREAK-NN spec cases (文生图平台测试方案 §5.10)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import auth, install_fake_provider, login_user


pytestmark = [pytest.mark.breaker]


def _payload(**o):
    base = {"model": "gpt-image-2", "prompt": "p", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(o)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-BREAK-01 · N consecutive failures → OPEN
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_break_01_open_after_failures(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    FakeAdapter.behavior["fail_rate"] = 1.0
    FakeAdapter.behavior["fail_with"] = "504"
    await install_fake_provider()

    user, token = await login_user(seeded_app, tier="vip")

    # Trigger > failure_threshold (default 5) failed attempts
    for _ in range(7):
        r = await seeded_app.post(
            "/api/jobs",
            headers=auth(token),
            files={"payload": (None, _payload(), "application/json")},
        )
        if r.status_code != 200:
            break
        h = r.json()["hash_id"]
        for _ in range(60):
            d = await seeded_app.get(f"/api/jobs/{h}", headers=auth(token))
            if d.status_code == 200 and d.json()["status"] in ("FAILED", "SUCCEEDED"):
                break
            await asyncio.sleep(0.05)

    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import select

    async with get_session() as s:
        prov = (
            await s.execute(select(Provider).where(Provider.id == "fake-1"))
        ).scalar_one()
    assert prov.circuit_state in ("open", "half_open")


# ---------------------------------------------------------------------------
# T-BREAK-02 · OPEN provider not selected → NO_PROVIDER_AVAILABLE
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.sched
async def test_t_break_02_open_excluded(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Force provider OPEN with future cooldown
    from datetime import datetime, timedelta, timezone
    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import update

    cooldown = datetime.now(timezone.utc) + timedelta(minutes=10)
    async with get_session() as s:
        await s.execute(
            update(Provider)
            .where(Provider.id == "fake-1")
            .values(circuit_state="open", cooldown_until=cooldown)
        )

    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.post(
        "/api/jobs",
        headers=auth(token),
        files={"payload": (None, _payload(), "application/json")},
    )
    assert r.status_code == 200, r.text
    h = r.json()["hash_id"]

    # Job will be picked up but no candidate → FAILED with NO_PROVIDER_AVAILABLE
    for _ in range(60):
        d = await seeded_app.get(f"/api/jobs/{h}", headers=auth(token))
        if d.status_code == 200 and d.json()["status"] in ("FAILED", "SUCCEEDED"):
            break
        await asyncio.sleep(0.05)
    body = d.json()
    assert body["status"] == "FAILED"
    reason = body.get("status_reason") or ""
    assert "NO_PROVIDER" in reason or "PROVIDER" in reason
