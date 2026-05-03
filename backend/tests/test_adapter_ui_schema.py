"""Tests for adapter ``ui_schema(model_id)`` (Create-page renderer, PR-1).

Each adapter declares the right-hand parameter panel for the models it
drives. The frontend pairs this layout with the merged
``ModelCapabilities`` to grey out individual options instead of dropping
fields entirely. These tests guard the contract:

1. Every ``k`` named is also a real ``ModelCapabilities`` field — a
   typo would yield a permanently-disabled control because the
   capability lookup would always be ``None``.
2. ``options`` for list-typed fields stay in sync with the adapter's
   internal ``_ALLOWED_*`` whitelist (or the per-feature subset).
3. Per-model differences (e.g. 3 Pro vs 3.1 Flash) are reflected.
4. ``ui_schema`` is idempotent and rejects unknown model ids.
5. Field ``order`` is unique within each ``group`` so the panel renders
   in a deterministic position.
"""

from __future__ import annotations

import pytest

from app.adapters.base import AdapterRegistry
from app.adapters.gemini_v1beta import (
    _ASPECT_RATIOS_FLASH_31_EXTRA,
    _ASPECT_RATIOS_PRO,
    _IMAGE_SIZES_FLASH_31_EXTRA,
    _IMAGE_SIZES_PRO,
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
from app.schemas.models import ModelCapabilities, ModelUIField


def _capability_field_names() -> set[str]:
    return set(ModelCapabilities.model_fields.keys())


# ---------------------------------------------------------------------------
# openai_v1
# ---------------------------------------------------------------------------


def test_openai_v1_ui_schema_keys_exist_on_model_capabilities() -> None:
    adapter = OpenAIV1Adapter()
    allowed = _capability_field_names()
    for model_id in adapter.supported_models():
        for field in adapter.ui_schema(model_id):
            assert field.k in allowed, (
                f"openai_v1 ui_schema for {model_id} declares unknown "
                f"capability field {field.k!r}"
            )


def test_openai_v1_ui_schema_options_match_allowed_sets() -> None:
    schema = {f.k: f for f in OpenAIV1Adapter().ui_schema("gpt-image-2")}

    assert schema["size"].options is not None
    assert set(schema["size"].options) == _ALLOWED_SIZE_PRESETS
    assert schema["quality"].options is not None
    assert set(schema["quality"].options) == _ALLOWED_QUALITY
    assert schema["output_format"].options is not None
    assert set(schema["output_format"].options) == _ALLOWED_OUTPUT_FORMAT
    assert schema["background"].options is not None
    assert set(schema["background"].options) == _ALLOWED_BACKGROUND
    assert schema["moderation"].options is not None
    assert set(schema["moderation"].options) == _ALLOWED_MODERATION


def test_openai_v1_ui_schema_groups_and_controls() -> None:
    schema = {f.k: f for f in OpenAIV1Adapter().ui_schema("gpt-image-2")}

    assert schema["n_max"].control == "number"
    assert schema["n_max"].group == "primary"
    assert schema["n_max"].presets == [1, 2, 4, 8]
    assert schema["n_max"].max == 10

    assert schema["size"].control == "chip-grid"
    assert schema["size"].group == "primary"

    for k in ("quality", "output_format", "background", "moderation"):
        assert schema[k].group == "advanced", (
            f"expected {k} in advanced group, got {schema[k].group}"
        )


def test_openai_v1_ui_schema_snapshot_matches_base_model() -> None:
    """The pinned snapshot shares the gpt-image-2 panel."""
    adapter = OpenAIV1Adapter()
    base = adapter.ui_schema("gpt-image-2")
    snap = adapter.ui_schema("gpt-image-2-2026-04-21")
    assert [(f.k, f.control, f.group) for f in base] == [
        (f.k, f.control, f.group) for f in snap
    ]


def test_openai_v1_ui_schema_unsupported_model_raises() -> None:
    with pytest.raises(ValueError):
        OpenAIV1Adapter().ui_schema("not-a-real-model")


# ---------------------------------------------------------------------------
# gemini_v1beta
# ---------------------------------------------------------------------------


def test_gemini_v1beta_ui_schema_keys_exist_on_model_capabilities() -> None:
    adapter = GeminiV1BetaAdapter()
    allowed = _capability_field_names()
    for model_id in adapter.supported_models():
        for field in adapter.ui_schema(model_id):
            assert field.k in allowed, (
                f"gemini_v1beta ui_schema for {model_id} declares unknown "
                f"capability field {field.k!r}"
            )


def test_gemini_v1beta_3_pro_omits_flash_only_fields() -> None:
    schema = GeminiV1BetaAdapter().ui_schema("gemini-3-pro-image-preview")
    keys = {f.k for f in schema}
    for forbidden in ("thinking_level", "include_thoughts", "image_search"):
        assert forbidden not in keys, (
            f"3-pro panel must not surface {forbidden} (3.1-flash only)"
        )

    aspect = next(f for f in schema if f.k == "aspect_ratio")
    assert aspect.options is not None
    assert set(aspect.options) == _ASPECT_RATIOS_PRO

    image_size = next(f for f in schema if f.k == "image_size")
    assert image_size.options is not None
    assert set(image_size.options) == _IMAGE_SIZES_PRO


def test_gemini_v1beta_3_1_flash_has_full_field_set() -> None:
    schema = GeminiV1BetaAdapter().ui_schema(
        "gemini-3.1-flash-image-preview"
    )
    by_key = {f.k: f for f in schema}

    assert "thinking_level" in by_key
    assert by_key["thinking_level"].options is not None
    assert set(by_key["thinking_level"].options) == _THINKING_LEVELS
    assert by_key["include_thoughts"].control == "toggle"
    assert by_key["image_search"].control == "toggle"
    assert by_key["google_search"].control == "toggle"

    assert by_key["aspect_ratio"].options is not None
    assert set(by_key["aspect_ratio"].options) == (
        _ASPECT_RATIOS_PRO | _ASPECT_RATIOS_FLASH_31_EXTRA
    )
    assert by_key["image_size"].options is not None
    assert set(by_key["image_size"].options) == (
        _IMAGE_SIZES_PRO | _IMAGE_SIZES_FLASH_31_EXTRA
    )


def test_gemini_v1beta_n_max_locked_to_one() -> None:
    """Gemini hard-codes n=1 — panel still renders the ticker
    so the field slot is stable across model switches."""
    for model_id in (
        "gemini-3-pro-image-preview",
        "gemini-3.1-flash-image-preview",
        "gemini-2.5-flash-image",
    ):
        schema = GeminiV1BetaAdapter().ui_schema(model_id)
        n_max = next(f for f in schema if f.k == "n_max")
        assert n_max.control == "number"
        assert n_max.max == 1
        assert n_max.presets == [1]


def test_gemini_v1beta_unsupported_model_raises() -> None:
    with pytest.raises(ValueError):
        GeminiV1BetaAdapter().ui_schema("gpt-image-2")


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter,model_id",
    [
        (OpenAIV1Adapter(), "gpt-image-2"),
        (OpenAIV1Adapter(), "gpt-image-2-2026-04-21"),
        (GeminiV1BetaAdapter(), "gemini-3-pro-image-preview"),
        (GeminiV1BetaAdapter(), "gemini-3.1-flash-image-preview"),
        (GeminiV1BetaAdapter(), "gemini-2.5-flash-image"),
    ],
)
def test_ui_schema_idempotent(adapter, model_id: str) -> None:  # type: ignore[no-untyped-def]
    """Same call should yield equal output — no hidden state."""
    a = adapter.ui_schema(model_id)
    b = adapter.ui_schema(model_id)
    assert [f.model_dump() for f in a] == [f.model_dump() for f in b]


