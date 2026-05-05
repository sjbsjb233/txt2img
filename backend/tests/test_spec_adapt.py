"""T-ADAPT-NN spec cases (文生图平台测试方案 §5.5).

Real-upstream cases (T-ADAPT-06 / T-ADAPT-07) live in tests/real/.
"""

from __future__ import annotations

import pytest

from app.adapters.base import AdapterRegistry
from app.adapters.openai_v1 import OpenAIV1Adapter
from app.adapters.gemini_v1beta import GeminiV1BetaAdapter


pytestmark = [pytest.mark.adapt]


# ---------------------------------------------------------------------------
# T-ADAPT-01 · auto-discovery sweeps app.adapters/
# ---------------------------------------------------------------------------
@pytest.mark.p0
def test_t_adapt_01_auto_discovery():
    AdapterRegistry.reset_for_tests()
    reg = AdapterRegistry.instance()
    reg.discover()
    types = {a.adapter_type for a in reg.list_all()}
    assert "openai_v1" in types
    assert "gemini_v1beta" in types


# ---------------------------------------------------------------------------
# T-ADAPT-02 · openai_v1 builds the documented payload
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_adapt_02_openai_payload_shape(monkeypatch):
    """Capture the outgoing httpx call and inspect the json body."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, *, json=None, headers=None, content=None, files=None, **kw):
            import json as _json
            captured["url"] = url
            if json is not None:
                captured["json"] = json
            elif content is not None:
                try:
                    captured["json"] = _json.loads(content if isinstance(content, str) else content.decode())
                except Exception:
                    captured["json"] = None
            else:
                captured["json"] = None
            captured["files"] = files
            captured["headers"] = headers or {}
            class R:
                status_code = 200
                text = ""
                def raise_for_status(self):
                    return None
                def json(self_inner):
                    import base64
                    from io import BytesIO
                    from PIL import Image
                    img = Image.new("RGB", (4, 4), "red")
                    buf = BytesIO()
                    img.save(buf, "PNG")
                    return {"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}]}
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    from app.schemas.normalized import NormalizedRequest, ProviderConfig

    adapter = OpenAIV1Adapter()
    provider = ProviderConfig(
        id="oai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        adapter_type="openai_v1",
    )
    request = NormalizedRequest(
        model="gpt-image-2",
        prompt="hello",
        n=2,
        size="1024x1024",
        quality="auto",
        output_format="png",
    )
    await adapter.generate(provider, request)
    assert "json" in captured
    body = captured["json"]
    for required in ("model", "prompt", "n", "size", "quality", "output_format"):
        assert required in body, body
    assert "aspect_ratio" not in body
    assert "image_size" not in body
    assert captured["headers"]["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# T-ADAPT-03 · gemini_v1beta builds the contents/parts shape
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_adapt_03_gemini_payload_shape(monkeypatch):
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, *, json=None, headers=None, content=None, files=None, **kw):
            import json as _json
            captured["url"] = url
            if json is not None:
                captured["json"] = json
            elif content is not None:
                try:
                    captured["json"] = _json.loads(content if isinstance(content, str) else content.decode())
                except Exception:
                    captured["json"] = None
            else:
                captured["json"] = None
            captured["files"] = files
            captured["headers"] = headers or {}
            class R:
                status_code = 200
                text = ""
                def raise_for_status(self):
                    return None
                def json(self_inner):
                    import base64
                    from io import BytesIO
                    from PIL import Image
                    img = Image.new("RGB", (4, 4), "blue")
                    buf = BytesIO()
                    img.save(buf, "PNG")
                    return {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "inlineData": {
                                                "mimeType": "image/png",
                                                "data": base64.b64encode(buf.getvalue()).decode(),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    from app.schemas.normalized import NormalizedRequest, ProviderConfig

    adapter = GeminiV1BetaAdapter()
    provider = ProviderConfig(
        id="g",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="AIzatest",
        adapter_type="gemini_v1beta",
    )
    request = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="hello",
        n=1,
        aspect_ratio="1:1",
        image_size="1K",
        thinking_level="minimal",
    )
    await adapter.generate(provider, request)
    body = captured["json"]
    assert "contents" in body and isinstance(body["contents"], list)
    parts = body["contents"][0]["parts"]
    assert any("text" in p for p in parts)
    gc = body["generationConfig"]
    assert "IMAGE" in gc["responseModalities"]
    assert gc["imageConfig"]["aspectRatio"] == "1:1"
    assert gc["imageConfig"]["imageSize"] == "1K"
    # API key sent in header
    assert "x-goog-api-key" in {k.lower(): v for k, v in captured["headers"].items()}


# ---------------------------------------------------------------------------
# T-ADAPT-04 · ``image_size`` must use capital K
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_adapt_04_image_size_uppercase_k():
    from app.schemas.normalized import NormalizedRequest, ProviderConfig, StandardError

    adapter = GeminiV1BetaAdapter()
    provider = ProviderConfig(
        id="g",
        base_url="https://example",
        api_key="AIz",
        adapter_type="gemini_v1beta",
    )
    bad = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x",
        image_size="1k",  # lowercase
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(provider, bad)
    assert "image_size" in (exc.value.field or "")


# ---------------------------------------------------------------------------
# T-ADAPT-05 · references concatenated by order asc
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_adapt_05_refs_in_order(monkeypatch):
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def post(self, url, *, json=None, headers=None, content=None, files=None, **kw):
            import json as _json
            if content is not None:
                captured["json"] = _json.loads(content if isinstance(content, str) else content.decode())
            else:
                captured["json"] = json
            captured["headers"] = headers
            class R:
                status_code = 200; text = ""
                def raise_for_status(self): return None
                def json(self_inner):
                    import base64
                    from io import BytesIO
                    from PIL import Image
                    img = Image.new("RGB", (4, 4), "blue")
                    buf = BytesIO(); img.save(buf, "PNG")
                    return {
                        "candidates": [{"content": {"parts": [{
                            "inlineData": {"mimeType": "image/png",
                                           "data": base64.b64encode(buf.getvalue()).decode()}
                        }]}}]
                    }
            return R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    from app.schemas.normalized import (
        NormalizedRequest, NormalizedReference, ProviderConfig
    )
    import base64

    adapter = GeminiV1BetaAdapter()
    provider = ProviderConfig(
        id="g",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="AIz",
        adapter_type="gemini_v1beta",
    )
    # Add three refs in scrambled call order — adapter must sort by .order
    refs = [
        NormalizedReference(order=2, mime="image/png", data_b64=base64.b64encode(b"two").decode()),
        NormalizedReference(order=0 + 1, mime="image/png", data_b64=base64.b64encode(b"one").decode()),
        NormalizedReference(order=3, mime="image/png", data_b64=base64.b64encode(b"three").decode()),
    ]
    request = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x", references=refs,
    )
    await adapter.generate(provider, request)
    parts = captured["json"]["contents"][0]["parts"]
    inline_parts = [p for p in parts if "inlineData" in p or "inline_data" in p]
    decoded = []
    for p in inline_parts:
        d = p.get("inlineData") or p.get("inline_data")
        decoded.append(base64.b64decode(d["data"]))
    assert decoded == [b"one", b"two", b"three"]
