"""Nanobanana / Gemini image probes against https://api.bltcy.ai .

Uses the *native Google* request shape (POST /v1beta/models/{model}:generateContent
with `contents`/`generationConfig.imageConfig`), since the user asked for the
official format. The relay is queried via Bearer auth (the keys are sk-* shaped).

Strategy
--------
- 5-success budget for the single nanobanana key.
- First do a cheap probe per model to find which models the key actually has
  access to. Failed access does NOT consume budget.
- Then exercise aspectRatio + imageSize variations on whichever models work.
- Final test exercises image *editing* (multi-modal input -> image output).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from probe_lib import (
    Budget,
    extract_gemini_image,
    gemini_generate_content,
    run_probe,
    write_report,
)

KEY = os.environ.get("BLTCY_NANOBANANA_KEY", "").strip()
LABEL = "nanobanana_key1"

if not KEY:
    sys.exit("error: set BLTCY_NANOBANANA_KEY env var to the relay key for nano-banana access.")

# Candidate Gemini image models. The user mentioned "two models" for this key
# but the chat truncated; we probe the well-known trio.
CANDIDATE_MODELS = [
    "gemini-2.5-flash-image",         # Nano Banana
    "gemini-3-pro-image-preview",      # Nano Banana Pro
    "gemini-3.1-flash-image-preview",  # Nano Banana 2
]


def _expected_for_aspect(ar: str, image_size: str | None) -> dict[str, int]:
    """Best-guess expected pixel dims for an (aspectRatio, imageSize) combo.

    Per Google's Nano Banana docs the long edge for "1K" ~ 1024px, "2K" ~ 2048,
    "4K" ~ 4096; aspect ratio sets the ratio of the two edges.
    """
    long_edge = {"512": 512, "1K": 1024, "2K": 2048, "4K": 4096}.get(image_size or "1K", 1024)
    a, b = (int(x) for x in ar.split(":"))
    if a >= b:
        w = long_edge
        h = round(long_edge * b / a)
    else:
        h = long_edge
        w = round(long_edge * a / b)
    return {"width": w, "height": h, "min_long_edge": int(long_edge * 0.9)}


def main() -> None:
    budget = Budget(label=LABEL, max_successes=5)

    # --- Phase 1: discover which models the key can access (cheap probe) ---
    accessible: list[str] = []
    for model in CANDIDATE_MODELS:
        if not budget.can_spend():
            break
        entry = run_probe(
            budget=budget,
            test_name=f"discover/{model}",
            request_summary={
                "endpoint": "/v1beta/models/.../generateContent",
                "model": model,
                "aspect_ratio": "1:1",
                "image_size": "1K",
                "input_images": 0,
            },
            call=lambda m=model: gemini_generate_content(
                api_key=KEY,
                model=m,
                prompt="A small red apple on a white background, studio lighting.",
                aspect_ratio="1:1",
                image_size="1K",
            ),
            extract=extract_gemini_image,
            expect=_expected_for_aspect("1:1", "1K"),
        )
        if entry.get("success"):
            accessible.append(model)

    if not accessible:
        print("[nanobanana] no candidate model returned an image; aborting further tests.")
        write_report("report_nanobanana.json", [budget])
        return

    primary = accessible[0]
    secondary = accessible[1] if len(accessible) > 1 else accessible[0]

    # --- Phase 2: vary aspect ratio + imageSize on accessible model(s) ---
    plan = [
        (primary,   "16:9", "2K",  "16:9 widescreen mountain landscape at golden hour"),
        (secondary, "9:16", "2K",  "9:16 portrait of a fluffy white cat sitting on books"),
        (primary,   "1:1",  "4K",  "1:1 ultra-detailed close-up of a dewdrop on a leaf"),
    ]
    for (model, ar, sz, prompt) in plan:
        if not budget.can_spend():
            break
        run_probe(
            budget=budget,
            test_name=f"vary/{model}/{ar}/{sz}",
            request_summary={
                "model": model,
                "aspect_ratio": ar,
                "image_size": sz,
                "input_images": 0,
            },
            call=lambda m=model, a=ar, s=sz, p=prompt: gemini_generate_content(
                api_key=KEY,
                model=m,
                prompt=p,
                aspect_ratio=a,
                image_size=s,
            ),
            extract=extract_gemini_image,
            expect=_expected_for_aspect(ar, sz),
        )

    # --- Phase 3: edit / multi-modal input (image + text -> image) ---
    if budget.can_spend():
        in_path = Path(__file__).resolve().parent / "inputs" / "test_square_1024.png"
        raw = in_path.read_bytes()
        run_probe(
            budget=budget,
            test_name=f"edit/{primary}/1:1/1K",
            request_summary={
                "model": primary,
                "aspect_ratio": "1:1",
                "image_size": "1K",
                "input_images": 1,
                "input_path": str(in_path.name),
                "edit_prompt": "Replace the yellow sun with a glowing full moon at night, keep mountain shape.",
            },
            call=lambda: gemini_generate_content(
                api_key=KEY,
                model=primary,
                prompt="Replace the yellow sun with a glowing full moon at night, keep the mountain shape.",
                aspect_ratio="1:1",
                image_size="1K",
                input_images=[("image/png", raw)],
            ),
            extract=extract_gemini_image,
            expect=_expected_for_aspect("1:1", "1K"),
        )

    path = write_report("report_nanobanana.json", [budget])
    print(f"\n[nanobanana] wrote {path}")
    print(f"[nanobanana] successes: {budget.successes}/{budget.max_successes}, attempts: {budget.attempts}")


if __name__ == "__main__":
    main()
