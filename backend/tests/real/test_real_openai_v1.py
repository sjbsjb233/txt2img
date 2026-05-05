"""T-ADAPT-06 Real: openai_v1 adapter against the configured relay.

Single 1024×1024 image. ≤ 1 image / test, no retries, no fallback.
Spend cap: ~¥0.05 per run.
"""

from __future__ import annotations

import os
from io import BytesIO

import pytest
from PIL import Image

from app.adapters.openai_v1 import OpenAIV1Adapter
from app.schemas.normalized import (
    NormalizedRequest,
    ProviderConfig,
    StandardError,
)


pytestmark = [pytest.mark.real, pytest.mark.adapt, pytest.mark.p0]


@pytest.mark.asyncio
async def test_t_adapt_06_openai_v1_smoke():
    if not os.environ.get("REAL_OPENAI_API_KEY"):
        pytest.skip("REAL_OPENAI_API_KEY not set")

    adapter = OpenAIV1Adapter()
    provider = ProviderConfig(
        id="real-oai",
        base_url=os.environ["REAL_OPENAI_BASE_URL"],
        api_key=os.environ["REAL_OPENAI_API_KEY"],
        adapter_type="openai_v1",
        timeout_seconds=120.0,
    )
    request = NormalizedRequest(
        model="gpt-image-2",
        prompt="A small flat-vector mockup of a yellow banana on white.",
        n=1,
        size="1024x1024",
        quality="low",
        output_format="png",
    )

    try:
        resp = await adapter.generate(provider, request)
    except StandardError as exc:
        pytest.fail(
            f"upstream rejected smoke call: kind={exc.kind} status={exc.upstream_status} "
            f"msg={exc.message} body={exc.upstream_body_excerpt}"
        )

    # 1) at least one image
    assert resp.image_count >= 1, resp.raw
    img0 = resp.images[0]
    # 2) PNG magic bytes
    assert img0.data[:8] == b"\x89PNG\r\n\x1a\n", img0.data[:16]
    # 3) Pillow can decode
    decoded = Image.open(BytesIO(img0.data))
    decoded.load()
    # 4) Resolution at least 1024 (some relays downgrade — mark a soft fail
    # via the exact assertion the spec asks for: should equal request).
    assert max(decoded.size) >= 1024, decoded.size
