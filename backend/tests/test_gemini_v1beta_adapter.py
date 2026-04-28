"""Unit tests for ``adapters/gemini_v1beta.py``."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.adapters.gemini_v1beta import GeminiV1BetaAdapter
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
        id="test_gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="goog-key",
        adapter_type="gemini_v1beta",
    )


def _img_b64() -> str:
    return base64.b64encode(b"fake-png-bytes").decode("ascii")


def _ok_response(text: str = "", n_images: int = 1) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"text": text})
    for _ in range(n_images):
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": _img_b64(),
                }
            }
        )
    return {"candidates": [{"content": {"parts": parts}}]}


@pytest.fixture
def mock_httpx(monkeypatch):
    state: dict[str, Any] = {"captured": {}, "status": 200, "body": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        state["captured"]["url"] = str(request.url)
        state["captured"]["method"] = request.method
        state["captured"]["headers"] = dict(request.headers)
        state["captured"]["content"] = request.content
        return httpx.Response(state["status"], json=state["body"])

    real = httpx.AsyncClient

    class Patched(real):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.adapters.gemini_v1beta.httpx.AsyncClient", Patched)
    return state


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_unsupported_model() -> None:
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(model="not-real", prompt="x")
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.kind is StandardErrorKind.UNSUPPORTED_MODEL


@pytest.mark.asyncio
async def test_rejects_n_above_one() -> None:
    """Gemini emits one image per call (design doc §4.1)."""
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview", prompt="x", n=2
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "n"


@pytest.mark.asyncio
async def test_rejects_lowercase_image_size() -> None:
    """Design doc §3.4: ``"1k"`` (lowercase) must be rejected."""
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x",
        image_size="1k",
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "image_size"


@pytest.mark.asyncio
async def test_rejects_512_on_pro() -> None:
    """``512`` is 3.1-flash only."""
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3-pro-image-preview",
        prompt="x",
        image_size="512",
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "image_size"


@pytest.mark.asyncio
async def test_accepts_512_on_flash_31(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x",
        image_size="512",
    )
    await adapter.generate(_provider(), req)
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["generationConfig"]["imageConfig"]["imageSize"] == "512"


@pytest.mark.asyncio
async def test_rejects_extreme_aspect_ratio_on_pro() -> None:
    """``1:4`` is 3.1-flash only."""
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3-pro-image-preview",
        prompt="x",
        aspect_ratio="1:4",
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "aspect_ratio"


@pytest.mark.asyncio
async def test_rejects_thinking_level_on_pro() -> None:
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3-pro-image-preview",
        prompt="x",
        thinking_level="high",
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "thinking_level"


@pytest.mark.asyncio
async def test_rejects_image_search_on_pro() -> None:
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3-pro-image-preview",
        prompt="x",
        image_search=True,
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "image_search"


@pytest.mark.asyncio
async def test_rejects_too_many_references() -> None:
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x",
        references=[
            NormalizedReference(
                order=i, mime="image/png", data_b64=_img_b64()
            )
            for i in range(1, 16)  # 15 > 14
        ],
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "references"


@pytest.mark.asyncio
async def test_rejects_openai_only_fields() -> None:
    adapter = GeminiV1BetaAdapter()
    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="x",
        size="1024x1024",
    )
    with pytest.raises(StandardError) as exc:
        await adapter.generate(_provider(), req)
    assert exc.value.field == "size"


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_url_and_auth_header(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    captured = mock_httpx["captured"]
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.1-flash-image-preview:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "goog-key"


@pytest.mark.asyncio
async def test_response_modalities_always_include_image(mock_httpx) -> None:
    """Design doc §2.4: ``responseModalities`` must declare IMAGE."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


