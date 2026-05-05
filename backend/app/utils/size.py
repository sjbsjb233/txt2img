"""Pure size-string helpers for OpenAI gpt-image-2 v2 custom resolutions.

This module is import-time pure: no httpx, no DB, no I/O. Both the
adapter (``app.adapters.openai_v1``) and the domain validator
(``app.domain.job_validator``) consume :func:`validate_custom_size`
to enforce the 5-rule custom-size contract before a request hits the
wire. Keeping the helpers here avoids the validator pulling adapter
dependencies (httpx, the registry…) just to read a couple of integer
constants — a layering issue Copilot flagged on PR #78.

Contract (OpenAI gpt-image-2 v2):

    1. Both width and height are multiples of 16.
    2. Longest edge ≤ 3840 px.
    3. Total pixels in [655 360, 8 294 400].
    4. Aspect ratio (max/min) ≤ 3:1.

The ">2K is experimental" note in the docs is *not* an error here —
those sizes are accepted at the wire level. Admins can still cap them
via ``capabilities.size`` if they want to gate access.
"""

from __future__ import annotations


# ``WIDTHxHEIGHT`` constants. Lowercase ``x`` only.
CUSTOM_SIZE_MIN_PIXELS = 655_360
CUSTOM_SIZE_MAX_PIXELS = 8_294_400
CUSTOM_SIZE_MAX_EDGE = 3840
CUSTOM_SIZE_MAX_RATIO = 3.0
CUSTOM_SIZE_MULTIPLE = 16


def parse_custom_size(raw: str) -> tuple[int, int]:
    """Decode ``"WIDTHxHEIGHT"`` to ``(w, h)``. Raises ``ValueError`` on
    malformed input. Both digits required, lower-case ``x`` only.
    """
    if not isinstance(raw, str) or "x" not in raw:
        raise ValueError("size must look like 'WIDTHxHEIGHT'")
    left, _, right = raw.partition("x")
    if not left.isdigit() or not right.isdigit():
        raise ValueError("size must look like 'WIDTHxHEIGHT'")
    return int(left), int(right)


def validate_custom_size(raw: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a custom (non-preset) size string.

    See module docstring for the contract.
    """
    try:
        w, h = parse_custom_size(raw)
    except ValueError as exc:
        return False, str(exc)
    if w <= 0 or h <= 0:
        return False, "size dimensions must be positive"
    if w % CUSTOM_SIZE_MULTIPLE or h % CUSTOM_SIZE_MULTIPLE:
        return False, (
            f"width and height must both be multiples of "
            f"{CUSTOM_SIZE_MULTIPLE}"
        )
    if max(w, h) > CUSTOM_SIZE_MAX_EDGE:
        return False, f"longest edge must be ≤ {CUSTOM_SIZE_MAX_EDGE}px"
    pixels = w * h
    if pixels < CUSTOM_SIZE_MIN_PIXELS:
        return False, f"total pixels must be ≥ {CUSTOM_SIZE_MIN_PIXELS}"
    if pixels > CUSTOM_SIZE_MAX_PIXELS:
        return False, f"total pixels must be ≤ {CUSTOM_SIZE_MAX_PIXELS}"
    ratio = max(w, h) / min(w, h)
    if ratio > CUSTOM_SIZE_MAX_RATIO:
        return False, (
            f"aspect ratio {ratio:.2f}:1 exceeds "
            f"{CUSTOM_SIZE_MAX_RATIO:.0f}:1 cap"
        )
    return True, ""


__all__ = (
    "CUSTOM_SIZE_MAX_EDGE",
    "CUSTOM_SIZE_MAX_PIXELS",
    "CUSTOM_SIZE_MAX_RATIO",
    "CUSTOM_SIZE_MIN_PIXELS",
    "CUSTOM_SIZE_MULTIPLE",
    "parse_custom_size",
    "validate_custom_size",
)
