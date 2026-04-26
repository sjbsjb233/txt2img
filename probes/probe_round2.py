"""Round 2 probes:

- Nano Banana key (10 successes) -- exercise 4K, extreme aspect ratios, and
  edits across all three models (skip discovery, we already know all three work).
- gpt-image-2 key1 (5 successes) and key2 (5 successes) -- run the *same*
  parameter set on both so we can compare what "low quality 1K" vs
  "high quality 1K/2K/4K" actually means at the relay level.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from probe_lib import (
    Budget,
    extract_gemini_image,
    extract_openai_image,
    gemini_generate_content,
    openai_edit_multipart,
    openai_generate,
    run_probe,
    write_report,
)

NANO_KEY = os.environ.get("BLTCY_NANOBANANA_KEY", "").strip()
GPT_KEY1 = os.environ.get("BLTCY_GPT_IMAGE_KEY1", "").strip()
GPT_KEY2 = os.environ.get("BLTCY_GPT_IMAGE_KEY2", "").strip()

if not (NANO_KEY and GPT_KEY1 and GPT_KEY2):
    sys.exit(
        "error: set BLTCY_NANOBANANA_KEY, BLTCY_GPT_IMAGE_KEY1, BLTCY_GPT_IMAGE_KEY2"
    )

INPUT_PNG = Path(__file__).resolve().parent / "inputs" / "test_square_1024.png"
INPUT_BYTES = INPUT_PNG.read_bytes()

PROMPT_GEN = "A cinematic still: an astronaut standing in a field of glowing blue flowers under twin moons, dramatic lighting."
EDIT_PROMPT = "Replace the yellow sun with a glowing full moon at night and tint the sky deep navy; keep the mountain shape and ground intact."
GPT_PROMPT = "A photorealistic still life: a glass of orange juice next to a sliced kiwi on a marble counter, soft morning light."


def _expected_aspect(ar: str, image_size: str) -> dict[str, int]:
    long_edge = {"512": 512, "1K": 1024, "2K": 2048, "4K": 4096}.get(image_size, 1024)
    a, b = (int(x) for x in ar.split(":"))
    if a >= b:
        w, h = long_edge, round(long_edge * b / a)
    else:
        h, w = long_edge, round(long_edge * a / b)
    return {"width": w, "height": h, "min_long_edge": int(long_edge * 0.85)}


def _expected_size(size: str) -> dict[str, int]:
    if size == "auto":
        return {}
    try:
        w, h = (int(x) for x in size.lower().split("x"))
        return {"width": w, "height": h}
    except Exception:  # noqa: BLE001
        return {}


# ---------------- Nano Banana plan (10 successes) ----------------

def run_nano() -> Budget:
    budget = Budget(label="nano_round2", max_successes=10)

    plan: list[tuple[str, str, str | None, str, list[tuple[str, bytes]] | None]] = [
        # (model, aspect_ratio, image_size, prompt, input_images)
        ("gemini-3-pro-image-preview", "16:9", "4K", PROMPT_GEN, None),
        ("gemini-3-pro-image-preview", "1:1",  "4K", PROMPT_GEN, None),
        ("gemini-3-pro-image-preview", "21:9", "2K", PROMPT_GEN, None),
        ("gemini-3-pro-image-preview", "4:5",  "2K", PROMPT_GEN, None),
        ("gemini-3.1-flash-image-preview", "16:9", "2K", PROMPT_GEN, None),
        ("gemini-3.1-flash-image-preview", "9:16", "4K", PROMPT_GEN, None),
        ("gemini-2.5-flash-image", "16:9", "1K", PROMPT_GEN, None),
        # edits across all three models
        ("gemini-3-pro-image-preview",     "1:1", "1K", EDIT_PROMPT, [("image/png", INPUT_BYTES)]),
        ("gemini-3.1-flash-image-preview", "1:1", "1K", EDIT_PROMPT, [("image/png", INPUT_BYTES)]),
        ("gemini-2.5-flash-image",         "1:1", "1K", EDIT_PROMPT, [("image/png", INPUT_BYTES)]),
    ]

    for model, ar, sz, prompt, inputs in plan:
        if not budget.can_spend():
            break
        kind = "edit" if inputs else "gen"
        run_probe(
            budget=budget,
            test_name=f"{kind}/{model}/{ar}/{sz}",
            request_summary={
                "endpoint": "/v1beta/models/.../generateContent",
                "model": model,
                "aspect_ratio": ar,
                "image_size": sz,
                "input_images": len(inputs) if inputs else 0,
                "prompt": prompt[:80],
            },
            call=lambda m=model, a=ar, s=sz, p=prompt, i=inputs: gemini_generate_content(
                api_key=NANO_KEY,
                model=m,
                prompt=p,
                aspect_ratio=a,
                image_size=s,
                input_images=i,
            ),
            extract=extract_gemini_image,
            expect=_expected_aspect(ar, sz or "1K"),
        )
    return budget


# ---------------- gpt-image-2 comparison plan (5 successes per key) ----------------

GPT_TESTS = [
    # (size, quality, label)
    ("1024x1024", "high", "1K_high"),
    ("2048x2048", "high", "2K_high"),
    ("4096x4096", "high", "4K_high"),
    ("1024x1536", "high", "portrait_high"),
    # edits
    ("EDIT_1024x1024", "high", "edit_1K_high"),
]


def run_gpt(key: str, label: str) -> Budget:
    budget = Budget(label=label, max_successes=5)
    model = "gpt-image-2"

    for size, quality, name in GPT_TESTS:
        if not budget.can_spend():
            break
        if size.startswith("EDIT_"):
            real_size = size.removeprefix("EDIT_")
            run_probe(
                budget=budget,
                test_name=f"{name}/{model}",
                request_summary={
                    "endpoint": "/v1/images/edits",
                    "model": model,
                    "size": real_size,
                    "quality": quality,
                    "image": INPUT_PNG.name,
                    "edit_prompt": EDIT_PROMPT[:80],
                },
                call=lambda s=real_size, q=quality: openai_edit_multipart(
                    api_key=key,
                    model=model,
                    prompt=EDIT_PROMPT,
                    image_path=INPUT_PNG,
                    size=s,
                    quality=q,
                ),
                extract=extract_openai_image,
                expect=_expected_size(real_size),
            )
        else:
            run_probe(
                budget=budget,
                test_name=f"{name}/{model}",
                request_summary={
                    "endpoint": "/v1/images/generations",
                    "model": model,
                    "size": size,
                    "quality": quality,
                },
                call=lambda s=size, q=quality: openai_generate(
                    api_key=key,
                    model=model,
                    prompt=GPT_PROMPT,
                    size=s,
                    quality=q,
                ),
                extract=extract_openai_image,
                expect=_expected_size(size),
            )
    return budget


def main() -> None:
    nano_budget = run_nano()
    gpt1_budget = run_gpt(GPT_KEY1, "gpt_key1_low_tier")
    gpt2_budget = run_gpt(GPT_KEY2, "gpt_key2_high_tier")

    path = write_report("report_round2.json", [nano_budget, gpt1_budget, gpt2_budget])
    print(f"\n[round2] wrote {path}")
    for b in (nano_budget, gpt1_budget, gpt2_budget):
        print(f"  {b.label}: successes {b.successes}/{b.max_successes}, attempts {b.attempts}")


if __name__ == "__main__":
    main()
