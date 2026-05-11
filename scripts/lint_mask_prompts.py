#!/usr/bin/env python3
"""Lint M1..M8 prompts for forbidden mask-leaking words.

The mask plan v2 forbids the prompt from naming:

* a position (left / right / top / bottom / side / above / below)
* a specific species the mask is supposed to test (villager / golem /
  human / mob — but NOT the replacement species like creeper / skeleton,
  those are intentional)
* a magnitude / distance (any "<digits>px" token, "one side", etc.)

If any of these leak in, the prompt would let the model pass the test
on prompt knowledge alone — defeating the whole point of shipping mask
fixtures.

CI runs this; exit 1 on any hit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


FORBIDDEN_WORDS: tuple[str, ...] = (
    "right",
    "left",
    "top",
    "bottom",
    "side",
    "above",
    "below",
    "villager",
    "golem",
    "human",
    "mob",
)


def _check(prompt: str) -> list[str]:
    hits: list[str] = []
    for w in FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", prompt, flags=re.IGNORECASE):
            hits.append(w)
    if re.search(r"\b\d+\s*px\b", prompt, flags=re.IGNORECASE):
        hits.append("<digits>px")
    if re.search(r"\bone\s+side\b", prompt, flags=re.IGNORECASE):
        hits.append("one side")
    return hits


def main() -> int:
    import os

    os.environ.setdefault("JWT_SECRET", "x")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
    os.environ.setdefault("DATA_ROOT", "./_data")

    from app.domain.test_cases import OPENAI_GPT_IMAGE_2_CASES

    cases = [c for c in OPENAI_GPT_IMAGE_2_CASES if c.case_id.startswith("M")]
    if len(cases) != 8:
        print(f"FAIL: expected 8 mask cases (M1..M8), found {len(cases)}", file=sys.stderr)
        return 1

    failures: list[str] = []
    for case in cases:
        req = case.request_factory()
        hits = _check(req.prompt)
        if hits:
            failures.append(f"{case.case_id}: forbidden words: {hits!r}")
            failures.append(f"    prompt: {req.prompt[:200]}")
        else:
            print(f"  OK  {case.case_id}: prompt clean ({len(req.prompt)} chars)")

    if failures:
        print("\nFAIL — forbidden words leaked into mask prompts:", file=sys.stderr)
        for line in failures:
            print(line, file=sys.stderr)
        return 1
    print(f"\nOK — all {len(cases)} mask prompts are clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
