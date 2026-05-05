"""Test-only adapter that lets cases steer upstream behaviour.

Spec ref: 文生图平台测试方案 §3.1. Class-level ``behavior`` is mutable
state used to swap delays / failure rates / image counts per test.
"""

from __future__ import annotations

import asyncio
import io
import json
import random
from typing import Any, ClassVar

from PIL import Image

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)


_ALL_MODELS: tuple[str, ...] = (
    "gpt-image-2",
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
)

# Caps that mirror the design doc's full set so tests don't get bitten by
# the validator rejecting a parameter the FakeAdapter would happily accept.
FULL_CAPS_GPT_IMAGE_2: dict[str, Any] = {
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
}

FULL_CAPS_GEM_PRO: dict[str, Any] = {
    "n_max": 1,
    "aspect_ratio": [
        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
        "9:16", "16:9", "21:9",
    ],
    "image_size": ["1K", "2K", "4K"],
    "thinking_level": ["minimal", "high"],
    "max_reference_images": 14,
    "max_prompt_chars": 32000,
    "supports_mask": False,
    "include_thoughts": True,
    "google_search": True,
    "image_search": False,
}

FULL_CAPS_GEM_FLASH: dict[str, Any] = {
    "n_max": 1,
    "aspect_ratio": [
        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
        "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1",
    ],
    "image_size": ["512", "1K", "2K", "4K"],
    "thinking_level": ["minimal", "high"],
    "max_reference_images": 14,
    "max_prompt_chars": 32000,
    "supports_mask": False,
    "include_thoughts": True,
    "google_search": True,
    "image_search": True,
}


def _solid_color_png(hex_color: str = "#9bdac5", size: tuple[int, int] = (20, 20)) -> bytes:
    """Tiny deterministic PNG. ~150 bytes."""
    img = Image.new("RGB", size, hex_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeAdapter(BaseAdapter):
    adapter_type = "fake"
    display_name = "Fake (test only)"
    description = "Controllable in-process adapter — never reaches network."

    # Class-level controls. Tests mutate these directly.
    behavior: ClassVar[dict[str, Any]] = {
        "delay_seconds": 0.01,
        "fail_rate": 0.0,
        "fail_with": "504",
        "image_count_returned": None,  # None → respect request.n
        "image_color": "#9bdac5",
        "image_size": (20, 20),
        "extra_latency_for_provider": {},
        "fail_for_provider": {},
        "calls": [],
        "last_payload": None,
    }

    @classmethod
    def reset(cls) -> None:
        cls.behavior.update({
            "delay_seconds": 0.01,
            "fail_rate": 0.0,
            "fail_with": "504",
            "image_count_returned": None,
            "image_color": "#9bdac5",
            "image_size": (20, 20),
            "extra_latency_for_provider": {},
            "fail_for_provider": {},
            "calls": [],
            "last_payload": None,
        })

    def supported_models(self) -> list[str]:
        return list(_ALL_MODELS)

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        b = type(self).behavior
        b["calls"].append(provider.id)
        b["last_payload"] = {
            "provider_id": provider.id,
            "model": request.model,
            "n": request.n,
            "prompt_len": len(request.prompt),
            "ref_count": len(request.references),
        }
        delay = float(b["delay_seconds"])
        delay += float(b["extra_latency_for_provider"].get(provider.id, 0))
        if delay > 0:
            await asyncio.sleep(delay)

        # Per-provider override beats global rate.
        per_prov = b["fail_for_provider"].get(provider.id)
        if per_prov is not None:
            should_fail = bool(per_prov)
        else:
            should_fail = random.random() < float(b["fail_rate"])

        if should_fail:
            raise self._raise_error(b["fail_with"])

        n = int(b["image_count_returned"]) if b["image_count_returned"] is not None else int(request.n)
        n = max(1, min(n, request.n if request.n else n))
        png = _solid_color_png(b["image_color"], b["image_size"])
        images = [
            NormalizedImage(data=png, mime="image/png",
                            width=b["image_size"][0], height=b["image_size"][1])
            for _ in range(n)
        ]
        return NormalizedResponse(
            images=images,
            image_count=len(images),
            raw={"ok": True, "fake": True},
        )

    @staticmethod
    def _raise_error(kind: str) -> StandardError:
        kinds = {
            "504": StandardError(
                StandardErrorKind.UPSTREAM_TIMEOUT,
                "fake upstream timeout",
                upstream_status=504,
            ),
            "500": StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                "fake upstream 500",
                upstream_status=500,
            ),
            "401": StandardError(
                StandardErrorKind.AUTH,
                "fake upstream auth failure",
                upstream_status=401,
            ),
            "429": StandardError(
                StandardErrorKind.RATE_LIMITED,
                "fake upstream rate limited",
                upstream_status=429,
            ),
            "empty": StandardError(
                StandardErrorKind.EMPTY_RESPONSE,
                "fake upstream returned no images",
            ),
        }
        return kinds.get(kind, kinds["504"])


def register_fake_adapter() -> None:
    """Idempotent — installs the FakeAdapter into the registry."""
    reg = AdapterRegistry.instance()
    if not reg.has("fake"):
        reg.register(FakeAdapter())
