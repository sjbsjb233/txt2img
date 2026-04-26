"""gpt-image-2 probes against https://api.bltcy.ai (OpenAI-compatible).

Tests:
  1. Baseline 1024x1024 low quality (tier-2 / "low quality 1K only" claim).
  2. Portrait 1024x1536 low quality (does the relay honor non-square?).
  3. Landscape 1536x1024 high quality (sanity check on standard sizes).
  4. 2K probe -- non-standard for OpenAI, asks the relay to upscale via size.
  5. 4K probe -- as above.
  6. images.edits round-trip (only run if budget remains).

NOTE: the user pasted the SAME key value for the "low-quality 1K" and the
"high-quality 1K/2K/4K" entries. We treat them as ONE physical key with a
single 5-success budget. If they are actually different keys, just rerun the
script with the second key string.
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

KEY = os.environ.get("BLTCY_GPT_IMAGE_KEY", "").strip()
LABEL = os.environ.get("BLTCY_GPT_IMAGE_LABEL", "gpt_image_2_key")

if not KEY:
    sys.exit("error: set BLTCY_GPT_IMAGE_KEY env var to the relay key for gpt-image-2 access.")

MODEL = "gpt-image-2"
MODEL_FALLBACKS = ["gpt-image-1.5", "gpt-image-1"]

PROMPT_BASE = "A photorealistic still life: a glass of orange juice next to a sliced kiwi on a marble counter, soft morning light."


def _expected(size: str) -> dict[str, int]:
    if size == "auto":
        return {}
    try:
        w, h = (int(x) for x in size.lower().split("x"))
        return {"width": w, "height": h}
    except Exception:  # noqa: BLE001
        return {}


def _model_for_attempt(attempt_idx: int) -> str:
    if attempt_idx < len(MODEL_FALLBACKS) + 1:
        return [MODEL, *MODEL_FALLBACKS][attempt_idx]
    return MODEL


def main() -> None:
    budget = Budget(label=LABEL, max_successes=5)

    # ---- 1. Baseline: 1024x1024, low quality. Establish that gpt-image-2 works.
    # If gpt-image-2 returns 404/unsupported, fall back through gpt-image-1.5,
    # gpt-image-1 -- failures don't burn the success budget.
    chosen_model: str | None = None
    for candidate in [MODEL, *MODEL_FALLBACKS]:
        if not budget.can_spend():
            break
        entry = run_probe(
            budget=budget,
            test_name=f"baseline_1024_low/{candidate}",
            request_summary={
                "endpoint": "/v1/images/generations",
                "model": candidate,
                "size": "1024x1024",
                "quality": "low",
            },
            call=lambda m=candidate: openai_generate(
                api_key=KEY,
                model=m,
                prompt=PROMPT_BASE,
                size="1024x1024",
                quality="low",
            ),
            extract=extract_openai_image,
            expect=_expected("1024x1024"),
        )
        if entry.get("success"):
            chosen_model = candidate
            break

    if chosen_model is None:
        print("[gpt-image-2] no compatible model found via any candidate; aborting.")
        write_report("report_gpt_image_2.json", [budget])
        return

    # ---- 2. Portrait at low quality: tests if non-square 1K is supported on the "low" tier.
    if budget.can_spend():
        run_probe(
            budget=budget,
            test_name=f"portrait_1024x1536_low/{chosen_model}",
            request_summary={"model": chosen_model, "size": "1024x1536", "quality": "low"},
            call=lambda: openai_generate(
                api_key=KEY,
                model=chosen_model,
                prompt=PROMPT_BASE + " Vertical composition, magazine cover style.",
                size="1024x1536",
                quality="low",
            ),
            extract=extract_openai_image,
            expect=_expected("1024x1536"),
        )

    # ---- 3. 2K probe (non-standard OpenAI): validates the high-tier claim.
    if budget.can_spend():
        run_probe(
            budget=budget,
            test_name=f"twoK_2048x2048_high/{chosen_model}",
            request_summary={"model": chosen_model, "size": "2048x2048", "quality": "high"},
            call=lambda: openai_generate(
                api_key=KEY,
                model=chosen_model,
                prompt=PROMPT_BASE,
                size="2048x2048",
                quality="high",
            ),
            extract=extract_openai_image,
            expect=_expected("2048x2048"),
        )

    # ---- 4. 4K probe.
    if budget.can_spend():
        run_probe(
            budget=budget,
            test_name=f"fourK_4096x4096_high/{chosen_model}",
            request_summary={"model": chosen_model, "size": "4096x4096", "quality": "high"},
            call=lambda: openai_generate(
                api_key=KEY,
                model=chosen_model,
                prompt=PROMPT_BASE,
                size="4096x4096",
                quality="high",
            ),
            extract=extract_openai_image,
            expect=_expected("4096x4096"),
        )

    # ---- 5. images.edits: multipart upload of input image with edit prompt.
    if budget.can_spend():
        in_path = Path(__file__).resolve().parent / "inputs" / "test_square_1024.png"
        run_probe(
            budget=budget,
            test_name=f"edit_1024_low/{chosen_model}",
            request_summary={
                "endpoint": "/v1/images/edits",
                "model": chosen_model,
                "size": "1024x1024",
                "quality": "low",
                "image": str(in_path.name),
                "edit_prompt": "Replace the yellow sun with a full moon at night.",
            },
            call=lambda: openai_edit_multipart(
                api_key=KEY,
                model=chosen_model,
                prompt="Replace the yellow sun with a full moon at night, keep the mountain shape.",
                image_path=in_path,
                size="1024x1024",
                quality="low",
            ),
            extract=extract_openai_image,
            expect=_expected("1024x1024"),
        )

    path = write_report("report_gpt_image_2.json", [budget])
    print(f"\n[gpt-image-2] wrote {path}")
    print(f"[gpt-image-2] successes: {budget.successes}/{budget.max_successes}, attempts: {budget.attempts}")


if __name__ == "__main__":
    main()
