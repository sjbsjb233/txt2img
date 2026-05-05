"""T-PROV-NN spec cases (文生图平台测试方案 §5.4)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import auth, install_fake_provider, login_admin, login_user


pytestmark = [pytest.mark.prov]


def _provider_payload(provider_id="oai", tier_access=("vip", "premium", "standard", "free")):
    return {
        "provider_id": provider_id,
        "label": "OpenAI",
        "adapter_type": "openai_v1",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-fakeABCDFQBR1234XYZ",
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
        "tier_access": list(tier_access),
    }


def _job_payload(**overrides):
    base = {"model": "gpt-image-2", "prompt": "test", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-PROV-01 · admin can create a provider; api_key encrypted at rest
# ---------------------------------------------------------------------------
@pytest.mark.p0
@pytest.mark.admin
async def test_t_prov_01_create_provider_encrypted(seeded_app: httpx.AsyncClient):
    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/providers", headers=auth(admin), json=_provider_payload()
    )
    assert r.status_code == 201, r.text

    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import select

    async with get_session() as s:
        prov = (
            await s.execute(select(Provider).where(Provider.id == "oai"))
        ).scalar_one()
    enc = prov.api_key_enc
    if isinstance(enc, bytes):
        assert b"sk-fakeABCDFQBR1234XYZ" not in enc
    else:
        assert "sk-fakeABCDFQBR1234XYZ" not in enc

    list_r = await seeded_app.get("/api/admin/providers", headers=auth(admin))
    body = list_r.json()
    masked = next(p for p in body if p["id"] == "oai")["api_key_masked"]
    # mask_api_key returns ``sk-***LAST4`` (head + *** + last 4)
    assert masked.endswith("4XYZ"), masked
    assert "sk-fakeAB" not in masked
    assert "***" in masked


# ---------------------------------------------------------------------------
# T-PROV-02 · capability change reflected in /api/models
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_prov_02_capability_patch_propagates(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)
    user, token = await login_user(seeded_app, tier="vip")

    # Get baseline aspect ratios for gemini-3-pro
    r = await seeded_app.get("/api/models", headers=auth(token))
    pro_caps = next(
        m for m in r.json()["models"] if m["model_id"] == "gemini-3-pro-image-preview"
    )["capabilities"]
    assert "16:9" in (pro_caps.get("aspect_ratio") or [])

    # Tighten to 1:1 only
    r2 = await seeded_app.patch(
        "/api/admin/providers/fake-1/models/gemini-3-pro-image-preview",
        headers=auth(admin),
        json={"capabilities": {"aspect_ratio": ["1:1"], "image_size": ["1K"]}},
    )
    assert r2.status_code == 200, r2.text

    # Re-fetch
    r3 = await seeded_app.get("/api/models", headers=auth(token))
    pro_caps2 = next(
        m for m in r3.json()["models"] if m["model_id"] == "gemini-3-pro-image-preview"
    )["capabilities"]
    assert "16:9" not in (pro_caps2.get("aspect_ratio") or [])


# ---------------------------------------------------------------------------
# T-PROV-03 · disabled provider not selected
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_prov_03_disabled_not_selected(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)
    # Disable
    r = await seeded_app.patch(
        "/api/admin/providers/fake-1",
        headers=auth(admin),
        json={"enabled": False},
    )
    assert r.status_code == 200, r.text

    user, token = await login_user(seeded_app, tier="free")
    files = {"payload": (None, _job_payload(), "application/json")}
    r2 = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r2.status_code in (422, 503)
    if r2.status_code == 503:
        assert r2.json()["detail"]["code"] == "NO_PROVIDER_AVAILABLE"


# ---------------------------------------------------------------------------
# T-PROV-04 · tier_access restriction enforced
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_prov_04_tier_access_blocks(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    # Only vip + premium; user is standard
    await install_fake_provider(tier_access=("vip", "premium"))
    user, token = await login_user(seeded_app, tier="standard")
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code in (422, 503)


# ---------------------------------------------------------------------------
# T-PROV-05 · balance below threshold → DRAINED
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_prov_05_low_balance_drains(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    # balance 0.4 < threshold 0.5
    await install_fake_provider(balance=0.4)

    user, token = await login_user(seeded_app, tier="free")
    files = {"payload": (None, _job_payload(), "application/json")}
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    # Job admitted; the scheduler will pick it up, the selector finds
    # the only candidate is sub-threshold, marks DRAINED, and the
    # executor records the job FAILED with NO_PROVIDER_AVAILABLE.
    assert r.status_code == 200, r.text

    # Wait for the executor to process and mark drained.
    import asyncio
    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import select

    drained = False
    for _ in range(40):
        await asyncio.sleep(0.1)
        async with get_session() as s:
            prov = (
                await s.execute(select(Provider).where(Provider.id == "fake-1"))
            ).scalar_one()
        if prov.circuit_state == "drained":
            drained = True
            break
    assert drained, f"provider stayed {prov.circuit_state!r}"


# ---------------------------------------------------------------------------
# T-PROV-06 · admin topup → drained → healthy
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_prov_06_topup_recovers(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider(balance=0.1)

    # Force a drained state by submitting once + waiting for executor
    user, token = await login_user(seeded_app, tier="free")
    files = {"payload": (None, _job_payload(), "application/json")}
    await seeded_app.post("/api/jobs", headers=auth(token), files=files)

    import asyncio
    from app.db.engine import get_session as _gs
    from app.db.models import Provider as _P
    from sqlalchemy import select as _sel

    for _ in range(40):
        await asyncio.sleep(0.1)
        async with _gs() as s:
            p = (await s.execute(_sel(_P).where(_P.id == "fake-1"))).scalar_one()
        if p.circuit_state == "drained":
            break

    admin = await login_admin(seeded_app)
    r = await seeded_app.post(
        "/api/admin/providers/fake-1/topup",
        headers=auth(admin),
        json={"amount_cny": 100.0},
    )
    assert r.status_code == 200, r.text

    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import select

    async with get_session() as s:
        prov = (
            await s.execute(select(Provider).where(Provider.id == "fake-1"))
        ).scalar_one()
    assert prov.balance_cny >= 100.0
    assert prov.circuit_state == "healthy"


# ---------------------------------------------------------------------------
# T-PROV-07 · /test endpoint does not write ledger
# ---------------------------------------------------------------------------
@pytest.mark.p1
@pytest.mark.admin
async def test_t_prov_07_test_endpoint_skips_ledger(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    admin = await login_admin(seeded_app)

    from app.db.engine import get_session
    from app.db.models import BillingLedger
    from sqlalchemy import select, func

    async with get_session() as s:
        before = (
            await s.execute(select(func.count(BillingLedger.id)))
        ).scalar() or 0

    r = await seeded_app.post(
        "/api/admin/providers/fake-1/test",
        headers=auth(admin),
        json={"model_id": "gpt-image-2"},
    )
    assert r.status_code == 200, r.text

    async with get_session() as s:
        after = (
            await s.execute(select(func.count(BillingLedger.id)))
        ).scalar() or 0
    assert after == before


# ---------------------------------------------------------------------------
# T-PROV-08 · API key never appears in user-facing responses
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_prov_08_api_key_never_leaks(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")

    # /api/models is the typical user-side endpoint
    r = await seeded_app.get("/api/models", headers=auth(token))
    body = r.text
    assert "fake-key" not in body
    assert "sk-" not in body
    # /api/me, /api/sessions
    for p in ("/api/me", "/api/sessions"):
        r = await seeded_app.get(p, headers=auth(token))
        assert "fake-key" not in r.text
        assert "api_key" not in r.text
