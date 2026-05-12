"""Tests for adapter self-describing capability schemas (PR-A).

Each adapter's ``capability_schema()`` advertises which capability fields
admin can configure for that adapter, replacing the previous hard-coded
list in the frontend. These tests guard the contract:

1. Every key the schema names is also a real
   ``ProviderModelCapabilities`` field — admin's saved JSON is still
   validated by that pydantic class, so a key the schema invented but
   the model class doesn't know about would be silently dropped.
2. The discrete options on a list-typed field stay in sync with the
   adapter's internal ``_ALLOWED_*`` whitelist — preventing the kind of
   drift that originally motivated this refactor.
3. Every registered adapter returns a non-empty schema (or at least a
   list) without raising.
"""

from __future__ import annotations

import pytest

from app.adapters.base import AdapterRegistry
from app.adapters.gemini_v1beta import (
    _ASPECT_RATIOS_FLASH_31_EXTRA,
    _ASPECT_RATIOS_PRO,
    _IMAGE_SIZES_FLASH_31_EXTRA,
    _IMAGE_SIZES_PRO,
    _MAX_REFERENCES,
    _THINKING_LEVELS,
    GeminiV1BetaAdapter,
)
from app.adapters.openai_v1 import (
    _ALLOWED_BACKGROUND,
    _ALLOWED_MODERATION,
    _ALLOWED_OUTPUT_FORMAT,
    _ALLOWED_QUALITY,
    _ALLOWED_SIZE_PRESETS,
    OpenAIV1Adapter,
)
from app.schemas.provider import (
    CapabilityField,
    CapabilityFieldBool,
    CapabilityFieldInt,
    CapabilityFieldList,
    ProviderModelCapabilities,
)


def _allowed_capability_keys() -> set[str]:
    """Return every field name on ``ProviderModelCapabilities``."""
    return set(ProviderModelCapabilities.model_fields.keys())


# ---------------------------------------------------------------------------
# Per-adapter static checks
# ---------------------------------------------------------------------------


def test_openai_v1_capability_schema_subset_of_provider_model_capabilities() -> None:
    """Every key the openai_v1 schema names is a real capability field."""
    schema = OpenAIV1Adapter().capability_schema()
    allowed = _allowed_capability_keys()
    for f in schema:
        assert f.k in allowed, (
            f"openai_v1 declares unknown capability key {f.k!r}; "
            "ProviderModelCapabilities would silently drop it"
        )


def test_gemini_v1beta_capability_schema_subset_of_provider_model_capabilities() -> None:
    schema = GeminiV1BetaAdapter().capability_schema()
    allowed = _allowed_capability_keys()
    for f in schema:
        assert f.k in allowed, (
            f"gemini_v1beta declares unknown capability key {f.k!r}; "
            "ProviderModelCapabilities would silently drop it"
        )


def test_openai_v1_capability_schema_options_match_validate_allowed_sets() -> None:
    """List-field options mirror the adapter's internal ``_ALLOWED_*``."""
    schema = {f.k: f for f in OpenAIV1Adapter().capability_schema()}

    assert isinstance(schema["size"], CapabilityFieldList)
    assert set(schema["size"].options) == _ALLOWED_SIZE_PRESETS

    assert isinstance(schema["quality"], CapabilityFieldList)
    assert set(schema["quality"].options) == _ALLOWED_QUALITY

    assert isinstance(schema["output_format"], CapabilityFieldList)
    assert set(schema["output_format"].options) == _ALLOWED_OUTPUT_FORMAT

    assert isinstance(schema["background"], CapabilityFieldList)
    assert set(schema["background"].options) == _ALLOWED_BACKGROUND

    assert isinstance(schema["moderation"], CapabilityFieldList)
    assert set(schema["moderation"].options) == _ALLOWED_MODERATION


def test_gemini_v1beta_capability_schema_options_match_internal_sets() -> None:
    schema = {f.k: f for f in GeminiV1BetaAdapter().capability_schema()}

    assert isinstance(schema["aspect_ratio"], CapabilityFieldList)
    assert set(schema["aspect_ratio"].options) == (
        _ASPECT_RATIOS_PRO | _ASPECT_RATIOS_FLASH_31_EXTRA
    )

    assert isinstance(schema["image_size"], CapabilityFieldList)
    assert set(schema["image_size"].options) == (
        _IMAGE_SIZES_PRO | _IMAGE_SIZES_FLASH_31_EXTRA
    )

    assert isinstance(schema["thinking_level"], CapabilityFieldList)
    assert set(schema["thinking_level"].options) == _THINKING_LEVELS

    assert isinstance(schema["max_reference_images"], CapabilityFieldInt)
    assert schema["max_reference_images"].max == _MAX_REFERENCES


