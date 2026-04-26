"""Retry probes against gpt key2 (high-tier) with extended client timeouts and
sizes within the upstream's 3840-pixel long-edge limit (revealed by the 400
error in the prior round).

Budget: 5 successful image deliveries on key2; failures do not count.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from probe_lib import (
    Budget,
    extract_openai_image,
    openai_edit_multipart,
    openai_generate,
    run_probe,
    write_report,
)

KEY2 = os.environ.get("BLTCY_GPT_IMAGE_KEY2", "").strip()
if not KEY2:
    sys.exit("set BLTCY_GPT_IMAGE_KEY2")

LABEL = "gpt_key2_high_tier_retry"
MODEL = "gpt-image-2"
INPUT_PNG = Path(__file__).resolve().parent / "inputs" / "test_square_1024.png"
PROMPT = "A photorealistic still life: a glass of orange juice next to a sliced kiwi on a marble counter, soft morning light."
EDIT_PROMPT = "Replace the yellow sun with a glowing full moon at night and tint the sky deep navy; keep mountain shape."

TIMEOUT_GEN = 600
TIMEOUT_EDIT = 600


def _expected(size: str) -> dict[str, int]:
    if size == "auto":
        return {}
    try:
        w, h = (int(x) for x in size.lower().split("x"))
        return {"width": w, "height": h}
    except Exception:  # noqa: BLE001
        return {}


def main() -> None:
    budget = Budget(label=LABEL, max_successes=5)

    # Long edge must be <= 3840 per the upstream's 400 error from round 2.
    plan = [
        ("1024x1024", "high"),   # 1K square baseline
        ("2048x2048", "high"),   # 2K square
        ("3840x2160", "high"),   # ~4K landscape (16:9), inside 3840 cap
        ("1024x1536", "high"),   # portrait, validate non-square
    ]

    for size, quality in plan:
        if not budget.can_spend():
            break
        run_probe(
            budget=budget,
            test_name=f"gen_{size}_{quality}/{MODEL}",
            request_summary={
                "endpoint": "/v1/images/generations",
                "model": MODEL,
                "size": size,
                "quality": quality,
                "client_timeout_s": TIMEOUT_GEN,
            },
            call=lambda s=size, q=quality: openai_generate(
                api_key=KEY2,
                model=MODEL,
                prompt=PROMPT,
                size=s,
                quality=q,
                timeout=TIMEOUT_GEN,
            ),
            extract=extract_openai_image,
            expect=_expected(size),
        )

    # images.edits round-trip
    if budget.can_spend():
        run_probe(
            budget=budget,
            test_name=f"edit_1024x1024_high/{MODEL}",
            request_summary={
                "endpoint": "/v1/images/edits",
                "model": MODEL,
                "size": "1024x1024",
                "quality": "high",
                "image": INPUT_PNG.name,
                "client_timeout_s": TIMEOUT_EDIT,
            },
            call=lambda: openai_edit_multipart(
                api_key=KEY2,
                model=MODEL,
                prompt=EDIT_PROMPT,
                image_path=INPUT_PNG,
                size="1024x1024",
                quality="high",
                timeout=TIMEOUT_EDIT,
            ),
            extract=extract_openai_image,
            expect=_expected("1024x1024"),
        )

    path = write_report("report_round2_key2_retry.json", [budget])
    print(f"\n[round2-retry] wrote {path}")
    print(f"  {budget.label}: successes {budget.successes}/{budget.max_successes}, attempts {budget.attempts}")


if __name__ == "__main__":
    main()
