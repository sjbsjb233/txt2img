"""T-PROV-09 / T-E2E-06 / T-E2E-07 Real flow tests.

Mounts a real OpenAI / Gemini provider via the admin API, then either
hits ``/api/admin/providers/<id>/test`` (T-PROV-09) or pushes one job
through the full executor pipeline (T-E2E-06 / T-E2E-07) and reads
back the rendered PNG.

Budget: 1 image per E2E test (~¥0.05 each).
"""

from __future__ import annotations

import asyncio
import json
import os
from io import BytesIO

import httpx
import pytest
from PIL import Image

from tests.infra.seeds import auth, login_admin, login_user


pytestmark = [pytest.mark.real]


# ---------------------------------------------------------------------------
# Provider payload builders
# ---------------------------------------------------------------------------


def _openai_provider() -> dict:
    return {
        "provider_id": "real-oai",
        "label": "Real OpenAI (relay)",
        "adapter_type": "openai_v1",
        "base_url": os.environ["REAL_OPENAI_BASE_URL"],
        "api_key": os.environ["REAL_OPENAI_API_KEY"],
        "cost_per_image_cny": 0.20,
        "initial_balance_cny": 100.0,
        "supported_models": [
            {
                "model_id": "gpt-image-2",
                "capabilities": {
                    "n_max": 4,
                    "size": ["1024x1024", "auto"],
                    "quality": ["low", "medium", "high", "auto"],
                    "output_format": ["png"],
                    "background": ["auto"],
                    "moderation": ["auto"],
                    "max_reference_images": 0,
                    "max_prompt_chars": 4000,
                    "supports_mask": False,
                    "stream": False,
                    "partial_images_max": 0,
                },
            }
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
    }


def _gemini_provider() -> dict:
    return {
        "provider_id": "real-gem",
        "label": "Real Gemini (relay)",
        "adapter_type": "gemini_v1beta",
        "base_url": os.environ["REAL_GEMINI_BASE_URL"],
        "api_key": os.environ["REAL_GEMINI_API_KEY"],
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 100.0,
        "supported_models": [
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {
                    "n_max": 1,
                    "aspect_ratio": ["1:1"],
                    "image_size": ["1K"],
                    "thinking_level": ["minimal"],
                    "max_reference_images": 0,
                    "max_prompt_chars": 4000,
                    "supports_mask": False,
                    "include_thoughts": False,
                    "google_search": False,
                    "image_search": False,
                },
            }
        ],
        "tier_access": ["vip", "premium", "standard", "free"],
    }


def _job_payload_openai() -> str:
    return json.dumps({
        "model": "gpt-image-2",
        "prompt": "A small flat-vector mockup of a yellow banana on white.",
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
        "output_format": "png",
    })


def _job_payload_gemini() -> str:
    return json.dumps({
        "model": "gemini-3.1-flash-image-preview",
        "prompt": "A small flat-vector mockup of a yellow banana on white.",
        "n": 1,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "thinking_level": "minimal",
    })