@pytest.mark.parametrize(
    "adapter,model_id",
    [
        (OpenAIV1Adapter(), "gpt-image-2"),
        (GeminiV1BetaAdapter(), "gemini-3-pro-image-preview"),
        (GeminiV1BetaAdapter(), "gemini-3.1-flash-image-preview"),
    ],
)
def test_ui_schema_field_order_unique_within_group(
    adapter, model_id: str
) -> None:  # type: ignore[no-untyped-def]
    """Render order must be deterministic; duplicate ``order`` would
    leave the panel in a Python-dict-iteration mood."""
    schema = adapter.ui_schema(model_id)
    by_group: dict[str, list[int]] = {}
    for field in schema:
        by_group.setdefault(field.group, []).append(field.order)
    for group, orders in by_group.items():
        assert len(orders) == len(set(orders)), (
            f"{adapter.adapter_type}/{model_id} duplicate order in {group}: "
            f"{orders}"
        )


def test_each_registered_adapter_returns_ui_schema_for_each_model() -> None:
    AdapterRegistry.reset_for_tests()
    try:
        reg = AdapterRegistry.instance()
        reg.discover()
        for adapter in reg.list_all():
            for model_id in adapter.supported_models():
                schema = adapter.ui_schema(model_id)
                assert isinstance(schema, list), (
                    f"{adapter.adapter_type}.ui_schema({model_id!r}) must "
                    "return a list"
                )
                for entry in schema:
                    assert isinstance(entry, ModelUIField)
    finally:
        AdapterRegistry.reset_for_tests()
