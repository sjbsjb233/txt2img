"""Unit tests for ``adapters/openai_v1.py``.

We mock ``httpx.AsyncClient`` so no real network call is ever made.
Each test asserts both *what we send* (so the wire format is provable
against the design doc) and *how we react to upstream responses* (so
errors get classified correctly for the circuit breaker).
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.adapters.openai_v1 import OpenAIV1Adapter
from app.schemas.normalized import (
    NormalizedReference,
    NormalizedRequest,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provider() -> ProviderConfig:
    return ProviderConfig(
        id="test_openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test-secret",
        adapter_type="openai_v1",
    )


def _png_b64() -> str:
    """Tiny valid base64 (the bytes don't have to actually be a PNG)."""
    return base64.b64encode(b"fake-image-bytes").decode("ascii")


def _capture_handler(captured: dict[str, Any], status: int, body: dict[str, Any]):
    """Build a custom ``httpx`` MockTransport handler.

    The handler stashes the inbound request into ``captured`` so the
    test can assert on URL / headers / body.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content
        return httpx.Response(status, json=body)

    return handler


@pytest.fixture
def mock_httpx(monkeypatch):
    """Replace ``httpx.AsyncClient`` so every adapter call hits MockTransport.

    Returns the ``captured`` dict the test populates and asserts on.
    """
    state: dict[str, Any] = {"captured": {}, "status": 200, "body": {}}

    real_async_client = httpx.AsyncClient

    class Patched(real_async_client):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            transport = httpx.MockTransport(
                _capture_handler(state["captured"], state["status"], state["body"])
            )
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.adapters.openai_v1.httpx.AsyncClient", Patched)
    return state


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_unsupported_model() -> None:
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(model="not-real", prompt="x")
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.kind is StandardErrorKind.UNSUPPORTED_MODEL


@pytest.mark.asyncio
async def test_rejects_transparent_background() -> None:
    """Design doc §1.4: gpt-image-2 specifically does not support transparent."""
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(model="gpt-image-2", prompt="x", background="transparent")
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.kind is StandardErrorKind.INVALID_PARAMETER
    assert exc.value.field == "background"


@pytest.mark.asyncio
async def test_rejects_n_above_ten() -> None:
    # Pydantic itself rejects n>10 at construction time. Confirm it.
    with pytest.raises(Exception):
        NormalizedRequest(model="gpt-image-2", prompt="x", n=20)


@pytest.mark.asyncio
async def test_rejects_aspect_ratio_for_gpt_image_2() -> None:
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(model="gpt-image-2", prompt="x", aspect_ratio="16:9")
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.kind is StandardErrorKind.INVALID_PARAMETER
    assert exc.value.field == "aspect_ratio"


@pytest.mark.asyncio
async def test_rejects_compression_without_jpeg_or_webp() -> None:
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="x",
        output_format="png",
        output_compression=50,
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "output_compression"


@pytest.mark.asyncio
async def test_rejects_partial_images_without_stream() -> None:
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2", prompt="x", partial_images=2, stream=False
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "partial_images"


@pytest.mark.asyncio
async def test_rejects_duplicate_reference_orders() -> None:
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="x",
        references=[
            NormalizedReference(order=1, mime="image/png", data_b64=_png_b64()),
            NormalizedReference(order=1, mime="image/png", data_b64=_png_b64()),
        ],
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "references"


# ---------------------------------------------------------------------------
# Wire format — generations endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generations_request_body_shape(mock_httpx) -> None:
    """Plain generation: hits /images/generations with the documented body."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="A photoreal vase",
        n=2,
        size="1536x1024",
        quality="high",
        output_format="webp",
        output_compression=70,
        background="opaque",
        moderation="auto",
    )
    resp = await adapter.generate(_provider(), req)
    assert resp.image_count == 1
    assert resp.images[0].data == b"fake-image-bytes"
    assert resp.images[0].mime == "image/webp"

    captured = mock_httpx["captured"]
    assert captured["url"] == "https://api.example.com/v1/images/generations"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer sk-test-secret"
    body = json.loads(captured["content"])
    assert body == {
        "model": "gpt-image-2",
        "prompt": "A photoreal vase",
        "n": 2,
        "size": "1536x1024",
        "quality": "high",
        "output_format": "webp",
        "output_compression": 70,
        "background": "opaque",
        "moderation": "auto",
    }


