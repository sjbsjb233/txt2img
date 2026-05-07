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