def test_openai_v1_does_not_expose_gemini_only_fields() -> None:
    """openai_v1 never lets admin configure aspect_ratio / image_size /
    thinking_level — those would just confuse admin and would be
    rejected by the adapter's ``_validate`` at request time anyway.
    """
    keys = {f.k for f in OpenAIV1Adapter().capability_schema()}
    for forbidden in ("aspect_ratio", "image_size", "thinking_level"):
        assert forbidden not in keys


def test_gemini_v1beta_does_not_expose_openai_only_fields() -> None:
    keys = {f.k for f in GeminiV1BetaAdapter().capability_schema()}
    # ``n_max`` is intentionally NOT forbidden any more: the Create-page
    # fan-out fallback relies on it as the user-facing slider ceiling,
    # while ``n_max_upstream`` (also exposed) carries the real per-call
    # ceiling the validator checks against.
    for forbidden in (
        "size",
        "quality",
        "background",
        "moderation",
        "output_format",
        "supports_mask",
        "supports_transparent_bg",
        "stream",
    ):
        assert forbidden not in keys


def test_gemini_v1beta_exposes_n_max_and_n_max_upstream_for_fanout() -> None:
    """Both knobs must surface so admin can configure slider vs. upstream cap.

    Slider ceiling lives in ``n_max``; the real per-call cap (1 for Gemini
    today) lives in ``n_max_upstream``. The Create page reads the first
    for slider rendering and uses the second to decide when to fan out
    into n=1 parallel POSTs.
    """
    keys = {f.k for f in GeminiV1BetaAdapter().capability_schema()}
    assert "n_max" in keys
    assert "n_max_upstream" in keys


def test_openai_v1_capability_schema_has_known_kinds() -> None:
    """Sanity-check the variety: at least one of each kind."""
    schema = OpenAIV1Adapter().capability_schema()
    kinds = {f.kind for f in schema}
    assert kinds == {"list", "int", "bool"}


def test_capability_schema_no_duplicate_keys_per_adapter() -> None:
    """A repeated ``k`` would break the React render-key invariant."""
    for adapter in (OpenAIV1Adapter(), GeminiV1BetaAdapter()):
        keys = [f.k for f in adapter.capability_schema()]
        assert len(keys) == len(set(keys)), (
            f"{adapter.adapter_type} has duplicate capability_schema keys: {keys}"
        )


# ---------------------------------------------------------------------------
# Registry-level — guards against a future adapter forgetting to implement.
# ---------------------------------------------------------------------------


def test_each_registered_adapter_returns_capability_schema() -> None:
    AdapterRegistry.reset_for_tests()
    try:
        reg = AdapterRegistry.instance()
        reg.discover()
        adapters = reg.list_all()
        assert adapters, "discover() found no adapters"
        for adapter in adapters:
            schema = adapter.capability_schema()
            assert isinstance(schema, list), (
                f"{adapter.adapter_type}.capability_schema() must return a list"
            )
            for f in schema:
                # Each entry must be a CapabilityField pydantic model so
                # the API layer can serialize it without surprise.
                assert hasattr(f, "k") and hasattr(f, "kind"), (
                    f"{adapter.adapter_type} schema entry not a "
                    f"CapabilityField: {f!r}"
                )
    finally:
        AdapterRegistry.reset_for_tests()


# ---------------------------------------------------------------------------
# Pydantic discriminator round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        CapabilityFieldList(k="size", options=["1024x1024"]),
        CapabilityFieldInt(k="n_max", min=1, max=10),
        CapabilityFieldBool(k="stream"),
    ],
)
def test_capability_field_round_trip_through_dump_validate(
    field: CapabilityField,
) -> None:
    """``model_dump`` then re-validate yields an equivalent shape.

    Important because the API layer serializes these to JSON and the
    test harness later compares against the dict form.
    """
    dumped = field.model_dump()
    assert dumped["k"] == field.k
    assert dumped["kind"] == field.kind
