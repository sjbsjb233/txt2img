"""Tests for the new ``/test-suite`` endpoint and runner.

Covers:
  * Dry-run (suite D only) returns expected case results without
    touching upstream.
  * Manual verdict callback updates run state + writes audit row.
  * SSE stream framing is parseable.
  * Persisted images are served back via the image route.
"""

from __future__ import annotations

import json

import httpx
import pytest


async def _login_admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provider_payload(**overrides) -> dict:
    base = {
        "provider_id": "bltcy",
        "label": "BLTCY",
        "adapter_type": "gemini_v1beta",
        "base_url": "https://api.bltcy.ai/v1beta",
        "api_key": "sk-bltcy-FQBR12345678",
        "cost_per_image_cny": 0.10,
        "initial_balance_cny": 5.0,
        "enabled": True,
        "max_concurrency": 10,
        "rpm_limit": 60,
        "supported_models": [
            {
                "model_id": "gemini-3.1-flash-image-preview",
                "capabilities": {},
                "enabled": True,
            }
        ],
        "tier_access": ["vip", "premium"],
    }
    base.update(overrides)
    return base


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event = None
    data_lines: list[str] = []
    for line in text.splitlines():
        if line == "":
            if event and data_lines:
                payload = "\n".join(data_lines)
                try:
                    events.append((event, json.loads(payload)))
                except Exception:
                    pass
            event = None
            data_lines = []
        elif line.startswith("event: "):
            event = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data_lines.append(line[len("data: "):])
    if event and data_lines:
        payload = "\n".join(data_lines)
        try:
            events.append((event, json.loads(payload)))
        except Exception:
            pass
    return events


