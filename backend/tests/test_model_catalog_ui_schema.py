"""Tests for ``model_catalog._resolve_ui_schema`` + ``validate_primary_adapters``.

The catalog routes every Create-page panel lookup through
``_MODEL_DISPLAY[model_id]['primary_adapter']``. These tests pin the
contract:

- Known models surface schema from the configured primary adapter.
- Models absent from the display table degrade silently to ``[]``.
- Misconfigured ``primary_adapter`` (missing field, unregistered
  adapter type, adapter that doesn't list the model) degrade to ``[]``
  *and* log a warning so ops can find the issue.
- ``validate_primary_adapters`` emits warnings when wiring is broken.
- Multiple adapters declaring the same model don't influence which
  schema flows through — only the explicitly named primary does.
"""

from __future__ import annotations

import logging

import pytest

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.domain import model_catalog
from app.domain.model_catalog import (
    _MODEL_DISPLAY,
    _resolve_ui_schema,
    validate_primary_adapters,
)
from app.schemas.models import ModelUIField
from app.schemas.normalized import (
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
)
from app.schemas.provider import CapabilityField


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyAdapter(BaseAdapter):
    """Minimal adapter so we can register fakes without monkey-patching."""

    adapter_type = "dummy"
    display_name = "Dummy"
    description = "test"
    _models: list[str] = []
    _ui_schema: dict[str, list[ModelUIField]] = {}

    async def generate(  # pragma: no cover — never called here
        self, provider: ProviderConfig, request: NormalizedRequest
    ) -> NormalizedResponse:
        return NormalizedResponse()

    def supported_models(self) -> list[str]:
        return list(self._models)

    def capability_schema(self) -> list[CapabilityField]:
        return []

    def ui_schema(self, model_id: str) -> list[ModelUIField]:
        if model_id not in self._models:
            raise ValueError(f"unsupported model: {model_id!r}")
        return list(self._ui_schema.get(model_id, []))


@pytest.fixture
def fresh_registry():
    """Yield a freshly-discovered registry; restore at exit."""
    AdapterRegistry.reset_for_tests()
    reg = AdapterRegistry.instance()
    reg.discover()
    try:
        yield reg
    finally:
        AdapterRegistry.reset_for_tests()


@pytest.fixture
def model_catalog_logger():
    """Yield the model_catalog logger with ``disabled`` cleared.

    Alembic's migration env runs ``logging.config.fileConfig`` during
    ``app.main`` lifespan / migration startup, which (by default)
    disables every pre-existing logger. Tests that depend on
    ``caplog`` seeing model_catalog warnings re-enable it explicitly.
    """
    logger = logging.getLogger("txt2img.model_catalog")
    prior = logger.disabled
    logger.disabled = False
    try:
        yield logger
    finally:
        logger.disabled = prior


# ---------------------------------------------------------------------------
# Resolution against real built-in adapters
# ---------------------------------------------------------------------------


def test_resolve_ui_schema_uses_primary_adapter_for_openai(
    fresh_registry,
) -> None:
    schema = _resolve_ui_schema("gpt-image-2")
    assert schema, "openai_v1 should produce a non-empty panel"
    keys = {f.k for f in schema}
    # Reflects openai-side fields, not gemini-side.
    assert "size" in keys
    assert "aspect_ratio" not in keys


def test_resolve_ui_schema_uses_primary_adapter_for_gemini_flash(
    fresh_registry,
) -> None:
    schema = _resolve_ui_schema("gemini-3.1-flash-image-preview")
    assert schema
    keys = {f.k for f in schema}
    assert "aspect_ratio" in keys
    assert "thinking_level" in keys
    assert "size" not in keys  # openai-only


def test_resolve_ui_schema_unknown_model_returns_empty(fresh_registry) -> None:
    """A model not in _MODEL_DISPLAY (e.g. an experimental relay model)
    surfaces an empty panel rather than crashing."""
    assert _resolve_ui_schema("totally-fake-model-id") == []


# ---------------------------------------------------------------------------
# Misconfiguration paths — must degrade + log
# ---------------------------------------------------------------------------


