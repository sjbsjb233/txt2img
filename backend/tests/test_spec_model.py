"""T-MODEL-NN spec cases (文生图平台测试方案 §5.6)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.infra.fake_adapter import (
    FULL_CAPS_GPT_IMAGE_2,
    FakeAdapter,
    register_fake_adapter,
)
from tests.infra.seeds import auth, install_fake_provider, login_admin, login_user


pytestmark = [pytest.mark.model]


def _job_payload(**overrides):
    base = {"model": "gpt-image-2", "prompt": "cat", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# T-MODEL-01 · /api/models filtered by tier_access
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_model_01_filtered_by_tier(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    # Provider A: vip-only, supports gpt-image-2
    await install_fake_provider(provider_id="vip-only", tier_access=("vip",))
    # Provider B: all tiers, supports gemini only
    await install_fake_provider(
        provider_id="all-gem",
        tier_access=("vip", "premium", "standard", "free"),
        models=("gemini-3.1-flash-image-preview",),
    )

    user, token = await login_user(seeded_app, tier="standard")
    r = await seeded_app.get("/api/models", headers=auth(token))
    assert r.status_code == 200
    by_id = {m["model_id"]: m for m in r.json()["models"]}
    # gpt-image-2 may appear in the descriptor list (catalog floors include
    # the canonical models) but must NOT be available to the standard user.
    if "gpt-image-2" in by_id:
        assert by_id["gpt-image-2"]["available"] is False
    # gemini-3.1-flash-image-preview is provided by all-gem (all tiers).
    assert by_id["gemini-3.1-flash-image-preview"]["available"] is True


# ---------------------------------------------------------------------------
# T-MODEL-02 · capability union across providers
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_model_02_caps_union(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    # Two providers each supporting gemini-3-pro with different aspect lists
    from app.db.engine import get_session
    from app.db.models import (
        Provider, ProviderModel, ProviderTierAccess,
    )
    from app.utils.crypto import encrypt

    async with get_session() as s:
        for pid, ratios in (("a", ["1:1", "16:9"]), ("b", ["1:1", "21:9"])):
            s.add(
                Provider(
                    id=pid, label=pid.upper(), adapter_type="fake",
                    base_url="http://fake", api_key_enc=encrypt("k"),
                    cost_per_image_cny=0.001, initial_balance_cny=10,
                    balance_cny=10, max_concurrency=4, rpm_limit=600,
                    enabled=1, circuit_state="healthy",
                )
            )
            s.add(
                ProviderModel(
                    provider_id=pid,
                    model_id="gemini-3-pro-image-preview",
                    capabilities_json=json.dumps({
                        "aspect_ratio": ratios, "image_size": ["1K", "2K"],
                        "n_max": 1,
                    }),
                    enabled=1,
                )
            )
            for t in ("vip", "premium", "standard", "free"):
                s.add(ProviderTierAccess(provider_id=pid, tier=t))

    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.get("/api/models", headers=auth(token))
    pro = next(m for m in r.json()["models"] if m["model_id"] == "gemini-3-pro-image-preview")
    aspect = set(pro["capabilities"].get("aspect_ratio") or [])
    assert {"1:1", "16:9", "21:9"}.issubset(aspect)


# ---------------------------------------------------------------------------
# T-MODEL-03 · all providers down → model.available=false
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_model_03_all_circuits_open(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    # Force fake-1's circuit OPEN
    from app.db.engine import get_session
    from app.db.models import Provider
    from sqlalchemy import update

    async with get_session() as s:
        await s.execute(
            update(Provider).where(Provider.id == "fake-1").values(circuit_state="open")
        )

    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.get("/api/models", headers=auth(token))
    body = r.json()["models"]
    # When all providers are open, model is excluded entirely or marked unavailable
    if body:
        for m in body:
            assert m.get("available") is False or "all_providers_circuit_open" in str(m.get("reason", ""))


# ---------------------------------------------------------------------------
# T-MODEL-04 · defaults match design doc §5.3 (no temperature)
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_model_04_defaults(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    r = await seeded_app.get("/api/models", headers=auth(token))
    by_id = {m["model_id"]: m for m in r.json()["models"]}

    gpt = by_id.get("gpt-image-2")
    if gpt:
        d = gpt["defaults"]
        assert d["n"] == 4
        assert d["quality"] == "auto"
        assert "temperature" not in d

    flash = by_id.get("gemini-3.1-flash-image-preview")
    if flash:
        d = flash["defaults"]
        assert d["n"] == 1
        assert d.get("aspect_ratio") == "1:1"
        assert d.get("thinking_level") == "minimal"
        assert "temperature" not in d


# ---------------------------------------------------------------------------
# T-MODEL-05 · invalid parameter → 422 INVALID_PARAMETER + field
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_model_05_invalid_parameter(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {
        "payload": (
            None,
            _job_payload(model="gpt-image-2", aspect_ratio="21:9"),
            "application/json",
        )
    }
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_PARAMETER"
    assert detail["field"] == "aspect_ratio"


# ---------------------------------------------------------------------------
# T-MODEL-06 · gemini family locks n=1
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_model_06_gemini_n1(seeded_app: httpx.AsyncClient):
    register_fake_adapter()
    FakeAdapter.reset()
    await install_fake_provider()
    user, token = await login_user(seeded_app, tier="vip")
    files = {
        "payload": (
            None,
            json.dumps({
                "model": "gemini-3.1-flash-image-preview",
                "prompt": "x", "n": 4, "aspect_ratio": "1:1",
            }),
            "application/json",
        )
    }
    r = await seeded_app.post("/api/jobs", headers=auth(token), files=files)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_PARAMETER"
    assert r.json()["detail"]["field"] == "n"