@pytest.mark.asyncio
async def test_dry_run_streams_d_suite_results(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Dry-run executes only the D套件 with zero upstream calls."""
    token = await _login_admin(seeded_app)
    create = await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    assert create.status_code == 201, create.text

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gemini-3.1-flash-image-preview",
            "suites": ["D"],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    types = [e[0] for e in events]
    assert "run_start" in types
    assert "case_start" in types
    assert "case_result" in types
    assert "run_done" in types

    # Every case is a D-suite case that the adapter rejects pre-flight.
    case_results = [d for (e, d) in events if e == "case_result"]
    assert len(case_results) > 0
    for r in case_results:
        assert r["suite"] == "D"
        assert r["cost_image"] is False
        # All D-suite cases pass when error_kind matches expectations.
        assert r["ok"] is True, r

    # run_done verdict should be PASS.
    done = next(d for (e, d) in events if e == "run_done")
    assert done["verdict"] == "PASS"
    assert done["cost_used"] == 0


@pytest.mark.asyncio
async def test_manual_verdict_updates_run(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """Manual verdict route flips a SEMI case from pending to pass."""
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    captured: dict = {}

    # Tiny 1x1 PNG
    PNG_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa"
        b"\xcf\x00\x00\x00\x02\x00\x01\xe5\x27\xde\xfc\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )

    async def fake_generate(self, provider, request):
        captured["model"] = request.model
        return NormalizedResponse(
            images=[NormalizedImage(data=PNG_1x1, mime="image/png")],
            image_count=1,
            metadata={"grounding_metadata": {"groundingChunks": [{"x": 1}]}},
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    # Run only B5 (SEMI: googleSearch grounding). Will pass auto checks
    # (image returned + grounding metadata populated), but manual_required.
    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gemini-3.1-flash-image-preview",
            "suites": ["B"],
            "case_ids": ["B5"],
        },
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    done = next(d for (e, d) in events if e == "run_done")
    run_id = done["run_id"]
    assert done["verdict"] == "WARN"

    # Approve manually.
    verdict = await seeded_app.post(
        f"/api/admin/providers/bltcy/test-suite/{run_id}/cases/B5/verdict",
        headers=_auth(token),
        json={"verdict": "pass"},
    )
    assert verdict.status_code == 200, verdict.text
    body = verdict.json()
    assert body["case_id"] == "B5"
    assert body["verdict"] == "pass"


@pytest.mark.asyncio
async def test_persisted_image_served_back(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """The /test-suite/<run>/images/<name> route serves persisted bytes."""
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    PNG_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa"
        b"\xcf\x00\x00\x00\x02\x00\x01\xe5\x27\xde\xfc\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )

    async def fake_generate(self, provider, request):
        return NormalizedResponse(
            images=[NormalizedImage(data=PNG_1x1, mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gemini-3.1-flash-image-preview",
            "suites": ["A"],
        },
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    images = [d for (e, d) in events if e == "case_image"]
    assert images, "expected at least one case_image event"
    img = images[0]
    img_resp = await seeded_app.get(img["bytes_url"], headers=_auth(token))
    assert img_resp.status_code == 200, img_resp.text
    assert img_resp.headers.get("content-type", "").startswith("image/")
    assert img_resp.content[:8] == PNG_1x1[:8]


@pytest.mark.asyncio
async def test_unknown_model_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={"model_id": "not-on-provider", "dry_run": True},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_suite_rejected(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gemini-3.1-flash-image-preview",
            "suites": ["Z"],
        },
    )
    # pydantic validation maps to 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_manual_verdict_unknown_run_returns_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )
    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite/tsuite_doesnotexist/cases/B1/verdict",
        headers=_auth(token),
        json={"verdict": "pass"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_image_url_carries_signature_and_works_unauthenticated(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """bytes_url is signed; can be fetched WITHOUT a bearer token.

    This is the load-bearing property: <img src=...> in the browser
    can't carry an Authorization header, so the route must validate
    by signature alone.
    """
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    PNG_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa"
        b"\xcf\x00\x00\x00\x02\x00\x01\xe5\x27\xde\xfc\x00\x00\x00\x00IEND"
        b"\xaeB`\x82"
    )

    async def fake_generate(self, provider, request):
        return NormalizedResponse(
            images=[NormalizedImage(data=PNG_1x1, mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={"model_id": "gemini-3.1-flash-image-preview", "suites": ["A"]},
    )
    events = _parse_sse(resp.text)
    img = next(d for (e, d) in events if e == "case_image")

    # bytes_url contains both ``sig`` and ``exp`` query parameters.
    assert "sig=" in img["bytes_url"]
    assert "exp=" in img["bytes_url"]

    # Fetch WITHOUT auth header → must still succeed because of signature.
    img_resp = await seeded_app.get(img["bytes_url"])
    assert img_resp.status_code == 200, img_resp.text
    assert img_resp.content[:8] == PNG_1x1[:8]


@pytest.mark.asyncio
async def test_image_url_rejects_tampered_signature(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """Modifying the sig / name / exp invalidates the URL."""
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    async def fake_generate(self, provider, request):
        return NormalizedResponse(
            images=[NormalizedImage(data=b"\x89PNG\r\n\x1a\n", mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={"model_id": "gemini-3.1-flash-image-preview", "suites": ["A"]},
    )
    img = next(d for (e, d) in _parse_sse(resp.text) if e == "case_image")

    # 1. Missing signature
    base_path = img["bytes_url"].split("?")[0]
    bare = await seeded_app.get(base_path)
    assert bare.status_code == 403

    # 2. Tampered signature
    bad = img["bytes_url"].replace("sig=", "sig=tampered")
    assert (await seeded_app.get(bad)).status_code == 403

    # 3. Expired
    import re
    expired = re.sub(r"exp=\d+", "exp=1", img["bytes_url"])
    assert (await seeded_app.get(expired)).status_code == 403


@pytest.mark.skip("C3 retired in mask plan v2; see test_mask_* below.")
def test_c3_left_similarity_synthetic_inputs(fresh_env: None) -> None:
    """Direct unit test of the C3 similarity function on 4 synthetic
    boundary inputs.

    Mirrors the table in design v2 §7:
    - perfect echo                  → > 95%
    - completely different image    → < 30%
    - real-relay style upscale + tone match (sim simulated)
                                     → 60-95% (signal range)
    - mask totally ignored          → < 30%
    """
    from io import BytesIO

    from PIL import Image, ImageDraw

    from app.domain.provider_test_runner import c3_left_similarity
    from app.resources.test_assets import load_edit_base

    base_bytes = load_edit_base().data

    # 1) Identical bytes → similarity ≈ 100%
    assert c3_left_similarity(base_bytes, base_bytes) > 0.95

    # 2) Completely different (solid red 1024x1024) → similarity drops sharply.
    red = Image.new("RGB", (1024, 1024), (220, 30, 20))
    buf = BytesIO()
    red.save(buf, format="PNG")
    diff_bytes = buf.getvalue()
    assert c3_left_similarity(base_bytes, diff_bytes) < 0.30

    # 3) Realistic relay style: upscale to 1254 + paint a sunset over
    # the right half (which c3_left_similarity ignores anyway), keep
    # the left half geometrically the same. Modest seam tone shift on
    # the kept side. Should land well above 0.50 floor — the mask
    # clearly did its job, even if not pixel-perfect.
    with Image.open(BytesIO(base_bytes)) as base_img:
        scaled = base_img.convert("RGB").resize((1254, 1254))
    sunset_overlay = Image.new("RGB", (1254 - 627, 1254), (245, 110, 40))
    scaled.paste(sunset_overlay, (627, 0))
    buf = BytesIO()
    scaled.save(buf, format="PNG")
    realistic_bytes = buf.getvalue()
    sim = c3_left_similarity(base_bytes, realistic_bytes)
    assert sim >= 0.50, f"realistic mock dropped to {sim:.2%}"

    # 4) Mask ignored entirely: replace the whole canvas with sunset-ish
    # colours unrelated to the dark rectangle. Should fall below floor.
    sunset = Image.new("RGB", (1024, 1024), (245, 110, 40))
    buf = BytesIO()
    sunset.save(buf, format="PNG")
    sunset_bytes = buf.getvalue()
    assert c3_left_similarity(base_bytes, sunset_bytes) < 0.30


@pytest.mark.skip("C3 retired in mask plan v2; see test_mask_* below.")
@pytest.mark.asyncio
async def test_c3_judge_returns_ok_true_below_threshold(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """SEMI cases must NEVER auto-fail — even when similarity < 50%.

    Per design v2 §5.2 the C3 judge surfaces the similarity number as
    an informational verdict bullet but always returns ok=True so the
    case lands in manual_pending for human review. This regression
    pins that contract.
    """
    from io import BytesIO

    from PIL import Image

    from app.adapters import openai_v1 as openai_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    # Solid 1024x1024 red — has nothing in common with edit_base, so
    # the similarity drops well below the 50% floor.
    red_buf = BytesIO()
    Image.new("RGB", (1024, 1024), (220, 30, 20)).save(red_buf, format="PNG")
    PNG_RED = red_buf.getvalue()

    async def fake_generate(self, provider, request):
        return NormalizedResponse(
            images=[NormalizedImage(data=PNG_RED, mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(openai_mod.OpenAIV1Adapter, "generate", fake_generate)

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json={
            "provider_id": "openai-c3",
            "label": "openai c3",
            "adapter_type": "openai_v1",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-mock-FQBR",
            "cost_per_image_cny": 0.0,
            "initial_balance_cny": 100,
            "enabled": True,
            "max_concurrency": 4,
            "rpm_limit": 60,
            "supported_models": [
                {"model_id": "gpt-image-2", "capabilities": {}, "enabled": True}
            ],
            "tier_access": ["vip"],
        },
    )

    resp = await seeded_app.post(
        "/api/admin/providers/openai-c3/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gpt-image-2",
            "suites": ["C"],
            "case_ids": ["C3"],
        },
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    c3 = next(d for (e, d) in events if e == "case_result" and d["case_id"] == "C3")

    # Auto-judge ok must be True — admin still gets to look.
    assert c3["ok"] is True, c3
    # Status flips to manual_pending so the human reviews.
    assert c3["status"] == "manual_pending"
    # The low-similarity verdict still appears (so admin sees the warning).
    similarity_bullets = [
        v for v in c3["auto_verdict"] if "相似度" in v.get("text", "")
    ]
    assert similarity_bullets, c3
    assert similarity_bullets[0]["pass"] is False  # low-similarity verdict shown as ✗


@pytest.mark.asyncio
async def test_short_circuit_cases_render_as_skipped_not_fail(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """When the A-suite fails, downstream B/C/D cases are short-circuited.

    The runner labels them with ``manual_verdict='skip'`` and a stable
    ``error_kind=SKIPPED_AFTER_A_FAILURE``. This regression check makes
    sure the SSE payload surfaces them as ``status='skipped'`` (not
    ``fail``) and that the run-done totals count them in the ``skipped``
    bucket — preventing the bug where short-circuited cases falsely
    drove the verdict to FAIL.
    """
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import StandardError, StandardErrorKind

    async def fake_generate(self, provider, request):
        # A1 fails → triggers short-circuit for the rest of the suite.
        raise StandardError(
            StandardErrorKind.UPSTREAM_ERROR, "synthetic A failure"
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={
            "model_id": "gemini-3.1-flash-image-preview",
            "suites": ["A", "D"],
        },
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    results = [d for (e, d) in events if e == "case_result"]
    a_results = [r for r in results if r["suite"] == "A"]
    d_results = [r for r in results if r["suite"] == "D"]
    assert a_results and a_results[0]["ok"] is False
    assert d_results, "D-suite cases should still appear as short-circuited"
    for r in d_results:
        assert r["status"] == "skipped", r
        assert r["error_kind"] == "SKIPPED_AFTER_A_FAILURE"
        assert r["manual_verdict"] == "skip"

    done = next(d for (e, d) in events if e == "run_done")
    assert done["totals"]["skipped"] == len(d_results)
    # The only real failure is A1.
    assert done["totals"]["fail"] == 1


@pytest.mark.asyncio
async def test_image_path_traversal_blocked(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """A crafted name like ``../../etc/passwd`` must be rejected."""
    from app.adapters import gemini_v1beta as gemini_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    async def fake_generate(self, provider, request):
        return NormalizedResponse(
            images=[NormalizedImage(data=b"\x89PNG\r\n", mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(
        gemini_mod.GeminiV1BetaAdapter, "generate", fake_generate
    )

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers", headers=_auth(token), json=_provider_payload()
    )

    resp = await seeded_app.post(
        "/api/admin/providers/bltcy/test-suite",
        headers=_auth(token),
        json={"model_id": "gemini-3.1-flash-image-preview", "suites": ["A"]},
    )
    events = _parse_sse(resp.text)
    done = next(d for (e, d) in events if e == "run_done")
    run_id = done["run_id"]

    bad = await seeded_app.get(
        f"/api/admin/providers/bltcy/test-suite/{run_id}/images/..%2F..%2Fpasswd",
        headers=_auth(token),
    )
    # Either 404 from our guard, or 400 from FastAPI route matching — both reject.
    assert bad.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Mask 测试方案 v2 — M1..M8 regressions
#
# These cover the new replacement for C3: 8 quantitative AUTO cases plus
# the 5-tier ``mask_subverdict`` rollup. The synthetic-input metric tests
# don't touch HTTP at all (just the pure metric functions) so they're
# fast and don't need ``seeded_app``.
# ---------------------------------------------------------------------------


def test_mask_fixtures_present(fresh_env: None) -> None:
    """All 17 fixture files + the manifest must ship with the bundle."""
    from app.resources.test_assets import (
        load_mask_inpaint,
        load_mask_manifest,
        load_mask_outpaint,
        load_mask_scene,
    )

    assert len(load_mask_scene().data) > 1000
    for target in ("villager", "iron_golem"):
        for kind in ("native", "fallback", "overlay"):
            assert len(load_mask_inpaint(target, kind).data) > 1000, (target, kind)
    for scenario in ("right", "bottom"):
        for kind in ("canvas", "native", "fallback", "overlay"):
            assert len(load_mask_outpaint(scenario, kind).data) > 1000, (scenario, kind)
    m = load_mask_manifest()
    assert m["inpaint_targets"]["villager"]["replacement"]
    assert m["outpaint_scenarios"]["right"]["canvas_size"] == [2304, 1024]


def test_inpaint_metrics_perfect_passthrough(fresh_env: None) -> None:
    """If the model returns the input scene unchanged, edit_score ≈ 0 →
    AUTO fails (no edit happened)."""
    from app.domain.provider_test_runner import _inpaint_metrics
    from app.resources.test_assets import load_mask_inpaint, load_mask_scene

    scene = load_mask_scene().data
    mask = load_mask_inpaint("villager", "native").data
    m = _inpaint_metrics(scene, mask, scene)
    assert m["preserve_score"] == pytest.approx(0.0, abs=1e-6)
    assert m["edit_score"] == pytest.approx(0.0, abs=1e-6)
    assert m["edit_score"] <= 0.08  # would fail edit_ok threshold
    assert m["heatmap_bytes"][:4] == b"\x89PNG"


def test_inpaint_metrics_mask_ignored(fresh_env: None) -> None:
    """If the model regenerates the entire image (red), preserve_score
    is high and ratio_score is low → AUTO fails."""
    from io import BytesIO

    from PIL import Image

    from app.domain.provider_test_runner import _inpaint_metrics
    from app.resources.test_assets import load_mask_inpaint, load_mask_scene

    scene_bytes = load_mask_scene().data
    mask_bytes = load_mask_inpaint("villager", "native").data
    buf = BytesIO()
    Image.new("RGB", (1536, 1024), (220, 30, 20)).save(buf, format="PNG")
    m = _inpaint_metrics(scene_bytes, mask_bytes, buf.getvalue())
    assert m["preserve_score"] > 0.05  # preserve_ok fails
    assert m["ratio_score"] <= 3.0  # ratio_ok fails too


def test_inpaint_metrics_happy_path(fresh_env: None) -> None:
    """Scene preserved + a sharp red splash inside the villager bbox →
    preserve ≈ 0 / edit > 0.08 / ratio ≫ 3.0 → AUTO passes."""
    from io import BytesIO

    from PIL import Image

    from app.domain.provider_test_runner import _inpaint_metrics
    from app.resources.test_assets import (
        load_mask_inpaint,
        load_mask_manifest,
        load_mask_scene,
    )

    scene_bytes = load_mask_scene().data
    mask_bytes = load_mask_inpaint("villager", "native").data
    bbox = load_mask_manifest()["inpaint_targets"]["villager"]["bbox"]

    with Image.open(BytesIO(scene_bytes)) as scene_img:
        out = scene_img.convert("RGB").copy()
    splash = Image.new("RGB", (bbox[2] - bbox[0], bbox[3] - bbox[1]), (255, 30, 20))
    out.paste(splash, (bbox[0], bbox[1]))
    buf = BytesIO()
    out.save(buf, format="PNG")

    m = _inpaint_metrics(scene_bytes, mask_bytes, buf.getvalue())
    assert m["preserve_score"] < 0.05, m
    assert m["edit_score"] > 0.08, m
    assert m["ratio_score"] > 3.0, m


def test_outpaint_metrics_black_void(fresh_env: None) -> None:
    """Original area preserved + extension filled with pure black →
    black_void_pct → 100% → AUTO fails."""
    from io import BytesIO

    from PIL import Image

    from app.domain.provider_test_runner import _outpaint_metrics
    from app.resources.test_assets import load_mask_outpaint

    canvas_bytes = load_mask_outpaint("right", "canvas").data
    mask_bytes = load_mask_outpaint("right", "native").data
    with Image.open(BytesIO(canvas_bytes)) as canvas_img:
        canvas_rgba = canvas_img.convert("RGBA")
        out = canvas_rgba.convert("RGB")
    # Right 768px → fill with pure black
    void = Image.new("RGB", (768, 1024), (0, 0, 0))
    out.paste(void, (1536, 0))
    buf = BytesIO()
    out.save(buf, format="PNG")

    m = _outpaint_metrics(canvas_bytes, mask_bytes, buf.getvalue())
    assert m["preserve_score"] < 0.05, m
    assert m["black_void_pct"] > 50.0, m
    assert m["black_void_pct"] <= 100.0


def test_outpaint_metrics_happy_path(fresh_env: None) -> None:
    """Original area preserved + extension filled with hi-freq noise →
    void ≈ 0% / edges > 1% → AUTO passes."""
    import random
    from io import BytesIO

    from PIL import Image

    from app.domain.provider_test_runner import _outpaint_metrics
    from app.resources.test_assets import load_mask_outpaint

    canvas_bytes = load_mask_outpaint("right", "canvas").data
    mask_bytes = load_mask_outpaint("right", "native").data
    with Image.open(BytesIO(canvas_bytes)) as canvas_img:
        out = canvas_img.convert("RGB").copy()

    # 768x1024 of high-contrast checker noise so Sobel produces lots of
    # > 20 magnitudes. Deterministic seed so the test is stable.
    rng = random.Random(42)
    pixels = bytes(
        bytearray(
            rng.choice((20, 220))
            for _ in range(768 * 1024 * 3)
        )
    )
    noise = Image.frombytes("RGB", (768, 1024), pixels)
    out.paste(noise, (1536, 0))
    buf = BytesIO()
    out.save(buf, format="PNG")

    m = _outpaint_metrics(canvas_bytes, mask_bytes, buf.getvalue())
    assert m["preserve_score"] < 0.05, m
    assert m["black_void_pct"] < 5.0, m
    assert m["extension_edges_pct"] > 1.0, m


def test_mask_subverdict_aggregation(fresh_env: None) -> None:
    """All 16 method-mix combinations (× target / scenario pairings) must
    map to the right 5-tier verdict."""
    from app.domain.provider_test_runner import compute_mask_subverdict

    def m(**kw):
        # missing keys mean "case did not run" — they default to False.
        return {k: v for k, v in kw.items() if v is not None}

    # PERFECT — all 8 pass.
    assert (
        compute_mask_subverdict(
            m(M1=True, M2=True, M3=True, M4=True, M5=True, M6=True, M7=True, M8=True)
        )
        == "PERFECT"
    )
    # STANDARD_ONLY — both native pairs pass, fallback fails everywhere.
    assert (
        compute_mask_subverdict(
            m(M1=True, M3=True, M5=True, M7=True,
              M2=False, M4=False, M6=False, M8=False)
        )
        == "STANDARD_ONLY"
    )
    # FALLBACK_ONLY — symmetric.
    assert (
        compute_mask_subverdict(
            m(M2=True, M4=True, M6=True, M8=True,
              M1=False, M3=False, M5=False, M7=False)
        )
        == "FALLBACK_ONLY"
    )
    # PARTIAL_UNUSABLE — only one inpaint target works (single-target
    # "model guessed" case the spec calls out).
    assert (
        compute_mask_subverdict(
            m(M1=True, M3=False, M5=True, M7=True,
              M2=False, M4=False, M6=False, M8=False)
        )
        == "PARTIAL_UNUSABLE"
    )
    # PARTIAL_UNUSABLE — cross-method coverage (native inpaint OK, but
    # only fallback outpaint works) means no single method is reliable.
    assert (
        compute_mask_subverdict(
            m(M1=True, M3=True, M5=False, M7=False,
              M2=False, M4=False, M6=True, M8=True)
        )
        == "PARTIAL_UNUSABLE"
    )
    # UNUSABLE — every case ran and failed.
    assert (
        compute_mask_subverdict(
            m(M1=False, M2=False, M3=False, M4=False,
              M5=False, M6=False, M7=False, M8=False)
        )
        == "UNUSABLE"
    )
    # None — no mask case ran at all.
    assert compute_mask_subverdict({}) is None


def test_mask_prompt_lint_script(fresh_env: None) -> None:
    """The fixtures lint script must pass for the 8 shipped prompts."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "lint_mask_prompts.py"
    assert script.exists(), script
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "all 8 mask prompts are clean" in result.stdout


@pytest.mark.asyncio
async def test_mask_full_suite_streams_eight_cases(
    seeded_app: httpx.AsyncClient, monkeypatch
) -> None:
    """End-to-end: run the C suite, get 8 mask case_result frames and a
    mask_subverdict in run_done.

    Adapter is faked to return a PNG with the original input area echoed
    back unchanged (a "mask ignored" failure mode) → all 8 cases fail
    AUTO → mask_subverdict == UNUSABLE.
    """
    from io import BytesIO

    from PIL import Image

    from app.adapters import openai_v1 as openai_mod
    from app.schemas.normalized import NormalizedImage, NormalizedResponse

    def make_png(size: tuple[int, int]) -> bytes:
        buf = BytesIO()
        Image.new("RGB", size, (220, 30, 20)).save(buf, format="PNG")
        return buf.getvalue()

    async def fake_generate(self, provider, request):
        # Size depends on the requested target dimensions — keep the
        # returned image at the same canvas so the geometry checks in
        # the judges still align.
        w, h = (int(x) for x in (request.size or "1536x1024").split("x"))
        return NormalizedResponse(
            images=[NormalizedImage(data=make_png((w, h)), mime="image/png")],
            image_count=1,
        )

    monkeypatch.setattr(openai_mod.OpenAIV1Adapter, "generate", fake_generate)

    token = await _login_admin(seeded_app)
    await seeded_app.post(
        "/api/admin/providers",
        headers=_auth(token),
        json={
            "provider_id": "openai-mask",
            "label": "openai mask",
            "adapter_type": "openai_v1",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-mock-FQBR",
            "cost_per_image_cny": 0.0,
            "initial_balance_cny": 100,
            "enabled": True,
            "max_concurrency": 4,
            "rpm_limit": 60,
            "supported_models": [
                {"model_id": "gpt-image-2", "capabilities": {}, "enabled": True}
            ],
            "tier_access": ["vip"],
        },
    )

    resp = await seeded_app.post(
        "/api/admin/providers/openai-mask/test-suite",
        headers=_auth(token),
        json={"model_id": "gpt-image-2", "suites": ["C"]},
    )
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    case_ids = [
        d["case_id"]
        for (e, d) in events
        if e == "case_result" and d["case_id"].startswith("M")
    ]
    assert sorted(case_ids) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]

    # Every M case should have a diff_heatmap + mask_overlay diagnostic.
    for ev, d in events:
        if ev != "case_result" or not d["case_id"].startswith("M"):
            continue
        diags = {x["kind"] for x in d.get("diagnostics") or []}
        assert diags == {"heatmap", "overlay"}, (d["case_id"], diags)
        assert d.get("mask_metrics") is not None, d["case_id"]

    done = next(d for (e, d) in events if e == "run_done")
    assert done["mask_subverdict"] == "UNUSABLE", done
