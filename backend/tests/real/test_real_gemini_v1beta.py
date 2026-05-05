"""T-ADAPT-07 Real: gemini_v1beta adapter against the configured relay.

Single 1K image, gemini-3.1-flash-image-preview (the cheapest variant).
"""

from __future__ import annotations

import os
from io import BytesIO

import pytest
from PIL import Image

from app.adapters.gemini_v1beta import GeminiV1BetaAdapter
from app.schemas.normalized import (
    NormalizedRequest,
    ProviderConfig,
    StandardError,
)


pytestmark = [pytest.mark.real, pytest.mark.adapt, pytest.mark.p0]


@pytest.mark.asyncio
async def test_t_adapt_07_gemini_v1beta_smoke():
    if not os.environ.get("REAL_GEMINI_API_KEY"):
        pytest.skip("REAL_GEMINI_API_KEY not set")

    adapter = GeminiV1BetaAdapter()
    provider = ProviderConfig(
        id="real-gem",
        base_url=os.environ["REAL_GEMINI_BASE_URL"],
        api_key=os.environ["REAL_GEMINI_API_KEY"],
        adapter_type="gemini_v1beta",
        timeout_seconds=120.0,
    )
    request = NormalizedRequest(
        model="gemini-3.1-flash-image-preview",
        prompt="A small flat-vector mockup of a yellow banana on white.",
        n=1,
        aspect_ratio="1:1",
        image_size="1K",
        thinking_level="minimal",
    )

    try:
        resp = await adapter.generate(provider, request)
    except StandardError as exc:
        pytest.fail(
            f"upstream rejected smoke call: kind={exc.kind} status={exc.upstream_status} "
            f"msg={exc.message} body={exc.upstream_body_excerpt}"
        )

    assert resp.image_count >= 1, resp.raw
    img0 = resp.images[0]
    # PNG or JPEG/WEBP magic — gemini relays sometimes return jpeg
    head = img0.data[:12]
    assert (
        head[:8] == b"\x89PNG\r\n\x1a\n"
        or head[:3] == b"\xff\xd8\xff"  # JPEG SOI
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    ), head
    decoded = Image.open(BytesIO(img0.data))
    decoded.load()
    assert decoded.size[0] > 0 and decoded.size[1] > 0