@pytest.mark.asyncio
async def test_generations_omits_unset_fields(mock_httpx) -> None:
    """Defaults are not emitted; ``input_fidelity`` is never on the wire."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(model="gpt-image-2", prompt="x")
    await adapter.generate(_provider(), req)

    body = json.loads(mock_httpx["captured"]["content"])
    assert body == {"model": "gpt-image-2", "prompt": "x"}
    assert "input_fidelity" not in body
    assert "n" not in body
    assert "size" not in body


@pytest.mark.asyncio
async def test_generations_stream_enables_partial_images(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2", prompt="x", stream=True, partial_images=2
    )
    await adapter.generate(_provider(), req)

    body = json.loads(mock_httpx["captured"]["content"])
    assert body["stream"] is True
    assert body["partial_images"] == 2


# ---------------------------------------------------------------------------
# Wire format — edits endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edits_request_uses_multipart_and_preserves_order(mock_httpx) -> None:
    """Multiple references are emitted as image[] in ``order`` ascending.

    We submit them out of order to prove the adapter sorts before
    serialising; design doc §6.5 makes this load-bearing.
    """
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="combine these",
        references=[
            NormalizedReference(
                order=3, mime="image/png", data_b64=_png_b64(), filename="c.png"
            ),
            NormalizedReference(
                order=1, mime="image/png", data_b64=_png_b64(), filename="a.png"
            ),
            NormalizedReference(
                order=2, mime="image/jpeg", data_b64=_png_b64(), filename="b.jpg"
            ),
        ],
    )
    await adapter.generate(_provider(), req)

    captured = mock_httpx["captured"]
    assert captured["url"] == "https://api.example.com/v1/images/edits"
    body = captured["content"].decode("latin-1")
    # Filenames must appear in ascending ``order``: a.png → b.jpg → c.png.
    idx_a = body.find("a.png")
    idx_b = body.find("b.jpg")
    idx_c = body.find("c.png")
    assert 0 < idx_a < idx_b < idx_c
    # All three are sent under the image[] field name.
    assert body.count('name="image[]"') == 3


@pytest.mark.asyncio
async def test_edits_single_reference_uses_image_field(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="edit me",
        references=[
            NormalizedReference(order=1, mime="image/png", data_b64=_png_b64())
        ],
    )
    await adapter.generate(_provider(), req)

    body = mock_httpx["captured"]["content"].decode("latin-1")
    assert 'name="image"' in body
    assert 'name="image[]"' not in body


@pytest.mark.asyncio
async def test_edits_with_mask_uses_image_array(mock_httpx) -> None:
    """When a mask is present we always use image[] for the references."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": _png_b64()}]}
    adapter = OpenAIV1Adapter()
    req = NormalizedRequest(
        model="gpt-image-2",
        prompt="inpaint",
        references=[
            NormalizedReference(order=1, mime="image/png", data_b64=_png_b64())
        ],
        mask=NormalizedReference(order=1, mime="image/png", data_b64=_png_b64()),
    )
    await adapter.generate(_provider(), req)
    body = mock_httpx["captured"]["content"].decode("latin-1")
    assert 'name="image[]"' in body
    assert 'name="mask"' in body


# ---------------------------------------------------------------------------
# Response parsing & error normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_response_extracts_revised_prompt(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "created": 1,
        "data": [
            {"b64_json": _png_b64(), "revised_prompt": "rewritten"},
            {"b64_json": _png_b64()},
        ],
    }
    adapter = OpenAIV1Adapter()
    resp = await adapter.generate(
        _provider(), NormalizedRequest(model="gpt-image-2", prompt="x", n=2)
    )
    assert resp.image_count == 2
    assert resp.images[0].metadata["revised_prompt"] == "rewritten"
    assert "revised_prompt" not in resp.images[1].metadata


@pytest.mark.asyncio
async def test_empty_data_array_raises_empty_response(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {"created": 1, "data": []}
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.EMPTY_RESPONSE


@pytest.mark.asyncio
async def test_401_normalizes_to_auth(mock_httpx) -> None:
    mock_httpx["status"] = 401
    mock_httpx["body"] = {"error": {"message": "bad key"}}
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.AUTH


@pytest.mark.asyncio
async def test_429_normalizes_to_rate_limited(mock_httpx) -> None:
    mock_httpx["status"] = 429
    mock_httpx["body"] = {"error": {"message": "slow down"}}
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.RATE_LIMITED


@pytest.mark.asyncio
async def test_500_normalizes_to_upstream_error(mock_httpx) -> None:
    mock_httpx["status"] = 503
    mock_httpx["body"] = {"error": {"message": "boom"}}
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.UPSTREAM_ERROR


@pytest.mark.asyncio
async def test_400_normalizes_to_invalid_parameter(mock_httpx) -> None:
    mock_httpx["status"] = 400
    mock_httpx["body"] = {"error": {"message": "bad prompt"}}
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.INVALID_PARAMETER


@pytest.mark.asyncio
async def test_raw_redacts_b64_blobs(mock_httpx) -> None:
    """The per-attempt log should not carry raw base64 image data."""
    mock_httpx["status"] = 200
    big_b64 = base64.b64encode(b"x" * 2_000).decode("ascii")
    mock_httpx["body"] = {"created": 1, "data": [{"b64_json": big_b64}]}
    adapter = OpenAIV1Adapter()
    resp = await adapter.generate(
        _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
    )
    assert resp.raw is not None
    assert "data" in resp.raw
    # ``b64_json`` shouldn't be present at all; we keep only its length.
    serialized = json.dumps(resp.raw)
    assert big_b64 not in serialized
    assert resp.raw["data"][0]["b64_json_len"] == len(big_b64)