def test_resolve_ui_schema_missing_primary_adapter_returns_empty(
    fresh_registry, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Inject a model with no primary_adapter set."""
    patched = dict(_MODEL_DISPLAY)
    patched["__test_no_primary__"] = {
        "display_name": "Test",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": None,
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    # Missing primary_adapter is silently empty (the warning fires from
    # validate_primary_adapters, not from per-call resolution).
    assert _resolve_ui_schema("__test_no_primary__") == []


def test_resolve_ui_schema_unregistered_primary_adapter_warns(
    fresh_registry,
    model_catalog_logger,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    patched = dict(_MODEL_DISPLAY)
    patched["__test_bad_adapter__"] = {
        "display_name": "Test",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": "nonexistent_adapter_type",
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    assert _resolve_ui_schema("__test_bad_adapter__") == []
    assert any(
        "is not registered" in rec.getMessage()
        for rec in caplog.records
    )


def test_resolve_ui_schema_adapter_refuses_model_warns(
    fresh_registry,
    model_catalog_logger,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """If primary_adapter is real but ui_schema raises ValueError —
    e.g. _MODEL_DISPLAY says openai_v1 but the model isn't in
    openai_v1.supported_models() — degrade with a warning."""
    patched = dict(_MODEL_DISPLAY)
    patched["__test_stale__"] = {
        "display_name": "Stale",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": "openai_v1",
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    assert _resolve_ui_schema("__test_stale__") == []
    assert any(
        "refused to produce ui_schema" in rec.getMessage()
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# Multi-adapter isolation — the headline guarantee from design v2 §3.2
# ---------------------------------------------------------------------------


def test_ui_schema_isolated_from_other_adapters_supporting_same_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register a second adapter that *also* claims gpt-image-2 with a
    deliberately different ui_schema. _resolve_ui_schema must still
    return openai_v1's output, because that's what _MODEL_DISPLAY
    declares."""
    AdapterRegistry.reset_for_tests()
    try:
        reg = AdapterRegistry.instance()
        reg.discover()

        class _SneakyAdapter(_DummyAdapter):
            adapter_type = "sneaky_v1"
            display_name = "Sneaky"
            _models = ["gpt-image-2"]
            _ui_schema = {
                "gpt-image-2": [
                    ModelUIField(
                        k="aspect_ratio",  # would never appear in real openai_v1 schema
                        control="chip-row",
                        label="Hijacked",
                        options=["1:1"],
                    )
                ]
            }

        reg.register(_SneakyAdapter())

        schema = _resolve_ui_schema("gpt-image-2")
        keys = {f.k for f in schema}
        assert "size" in keys, (
            "should still come from openai_v1, the declared primary"
        )
        assert "aspect_ratio" not in keys, (
            "sneaky adapter must not influence panel for gpt-image-2"
        )
    finally:
        AdapterRegistry.reset_for_tests()


# ---------------------------------------------------------------------------
# validate_primary_adapters
# ---------------------------------------------------------------------------


def test_validate_primary_adapters_quiet_for_clean_config(
    fresh_registry, model_catalog_logger, caplog
) -> None:
    """Stock _MODEL_DISPLAY against the real registry — no warnings."""
    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    validate_primary_adapters()
    msgs = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "txt2img.model_catalog"
    ]
    assert msgs == [], f"unexpected warnings: {msgs}"


def test_validate_primary_adapters_warns_on_mismatched_supported_models(
    fresh_registry,
    model_catalog_logger,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    patched = dict(_MODEL_DISPLAY)
    patched["__bogus_for_openai__"] = {
        "display_name": "Bogus",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": "openai_v1",
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    validate_primary_adapters()
    assert any(
        "does not list it in supported_models" in rec.getMessage()
        for rec in caplog.records
    )


def test_validate_primary_adapters_warns_on_missing_primary_adapter(
    fresh_registry,
    model_catalog_logger,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    patched = dict(_MODEL_DISPLAY)
    patched["__missing_primary__"] = {
        "display_name": "X",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": None,
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    validate_primary_adapters()
    assert any(
        "no primary_adapter" in rec.getMessage()
        for rec in caplog.records
    )


def test_validate_primary_adapters_warns_on_unregistered_adapter(
    fresh_registry,
    model_catalog_logger,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    patched = dict(_MODEL_DISPLAY)
    patched["__bad_adapter_type__"] = {
        "display_name": "X",
        "tag": None,
        "logo": None,
        "blurb": None,
        "primary_adapter": "definitely_not_registered",
    }
    monkeypatch.setattr(model_catalog, "_MODEL_DISPLAY", patched)

    caplog.set_level(logging.WARNING, logger="txt2img.model_catalog")
    validate_primary_adapters()
    assert any(
        "is not registered" in rec.getMessage()
        for rec in caplog.records
    )
