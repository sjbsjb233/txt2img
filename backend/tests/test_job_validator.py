"""Unit tests for the parameter validator (PR-13).

The validator is exercised end-to-end in ``test_jobs_api.py`` but the
matrix of capability shapes vs. payload combinations is large enough to
benefit from focused, dependency-free coverage here.
"""

from __future__ import annotations

import pytest

from app.domain.job_validator import validate_against_capabilities
from app.schemas.jobs import JobCreatePayload
from app.schemas.models import ModelCapabilities


def _payload(**kwargs) -> JobCreatePayload:
    base = {
        "model": "gpt-image-2",
        "prompt": "a still life",
        "n": 1,
    }
    base.update(kwargs)
    return JobCreatePayload.model_validate(base)


def test_passes_when_all_fields_unset() -> None:
    caps = ModelCapabilities(n_max=10)
    assert (
        validate_against_capabilities(_payload(), caps, reference_count=0)
        is None
    )


def test_rejects_n_above_cap() -> None:
    caps = ModelCapabilities(n_max=4)
    fail = validate_against_capabilities(
        _payload(n=8), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "n"


def test_rejects_value_not_in_list() -> None:
    caps = ModelCapabilities(size=["1024x1024"])
    fail = validate_against_capabilities(
        _payload(size="2048x2048"), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "size"


def test_rejects_field_when_capability_missing() -> None:
    """Setting a parameter the capability doesn't expose is a hard fail."""
    caps = ModelCapabilities()
    fail = validate_against_capabilities(
        _payload(thinking_level="high"), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "thinking_level"


def test_partial_images_requires_stream_true() -> None:
    caps = ModelCapabilities(partial_images_max=3, stream=True)
    fail = validate_against_capabilities(
        _payload(partial_images=2, stream=False),
        caps,
        reference_count=0,
    )
    assert fail is not None
    assert fail.field == "partial_images"


def test_boolean_opt_in_requires_capability_true() -> None:
    caps = ModelCapabilities()
    fail = validate_against_capabilities(
        _payload(google_search=True), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "google_search"


def test_max_reference_images_enforced() -> None:
    caps = ModelCapabilities(max_reference_images=3)
    fail = validate_against_capabilities(
        _payload(), caps, reference_count=10
    )
    assert fail is not None
    assert fail.field == "references"


def test_prompt_length_enforced() -> None:
    caps = ModelCapabilities(max_prompt_chars=5)
    fail = validate_against_capabilities(
        _payload(prompt="abcdef"), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "prompt"


def test_output_compression_requires_jpeg_or_webp() -> None:
    caps = ModelCapabilities(output_format=["png", "jpeg", "webp"])
    fail = validate_against_capabilities(
        _payload(output_compression=70, output_format="png"),
        caps,
        reference_count=0,
    )
    assert fail is not None
    assert fail.field == "output_compression"


def test_transparent_background_requires_capability_true() -> None:
    caps = ModelCapabilities(
        background=["auto", "opaque", "transparent"],
        supports_transparent_bg=False,
    )
    fail = validate_against_capabilities(
        _payload(background="transparent"),
        caps,
        reference_count=0,
    )
    assert fail is not None
    assert fail.field == "background"


def test_pydantic_n_le_64_rejects_huge_value() -> None:
    """Sanity: the schema's own bounds reject obviously absurd values."""
    with pytest.raises(Exception):
        JobCreatePayload.model_validate(
            {"model": "x", "prompt": "x", "n": 1_000_000}
        )


# ---------------------------------------------------------------------------
# n_max_upstream — Create-page fan-out fallback (see plan §4.1.2).
#
# When ``n_max_upstream`` is set, it overrides ``n_max`` for the per-call
# check: a Gemini-style model exposes ``n_max=4`` to the slider but only
# ``n_max_upstream=1`` to the upstream call, so a job with ``n=4`` must
# still be rejected (the frontend should have fanned out into n=1 calls).
# ---------------------------------------------------------------------------


def test_n_max_upstream_overrides_n_max_when_both_set() -> None:
    caps = ModelCapabilities(n_max=4, n_max_upstream=1)
    # Slider would let user pick 4, but a single POST with n=4 must fail
    # because the upstream call can only return 1.
    fail = validate_against_capabilities(
        _payload(n=4), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "n"
    assert "1" in fail.message


def test_n_max_upstream_allows_per_call_value() -> None:
    caps = ModelCapabilities(n_max=4, n_max_upstream=1)
    # The fan-out helper on the frontend issues n=1 sub-requests — those
    # must clear validation.
    assert (
        validate_against_capabilities(_payload(n=1), caps, reference_count=0)
        is None
    )


def test_n_max_upstream_none_falls_back_to_n_max() -> None:
    """Older capability rows without ``n_max_upstream`` keep old behaviour."""
    caps = ModelCapabilities(n_max=4)  # n_max_upstream is None
    # n=4 should still pass; n=5 should fail with n_max=4.
    assert (
        validate_against_capabilities(_payload(n=4), caps, reference_count=0)
        is None
    )
    fail = validate_against_capabilities(
        _payload(n=5), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "n"


def test_n_max_upstream_alone_caps_request() -> None:
    """``n_max_upstream`` alone (no ``n_max``) still caps the per-call n."""
    caps = ModelCapabilities(n_max_upstream=2)
    fail = validate_against_capabilities(
        _payload(n=3), caps, reference_count=0
    )
    assert fail is not None
    assert fail.field == "n"
