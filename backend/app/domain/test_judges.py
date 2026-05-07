"""Generic judges + helpers for the provider test-suite runner.

Decoupled from any single adapter — ``provider_test_runner`` composes
these into per-case verdicts. Each judge consumes a NormalizedResponse
(or StandardError) plus the original request and returns a list of
``Verdict`` rows (text + boolean) the runner forwards verbatim to the
admin UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image

from app.schemas.normalized import NormalizedImage


# ---------------------------------------------------------------------------
# Verdict rows surfaced to the UI
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """One bullet shown under a case card. ``pass=True`` paints it green."""

    pass_: bool
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"pass": self.pass_, "text": self.text}


# ---------------------------------------------------------------------------
# Image probing
# ---------------------------------------------------------------------------


def probe_image(img: NormalizedImage) -> dict[str, Any]:
    """Decode width/height/mode/alpha-ratio from an image's bytes.

    Tolerates malformed bytes by returning a stub that still has the
    keys the runner expects, but with width/height = 0. Callers should
    treat width=0 as "decode failed".
    """
    info: dict[str, Any] = {
        "width": 0,
        "height": 0,
        "mode": None,
        "aspect_ratio": 0.0,
        "alpha_ratio": 0.0,
        "byte_size": len(img.data) if img.data else 0,
    }
    if not img.data:
        return info
    try:
        with Image.open(BytesIO(img.data)) as im:
            w, h = im.size
            info["width"] = w
            info["height"] = h
            info["mode"] = im.mode
            if w and h:
                info["aspect_ratio"] = round(w / h, 4)
            if im.mode == "RGBA":
                # ``alpha.histogram()`` returns a list of pixel counts
                # indexed by intensity 0..255. Index 0 is the fully
                # transparent count — pulling it directly avoids an
                # O(w*h) Python loop, which matters once images get
                # past 2K (a 4K RGBA mask is 16M pixels).
                alpha = im.split()[-1]
                hist = alpha.histogram()
                transparent = hist[0] if hist else 0
                info["alpha_ratio"] = round(
                    transparent / max(1, w * h), 4
                )
    except Exception:
        pass
    return info


def aspect_close(width: int, height: int, target: str, tol: float = 0.06) -> bool:
    """True iff w/h is within ``tol`` (relative) of the target ratio.

    ``target`` is the slash-form Gemini uses (``"16:9"``, ``"1:4"``).
    """
    if width <= 0 or height <= 0:
        return False
    try:
        tw_str, th_str = target.split(":")
        tw, th = int(tw_str), int(th_str)
    except (ValueError, AttributeError):
        return False
    if tw <= 0 or th <= 0:
        return False
    actual = width / height
    expected = tw / th
    return abs(actual / expected - 1.0) <= tol


# ---------------------------------------------------------------------------
# File-magic check (cheap content-type tamper detection)
# ---------------------------------------------------------------------------


_MAGIC: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


def magic_matches(data: bytes, mime: str) -> bool:
    """True iff ``data`` is a plausible file of type ``mime``.

    WebP needs a two-window check (``RIFF...WEBP``) so it gets its own
    branch. Unknown MIMEs default to True — we only fail on a clear
    mismatch.
    """
    if not data:
        return False
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    sig = _MAGIC.get(mime)
    if sig is None:
        return True
    return data.startswith(sig)


# ---------------------------------------------------------------------------
# Error-kind expectations for the D套件
# ---------------------------------------------------------------------------


# (case_id, adapter_type) → set of acceptable kind values. ``"*"`` matches
# any adapter. Runner consults this when grading a D-case: if the
# StandardError raised by ``adapter.generate`` lands in the expected set
# the case is treated as PASS.
EXPECTED_ERROR: dict[tuple[str, str], set[str]] = {}


def register_expected(adapter_type: str, mapping: dict[str, set[str]]) -> None:
    """Bulk-register expected error kinds for one adapter."""
    for case_id, kinds in mapping.items():
        EXPECTED_ERROR[(case_id, adapter_type)] = set(kinds)


def expected_error_kinds(case_id: str, adapter_type: str) -> set[str]:
    """Return the set of acceptable kind values for ``case_id``.

    Falls back to the wildcard adapter, then to "INVALID_PARAMETER" as
    the safe default.
    """
    if (case_id, adapter_type) in EXPECTED_ERROR:
        return EXPECTED_ERROR[(case_id, adapter_type)]
    if (case_id, "*") in EXPECTED_ERROR:
        return EXPECTED_ERROR[(case_id, "*")]
    return {"INVALID_PARAMETER"}
