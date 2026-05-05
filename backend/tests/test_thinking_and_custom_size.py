"""Unit + integration tests for the gpt-image-2 ``thinking`` field and the
``size_allow_custom`` capability.

The feature has three load-bearing behaviours that all need test coverage:

1. ``validate_custom_size`` enforces OpenAI gpt-image-2 v2's 5-rule
   contract correctly (16-multiple / max edge 3840 / pixel range
   655 360–8 294 400 / aspect ratio ≤ 3:1).
2. The job-validator only accepts a non-preset ``size`` when the merged
   capability surface sets ``size_allow_custom`` true; otherwise it
   returns INVALID_PARAMETER.
3. The OpenAI adapter forwards ``thinking`` on the wire when set,
   rejects unknown values, and never echoes it for non-OpenAI adapters.
"""

from __future__ import annotations

import json

import pytest

from app.adapters.openai_v1 import (
    OpenAIV1Adapter,
    parse_custom_size,
    validate_custom_size,
)
from app.domain.job_validator import validate_against_capabilities
from app.schemas.jobs import JobCreatePayload
from app.schemas.models import ModelCapabilities
from app.schemas.normalized import (
    NormalizedRequest,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)


# ---------------------------------------------------------------------------
# parse_custom_size / validate_custom_size — pure functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expect",
    [
        ("1024x1024", (1024, 1024)),
        ("1280x720", (1280, 720)),
        ("3840x2160", (3840, 2160)),
    ],
)
def test_parse_custom_size_accepts_well_formed(raw, expect):
    assert parse_custom_size(raw) == expect


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bogus",
        "1024X1024",  # uppercase X — strict matcher
        "1024",
        "x1024",
        "1024x",
        "1024xabc",
        "abcx1024",
        "1024 x 1024",
    ],
)
def test_parse_custom_size_rejects_malformed(raw):
    with pytest.raises(ValueError):
        parse_custom_size(raw)


@pytest.mark.parametrize(
    "raw",
    [
        # All five rules satisfied + sanity examples.
        "1024x1024",
        "1280x720",
        "1280x1280",
        "1536x512",   # exactly 3:1
        "1920x1088",  # FHD-ish (1080 isn't 16-multiple, 1088 is)
        "2560x1440",  # 2K boundary
        "2560x1600",  # experimental
        "3840x1280",  # max edge, 3:1
        "3840x2160",  # 4K UHD, exactly at pixel ceiling
        "2880x2880",  # square at pixel ceiling
    ],
)
def test_validate_custom_size_accepts_legal(raw):
    ok, reason = validate_custom_size(raw)
    assert ok, reason


@pytest.mark.parametrize(
    "raw, expected_in_reason",
    [
        ("1000x1000",  "multiples"),    # not 16-multiple
        ("1024x1080",  "multiples"),    # 1080 not 16-multiple
        ("4096x1024",  "longest edge"), # > 3840
        ("1024x4096",  "longest edge"),
        ("512x1024",   "≥"),            # 524 288 < 655 360
        ("3840x2176",  "≤"),            # > 8 294 400
        ("1536x480",   "ratio"),        # 3.2:1 over 3:1 cap
        ("480x1536",   "ratio"),
        ("0x1024",     "positive"),
        ("1024x0",     "positive"),
    ],
)
def test_validate_custom_size_rejects_illegal(raw, expected_in_reason):
    ok, reason = validate_custom_size(raw)
    assert not ok
    assert expected_in_reason in reason, reason


# ---------------------------------------------------------------------------
# OpenAI adapter — _validate enforces the same rules
# ---------------------------------------------------------------------------


def _adapter_request(**overrides):
    """Build a minimal NormalizedRequest with overridable knobs."""
    base = dict(
        model="gpt-image-2",
        prompt="a test banana",
        n=1,
        size="1024x1024",
    )
    base.update(overrides)
    return NormalizedRequest(**base)


def _provider() -> ProviderConfig:
    return ProviderConfig(
        id="t",
        base_url="https://example.test/v1",
        api_key="sk-test",
        adapter_type="openai_v1",
        timeout_seconds=30.0,
    )


def test_adapter_validate_rejects_unknown_thinking():
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        adapter._validate(_adapter_request(thinking="extreme"))
    assert exc.value.kind == StandardErrorKind.INVALID_PARAMETER
    assert exc.value.field == "thinking"


@pytest.mark.parametrize("value", ["off", "low", "medium", "high"])
def test_adapter_validate_accepts_documented_thinking(value):
    adapter = OpenAIV1Adapter()
    # No raise.
    adapter._validate(_adapter_request(thinking=value))


def test_adapter_validate_rejects_illegal_custom_size():
    adapter = OpenAIV1Adapter()
    with pytest.raises(StandardError) as exc:
        adapter._validate(_adapter_request(size="1000x1000"))
    assert exc.value.kind == StandardErrorKind.INVALID_PARAMETER
    assert exc.value.field == "size"
    assert "multiples" in exc.value.message