@pytest.mark.asyncio
async def test_image_size_uppercase_k_preserved(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    await adapter.generate(
        _provider(),
        NormalizedRequest(
            model="gemini-3.1-flash-image-preview",
            prompt="x",
            image_size="2K",
        ),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["generationConfig"]["imageConfig"]["imageSize"] == "2K"


@pytest.mark.asyncio
async def test_thinking_config_only_for_flash_31(mock_httpx) -> None:
    """Sanity check the body builder for both models."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()

    # 3.1 flash: thinkingConfig appears.
    await adapter.generate(
        _provider(),
        NormalizedRequest(
            model="gemini-3.1-flash-image-preview",
            prompt="x",
            thinking_level="high",
            include_thoughts=True,
        ),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "high",
        "includeThoughts": True,
    }

    # 3 Pro with no thinking-related fields: no thinkingConfig appears.
    await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3-pro-image-preview", prompt="x"),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert "thinkingConfig" not in body["generationConfig"]


@pytest.mark.asyncio
async def test_references_emitted_in_ascending_order(mock_httpx) -> None:
    """Out-of-order references must be sorted before serialisation.

    The text part is always first; references follow strictly by order.
    """
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    a_b64 = base64.b64encode(b"a").decode("ascii")
    b_b64 = base64.b64encode(b"b").decode("ascii")
    c_b64 = base64.b64encode(b"c").decode("ascii")

    req = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="combine",
        references=[
            NormalizedReference(order=3, mime="image/png", data_b64=c_b64),
            NormalizedReference(order=1, mime="image/png", data_b64=a_b64),
            NormalizedReference(order=2, mime="image/png", data_b64=b_b64),
        ],
    )
    await adapter.generate(_provider(), req)

    body = json.loads(mock_httpx["captured"]["content"])
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "combine"}
    assert parts[1]["inline_data"]["data"] == a_b64
    assert parts[2]["inline_data"]["data"] == b_b64
    assert parts[3]["inline_data"]["data"] == c_b64


@pytest.mark.asyncio
async def test_google_search_tool(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    await adapter.generate(
        _provider(),
        NormalizedRequest(
            model="gemini-3-pro-image-preview",
            prompt="x",
            google_search=True,
        ),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["tools"] == [{"google_search": {}}]


@pytest.mark.asyncio
async def test_image_search_uses_search_types(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response()
    adapter = GeminiV1BetaAdapter()
    await adapter.generate(
        _provider(),
        NormalizedRequest(
            model="gemini-3.1-flash-image-preview",
            prompt="x",
            google_search=True,
            image_search=True,
        ),
    )
    body = json.loads(mock_httpx["captured"]["content"])
    assert body["tools"] == [
        {
            "google_search": {
                "searchTypes": {
                    "webSearch": {},
                    "imageSearch": {},
                }
            }
        }
    ]


# ---------------------------------------------------------------------------
# Response parsing & error normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_returns_image_and_text(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = _ok_response(text="here is your image")
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    assert resp.image_count == 1
    assert resp.images[0].data == b"fake-png-bytes"
    assert resp.text == "here is your image"


@pytest.mark.asyncio
async def test_thought_signature_collected(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": _img_b64(),
                            },
                            "thought_signature": "<sig-A>",
                        }
                    ]
                }
            }
        ]
    }
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    assert resp.metadata["thought_signatures"] == ["<sig-A>"]
    assert resp.images[0].metadata["thought_signature"] == "<sig-A>"


@pytest.mark.asyncio
async def test_thought_only_parts_excluded(mock_httpx) -> None:
    """``thought=true`` parts are not counted as outputs."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "thought": True,
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": _img_b64(),
                            },
                        },
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": _img_b64(),
                            }
                        },
                    ]
                }
            }
        ]
    }
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    # Only the non-thought image counts toward image_count.
    assert resp.image_count == 1


@pytest.mark.asyncio
async def test_text_only_response_raises_empty(mock_httpx) -> None:
    """Upstream answered with text but no inline image."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [{"content": {"parts": [{"text": "I cannot."}]}}]
    }
    adapter = GeminiV1BetaAdapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(),
            NormalizedRequest(
                model="gemini-3.1-flash-image-preview", prompt="x"
            ),
        )
    assert exc.value.kind is StandardErrorKind.EMPTY_RESPONSE


@pytest.mark.asyncio
async def test_grounding_metadata_surfaced(mock_httpx) -> None:
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": _img_b64(),
                            }
                        }
                    ]
                },
                "groundingMetadata": {"searchEntryPoint": "<html>"},
            }
        ]
    }
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(
            model="gemini-3.1-flash-image-preview",
            prompt="x",
            google_search=True,
        ),
    )
    assert resp.metadata["grounding_metadata"]["searchEntryPoint"] == "<html>"


@pytest.mark.asyncio
async def test_401_normalizes_to_auth(mock_httpx) -> None:
    mock_httpx["status"] = 401
    mock_httpx["body"] = {"error": {"message": "bad key"}}
    adapter = GeminiV1BetaAdapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(),
            NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
        )
    assert exc.value.kind is StandardErrorKind.AUTH


@pytest.mark.asyncio
async def test_429_normalizes_to_rate_limited(mock_httpx) -> None:
    mock_httpx["status"] = 429
    mock_httpx["body"] = {"error": {"message": "slow down"}}
    adapter = GeminiV1BetaAdapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(),
            NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
        )
    assert exc.value.kind is StandardErrorKind.RATE_LIMITED


@pytest.mark.asyncio
async def test_500_normalizes_to_upstream_error(mock_httpx) -> None:
    mock_httpx["status"] = 500
    mock_httpx["body"] = {"error": {"message": "boom"}}
    adapter = GeminiV1BetaAdapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(),
            NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
        )
    assert exc.value.kind is StandardErrorKind.UPSTREAM_ERROR


@pytest.mark.asyncio
async def test_camelcase_inline_data_accepted(mock_httpx) -> None:
    """Some relays use camelCase ``inlineData``; we accept either spelling."""
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": _img_b64(),
                            }
                        }
                    ]
                }
            }
        ]
    }
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    assert resp.image_count == 1


@pytest.mark.asyncio
async def test_raw_redacts_b64_blobs(mock_httpx) -> None:
    big_b64 = base64.b64encode(b"x" * 2_000).decode("ascii")
    mock_httpx["status"] = 200
    mock_httpx["body"] = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "narration"},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": big_b64,
                            }
                        },
                    ]
                }
            }
        ]
    }
    adapter = GeminiV1BetaAdapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gemini-3.1-flash-image-preview", prompt="x"),
    )
    assert resp.raw is not None
    serialized = json.dumps(resp.raw)
    assert big_b64 not in serialized
