"""Unit tests for ``adapters/openai_v1.py``.

We mock ``httpx.AsyncClient`` so no real network call is ever made.
Each test asserts both *what we send* (so the wire format is provable
against the design doc) and *how we react to upstream responses* (so
errors get classified correctly for the circuit breaker).
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

import httpx
import pytest

from app.adapters import openai_v1 as openai_module
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


def _patch_httpx_with_handler(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace ``httpx.AsyncClient`` so it routes through ``handler``.

    Shared by every test that needs custom routing for both the API
    POST and the CDN GET. Avoids re-defining the same Patched subclass
    in each test.
    """
    real_async_client = httpx.AsyncClient

    class Patched(real_async_client):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.adapters.openai_v1.httpx.AsyncClient", Patched)


def _bypass_url_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_is_safe_url`` always allow.

    URL-format parser tests rely on ``MockTransport`` routing both the
    API call and the CDN fetch — but ``_is_safe_url`` does its own DNS
    lookup before any GET, which MockTransport can't intercept. Tests
    that target the parser itself bypass the safety check; SSRF
    behaviour has dedicated tests below.
    """

    async def _ok(_url: str) -> bool:
        return True

    monkeypatch.setattr(openai_module, "_is_safe_url", _ok)


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
async def test_url_format_response_is_downloaded(monkeypatch) -> None:
    """OpenAI's default response uses ``url`` rather than ``b64_json``.

    Relays such as BLTCY always go through CDN URLs. The adapter must
    GET each URL and surface the downloaded bytes as if they had arrived
    inline. We mock the CDN response with a custom MockTransport handler
    that routes both the API call and the CDN GET.
    """
    _bypass_url_safety(monkeypatch)
    api_url = "https://api.example.com/v1/images/generations"
    cdn_url = "https://cdn.example.com/img.png?sig=secrettoken"
    cdn_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == api_url:
            return httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [
                        {
                            "url": cdn_url,
                            "revised_prompt": "rewritten prompt",
                        }
                    ],
                },
            )
        if str(request.url) == cdn_url:
            return httpx.Response(
                200,
                content=cdn_bytes,
                headers={"content-type": "image/png"},
            )
        return httpx.Response(404)

    _patch_httpx_with_handler(monkeypatch, handler)

    adapter = OpenAIV1Adapter()
    resp = await adapter.generate(
        _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
    )
    assert resp.image_count == 1
    assert resp.images[0].data == cdn_bytes
    assert resp.images[0].mime == "image/png"
    assert resp.images[0].metadata["revised_prompt"] == "rewritten prompt"
    # The signed-token query string must NOT make it into metadata —
    # the adapter sanitises before logging.
    stored_url = resp.images[0].metadata["url"]
    assert "secrettoken" not in stored_url
    assert stored_url.startswith("https://cdn.example.com/img.png")


@pytest.mark.asyncio
async def test_url_download_failure_skips_item(monkeypatch) -> None:
    """If the CDN GET fails the item is skipped, not the whole call.

    With only one item that fails we end up with no usable images, which
    surfaces as ``EMPTY_RESPONSE`` — the same failure mode the executor
    expects when an upstream returns text-only.
    """
    _bypass_url_safety(monkeypatch)
    api_url = "https://api.example.com/v1/images/generations"
    cdn_url = "https://cdn.example.com/img.png"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == api_url:
            return httpx.Response(
                200,
                json={"created": 1, "data": [{"url": cdn_url}]},
            )
        # CDN is down.
        return httpx.Response(502, content=b"")

    _patch_httpx_with_handler(monkeypatch, handler)

    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.EMPTY_RESPONSE


@pytest.mark.asyncio
async def test_b64_takes_precedence_over_url(monkeypatch) -> None:
    """When both fields are present we prefer b64 to avoid an extra GET."""
    _bypass_url_safety(monkeypatch)
    api_url = "https://api.example.com/v1/images/generations"
    inline_b64 = base64.b64encode(b"inline-bytes").decode("ascii")
    cdn_hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == api_url:
            return httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [
                        {
                            "b64_json": inline_b64,
                            "url": "https://cdn.example.com/should_not_fetch.png",
                        }
                    ],
                },
            )
        cdn_hits.append(str(request.url))
        return httpx.Response(500)

    _patch_httpx_with_handler(monkeypatch, handler)

    adapter = OpenAIV1Adapter()
    resp = await adapter.generate(
        _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
    )
    assert resp.images[0].data == b"inline-bytes"
    # Confirm we did not fall through to the CDN.
    assert cdn_hits == []


# ---------------------------------------------------------------------------
# URL safety / SSRF defence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        # AWS / GCP metadata endpoint (link-local).
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        # Loopback.
        "http://127.0.0.1:8080/api/internal",
        "http://localhost/admin",
        # IPv6 loopback.
        "http://[::1]/x",
        # Bad scheme.
        "file:///etc/passwd",
        "ftp://cdn.example.com/x",
        # Malformed.
        "not a url",
        "",
    ],
)
async def test_is_safe_url_blocks_unsafe(url: str) -> None:
    """Defense in depth: all of these must be rejected before fetching.

    Narrowed in PR-13 follow-up: RFC1918 / 198.18.0.0/15 are intentionally
    allowed because Mainland China transparent-proxy setups hand them out
    as fake-IP anchors that route to real public CDNs (see comment on
    ``_is_blocked_ip``). The truly dangerous targets — loopback, link-local
    cloud metadata, unspecified — remain blocked.
    """
    from app.adapters.openai_v1 import _is_safe_url

    assert await _is_safe_url(url) is False


@pytest.mark.asyncio
async def test_is_safe_url_accepts_public_ip() -> None:
    """A public IP literal passes the safety check."""
    from app.adapters.openai_v1 import _is_safe_url

    # Cloudflare's documentation IP — public, not private.
    assert await _is_safe_url("https://1.1.1.1/img.png") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        # RFC1918 — proxy / split-DNS legitimate use case.
        "http://10.0.0.5/x.png",
        "http://192.168.1.10/image.png",
        "http://172.16.0.1/secret.png",
        # 198.18.0.0/15 (RFC2544 benchmark range) — fake-IP anchor for
        # transparent proxy deployments.
        "http://198.18.2.251/cdn/img.png",
    ],
)
async def test_is_safe_url_accepts_proxy_anchored_private(url: str) -> None:
    """RFC1918 and 198.18.0.0/15 must NOT be blocked.

    Mainland China VPN / transparent-proxy stacks rewrite legitimate CDN
    hostnames into these ranges and route them through the proxy to the
    real upstream. Blocking them would have broken every relay whose
    hostname the local resolver maps that way.
    """
    from app.adapters.openai_v1 import _is_safe_url

    assert await _is_safe_url(url) is True


@pytest.mark.asyncio
async def test_url_to_internal_host_is_skipped(monkeypatch) -> None:
    """A relay returning an SSRF-style URL must not trigger a fetch.

    We don't bypass ``_is_safe_url`` here — that's the point of the test.
    We do patch the API call's transport so the test stays hermetic, but
    use a literal IP in the metadata URL so the safety check classifies
    it without any DNS.
    """
    api_url = "https://api.example.com/v1/images/generations"
    cdn_hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == api_url:
            return httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [
                        {"url": "http://169.254.169.254/secrets"}
                    ],
                },
            )
        cdn_hits.append(str(request.url))
        return httpx.Response(200, content=b"x", headers={"content-type": "image/png"})

    _patch_httpx_with_handler(monkeypatch, handler)

    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.EMPTY_RESPONSE
    # Crucially: we never even attempted the metadata fetch.
    assert cdn_hits == []


# ---------------------------------------------------------------------------
# Non-image content-type guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_image_content_type_is_rejected(monkeypatch) -> None:
    """If the CDN serves text/html we treat the item as a failed download.

    Relays sometimes 200 with an HTML error page when the underlying
    storage is unhappy. Persisting that as a "PNG" would be wrong.
    """
    _bypass_url_safety(monkeypatch)
    api_url = "https://api.example.com/v1/images/generations"
    cdn_url = "https://cdn.example.com/img.png"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == api_url:
            return httpx.Response(
                200, json={"created": 1, "data": [{"url": cdn_url}]}
            )
        return httpx.Response(
            200,
            content=b"<html>nope</html>",
            headers={"content-type": "text/html"},
        )

    _patch_httpx_with_handler(monkeypatch, handler)

    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        await adapter.generate(
            _provider(), NormalizedRequest(model="gpt-image-2", prompt="x")
        )
    assert exc.value.kind is StandardErrorKind.EMPTY_RESPONSE


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_downloads_run_concurrently(monkeypatch) -> None:
    """``n>1`` URL fetches run via ``asyncio.gather``.

    We confirm concurrency by counting peak in-flight CDN requests with
    a shared counter. Sequential downloads would peak at 1; the
    parallel implementation should peak at >1.
    """
    import asyncio

    _bypass_url_safety(monkeypatch)
    api_url = "https://api.example.com/v1/images/generations"
    cdn_bytes = b"\x89PNG"

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def async_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        if str(request.url) == api_url:
            return httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [
                        {"url": f"https://cdn.example.com/{i}.png"}
                        for i in range(4)
                    ],
                },
            )
        # MockTransport accepts async handlers. We hold the request open
        # long enough that a parallel implementation lets multiple CDN
        # fetches overlap; a sequential one peaks at 1.
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return httpx.Response(
            200, content=cdn_bytes, headers={"content-type": "image/png"}
        )

    _patch_httpx_with_handler(monkeypatch, async_handler)

    adapter = OpenAIV1Adapter()
    resp = await adapter.generate(
        _provider(),
        NormalizedRequest(model="gpt-image-2", prompt="x", n=4),
    )
    assert resp.image_count == 4
    # If the implementation were sequential the peak would be exactly 1.
    assert peak >= 2, f"expected concurrent fetches; peak={peak}"


# ---------------------------------------------------------------------------
# URL sanitiser unit tests
# ---------------------------------------------------------------------------


def test_sanitize_url_strips_query_and_fragment() -> None:
    from app.adapters.openai_v1 import _sanitize_url_for_log

    assert (
        _sanitize_url_for_log(
            "https://cdn.example.com/img.png?sig=abc&exp=999#frag"
        )
        == "https://cdn.example.com/img.png"
    )


def test_sanitize_url_keeps_host_and_path() -> None:
    from app.adapters.openai_v1 import _sanitize_url_for_log

    # No query / fragment to begin with — passthrough.
    assert (
        _sanitize_url_for_log("https://cdn.example.com/a/b/img.webp")
        == "https://cdn.example.com/a/b/img.webp"
    )


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