async def _wait_terminal(seeded_app, token, hash_id, timeout=180.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await seeded_app.get(f"/api/jobs/{hash_id}", headers=auth(token))
        if r.status_code == 200 and r.json()["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return r.json()
        await asyncio.sleep(1.0)
    raise AssertionError(f"job {hash_id} not terminal in {timeout}s")


# ---------------------------------------------------------------------------
# T-PROV-09 · OpenAI provider /test
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.prov
async def test_t_prov_09_openai_provider_test_endpoint(seeded_app: httpx.AsyncClient):
    if not os.environ.get("REAL_OPENAI_API_KEY"):
        pytest.skip("REAL_OPENAI_API_KEY not set")

    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/providers", headers=auth(admin), json=_openai_provider()
    )
    assert r.status_code == 201, r.text

    test = await seeded_app.post(
        "/api/admin/providers/real-oai/test",
        headers=auth(admin),
        json={"model_id": "gpt-image-2"},
        timeout=180.0,
    )
    assert test.status_code == 200, test.text
    body = test.json()
    assert body["ok"] is True, body
    assert body["image_count"] >= 1
    assert body["latency_ms"] > 0
    # ledger should NOT have grown — /test is a side-effect-free probe
    from app.db.engine import get_session
    from app.db.models import BillingLedger
    from sqlalchemy import select, func

    async with get_session() as s:
        n = (await s.execute(select(func.count(BillingLedger.id)))).scalar() or 0
    assert n == 0


# ---------------------------------------------------------------------------
# T-E2E-06 · full OpenAI flow through the job pipeline
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.job
async def test_t_e2e_06_openai_full_flow(seeded_app: httpx.AsyncClient):
    if not os.environ.get("REAL_OPENAI_API_KEY"):
        pytest.skip("REAL_OPENAI_API_KEY not set")

    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/providers", headers=auth(admin), json=_openai_provider()
    )
    assert r.status_code == 201, r.text

    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload_openai(), "application/json")}
    sub = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert sub.status_code == 200, sub.text
    h = sub.json()["hash_id"]

    detail = await _wait_terminal(seeded_app, token, h, timeout=180.0)
    assert detail["status"] == "SUCCEEDED", detail
    assert detail.get("images") and len(detail["images"]) >= 1

    # Pull the original image and verify bytes
    img_resp = await seeded_app.get(
        f"/api/jobs/{h}/images/1/original", headers=auth(token), timeout=30.0
    )
    assert img_resp.status_code == 200
    assert img_resp.content[:8] == b"\x89PNG\r\n\x1a\n", img_resp.content[:16]
    decoded = Image.open(BytesIO(img_resp.content))
    decoded.load()
    assert max(decoded.size) >= 1024, decoded.size

    # Ledger should have one row, balance should be ≈ initial - cost_per_image
    from app.db.engine import get_session
    from app.db.models import BillingLedger, Provider
    from sqlalchemy import select, func

    async with get_session() as s:
        n = (await s.execute(select(func.count(BillingLedger.id)))).scalar() or 0
        prov = (
            await s.execute(select(Provider).where(Provider.id == "real-oai"))
        ).scalar_one()
    assert n >= 1
    assert prov.balance_cny < prov.initial_balance_cny


# ---------------------------------------------------------------------------
# T-E2E-07 · full Gemini flow through the job pipeline
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.job
async def test_t_e2e_07_gemini_full_flow(seeded_app: httpx.AsyncClient):
    if not os.environ.get("REAL_GEMINI_API_KEY"):
        pytest.skip("REAL_GEMINI_API_KEY not set")

    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/providers", headers=auth(admin), json=_gemini_provider()
    )
    assert r.status_code == 201, r.text

    user, token = await login_user(seeded_app, tier="vip")
    files = {"payload": (None, _job_payload_gemini(), "application/json")}
    sub = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert sub.status_code == 200, sub.text
    h = sub.json()["hash_id"]

    detail = await _wait_terminal(seeded_app, token, h, timeout=180.0)
    assert detail["status"] == "SUCCEEDED", detail
    assert detail.get("images") and len(detail["images"]) >= 1

    img_resp = await seeded_app.get(
        f"/api/jobs/{h}/images/1/original", headers=auth(token), timeout=30.0
    )
    assert img_resp.status_code == 200
    head = img_resp.content[:12]
    assert (
        head[:8] == b"\x89PNG\r\n\x1a\n"
        or head[:3] == b"\xff\xd8\xff"
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    ), head
    decoded = Image.open(BytesIO(img_resp.content))
    decoded.load()
    assert decoded.size[0] > 0 and decoded.size[1] > 0

    from app.db.engine import get_session
    from app.db.models import BillingLedger, Provider
    from sqlalchemy import select, func

    async with get_session() as s:
        n = (await s.execute(select(func.count(BillingLedger.id)))).scalar() or 0
        prov = (
            await s.execute(select(Provider).where(Provider.id == "real-gem"))
        ).scalar_one()
    assert n >= 1
    assert prov.balance_cny < prov.initial_balance_cny