def test_adapter_validate_accepts_legal_custom_size():
    adapter = OpenAIV1Adapter()
    # Custom size that obeys all 5 rules.
    adapter._validate(_adapter_request(size="1280x720"))


def test_adapter_body_forwards_thinking():
    """Wire format must carry ``thinking`` when set."""
    adapter = OpenAIV1Adapter()
    body = adapter._build_generations_body(_adapter_request(thinking="medium"))
    assert body.get("thinking") == "medium"


def test_adapter_body_omits_thinking_when_unset():
    adapter = OpenAIV1Adapter()
    body = adapter._build_generations_body(_adapter_request())
    assert "thinking" not in body


# ---------------------------------------------------------------------------
# Capability + UI schemas surface the new fields
# ---------------------------------------------------------------------------


def test_capability_schema_advertises_thinking_and_size_allow_custom():
    adapter = OpenAIV1Adapter()
    caps = adapter.capability_schema()
    keys = {f.k for f in caps}
    assert "thinking" in keys
    assert "size_allow_custom" in keys
    thinking_field = next(f for f in caps if f.k == "thinking")
    assert thinking_field.kind == "list"
    assert set(thinking_field.options) == {"off", "low", "medium", "high"}
    custom_field = next(f for f in caps if f.k == "size_allow_custom")
    assert custom_field.kind == "bool"


def test_ui_schema_advertises_thinking():
    adapter = OpenAIV1Adapter()
    ui = adapter.ui_schema("gpt-image-2")
    by_k = {f.k: f for f in ui}
    assert "thinking" in by_k
    field = by_k["thinking"]
    assert field.control == "chip-row"
    assert field.options == ["off", "low", "medium", "high"]


# ---------------------------------------------------------------------------
# job_validator — size_allow_custom escape hatch
# ---------------------------------------------------------------------------


def _payload(**overrides) -> JobCreatePayload:
    base = dict(
        model="gpt-image-2",
        prompt="a tall ship",
        n=1,
    )
    base.update(overrides)
    return JobCreatePayload(**base)


def _caps(**kw) -> ModelCapabilities:
    """Default to a typical openai_v1 capability surface."""
    base = dict(
        size=["1024x1024", "1024x1536", "1536x1024", "auto"],
    )
    base.update(kw)
    return ModelCapabilities(**base)


def test_validator_accepts_size_in_preset_list():
    f = validate_against_capabilities(
        _payload(size="1024x1024"),
        _caps(),
        reference_count=0,
    )
    assert f is None


def test_validator_rejects_custom_size_when_not_opted_in():
    f = validate_against_capabilities(
        _payload(size="1280x720"),
        _caps(),  # size_allow_custom defaults to None / False
        reference_count=0,
    )
    assert f is not None
    assert f.field == "size"
    assert "1280x720" in f.message
    # Should mention the allowed list, not the custom-rule reason.
    assert "allowed set" in f.message


def test_validator_accepts_custom_size_when_opted_in_and_legal():
    f = validate_against_capabilities(
        _payload(size="1280x720"),
        _caps(size_allow_custom=True),
        reference_count=0,
    )
    assert f is None


def test_validator_rejects_custom_size_when_opted_in_but_illegal_16_multiple():
    f = validate_against_capabilities(
        _payload(size="1000x1000"),
        _caps(size_allow_custom=True),
        reference_count=0,
    )
    assert f is not None
    assert f.field == "size"
    assert "multiples" in f.message


def test_validator_rejects_custom_size_above_max_edge():
    f = validate_against_capabilities(
        _payload(size="4096x1024"),
        _caps(size_allow_custom=True),
        reference_count=0,
    )
    assert f is not None
    assert f.field == "size"
    assert "longest edge" in f.message


def test_validator_rejects_custom_size_above_3_to_1_ratio():
    f = validate_against_capabilities(
        _payload(size="1536x480"),
        _caps(size_allow_custom=True),
        reference_count=0,
    )
    assert f is not None
    assert f.field == "size"
    assert "ratio" in f.message


def test_validator_rejects_custom_size_below_pixel_minimum():
    f = validate_against_capabilities(
        _payload(size="512x1024"),
        _caps(size_allow_custom=True),
        reference_count=0,
    )
    assert f is not None
    assert f.field == "size"
    assert "≥" in f.message


# ---------------------------------------------------------------------------
# job_validator — thinking
# ---------------------------------------------------------------------------


def test_validator_accepts_thinking_when_in_caps():
    f = validate_against_capabilities(
        _payload(thinking="high"),
        _caps(thinking=["off", "low", "medium", "high"]),
        reference_count=0,
    )
    assert f is None


def test_validator_rejects_thinking_when_caps_none():
    f = validate_against_capabilities(
        _payload(thinking="high"),
        _caps(),  # caps.thinking is None
        reference_count=0,
    )
    assert f is not None
    assert f.field == "thinking"


def test_validator_rejects_thinking_when_value_not_in_caps():
    f = validate_against_capabilities(
        _payload(thinking="high"),
        _caps(thinking=["off", "low"]),  # admin disabled medium/high
        reference_count=0,
    )
    assert f is not None
    assert f.field == "thinking"
