"""Unit tests for the adapter abstract base class and registry.

These exercise discovery / registration mechanics independently of any
specific adapter implementation. Per-adapter wire-format tests live in
``test_openai_v1_adapter.py`` and ``test_gemini_v1beta_adapter.py``.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.base import AdapterRegistry, BaseAdapter
from app.schemas.models import ModelUIField
from app.schemas.normalized import (
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)
from app.schemas.provider import CapabilityField


# ---------------------------------------------------------------------------
# A minimal subclass for tests that only care about base-class behaviour.
# ---------------------------------------------------------------------------


class _Toy(BaseAdapter):
    adapter_type = "toy"
    display_name = "Toy"
    description = "test adapter"

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:  # pragma: no cover — not exercised here
        return NormalizedResponse()

    def supported_models(self) -> list[str]:
        return ["toy-model"]

    def capability_schema(self) -> list[CapabilityField]:
        return []

    def ui_schema(self, model_id: str) -> list[ModelUIField]:
        if model_id not in self.supported_models():
            raise ValueError(f"unsupported model: {model_id!r}")
        return []


def test_register_rejects_empty_adapter_type() -> None:
    class Bad(_Toy):
        adapter_type = ""

    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="adapter_type"):
        reg.register(Bad())


def test_register_rejects_missing_display_name() -> None:
    class Bad(_Toy):
        adapter_type = "x"
        display_name = ""

    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="display_name"):
        reg.register(Bad())


def test_register_rejects_duplicate_adapter_type() -> None:
    reg = AdapterRegistry()
    reg.register(_Toy())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_Toy())


def test_get_unknown_raises_keyerror() -> None:
    reg = AdapterRegistry()
    with pytest.raises(KeyError):
        reg.get("not-a-real-adapter")


def test_has_and_list_all() -> None:
    reg = AdapterRegistry()
    assert reg.has("toy") is False
    assert reg.list_all() == []
    reg.register(_Toy())
    assert reg.has("toy") is True
    assert [a.adapter_type for a in reg.list_all()] == ["toy"]


def test_discover_finds_built_in_adapters() -> None:
    """Walk ``app.adapters`` and confirm both built-ins register."""
    reg = AdapterRegistry()
    reg.discover()
    types = {a.adapter_type for a in reg.list_all()}
    assert "openai_v1" in types
    assert "gemini_v1beta" in types


def test_discover_idempotent() -> None:
    reg = AdapterRegistry()
    reg.discover()
    before = len(reg.list_all())
    reg.discover()
    assert len(reg.list_all()) == before


def test_normalize_error_passthrough_for_standard_error() -> None:
    """A ``StandardError`` already classified must be returned as-is."""
    adapter = _Toy()
    original = StandardError(StandardErrorKind.AUTH, "nope")
    assert adapter.normalize_error(original) is original


def test_normalize_error_classifies_timeouts() -> None:
    adapter = _Toy()
    err = adapter.normalize_error(httpx.ReadTimeout("slow"))
    assert err.kind is StandardErrorKind.UPSTREAM_TIMEOUT


def test_normalize_error_classifies_connect_errors() -> None:
    adapter = _Toy()
    err = adapter.normalize_error(httpx.ConnectError("refused"))
    assert err.kind is StandardErrorKind.NETWORK_ERROR


def test_normalize_error_falls_back_to_other() -> None:
    adapter = _Toy()
    err = adapter.normalize_error(RuntimeError("???"))
    assert err.kind is StandardErrorKind.OTHER


def test_singleton_instance() -> None:
    AdapterRegistry.reset_for_tests()
    a = AdapterRegistry.instance()
    b = AdapterRegistry.instance()
    assert a is b
    AdapterRegistry.reset_for_tests()
