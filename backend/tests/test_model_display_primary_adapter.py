"""_MODEL_DISPLAY × primary_adapter integrity (test plan §3.3).

Guards against the most common ways the wiring rots over time:
adding a new model to _MODEL_DISPLAY without filling primary_adapter,
typo'ing the adapter type, or pointing primary_adapter at an adapter
that doesn't list the model in supported_models.
"""

import logging
import pytest

from app.adapters.base import get_registry
from app.domain.model_catalog import _MODEL_DISPLAY, validate_primary_adapters


@pytest.fixture(autouse=True)
def _registry_ready(initialized_db):
    get_registry().discover()


class TestModelDisplayIntegrity:

    def test_every_entry_has_primary_adapter(self):
        for model_id, display in _MODEL_DISPLAY.items():
            assert "primary_adapter" in display, (
                f"{model_id} missing primary_adapter field"
            )
            assert display["primary_adapter"], (
                f"{model_id} has empty primary_adapter"
            )

    def test_primary_adapter_references_registered_adapter(self):
        registry = get_registry()
        for model_id, display in _MODEL_DISPLAY.items():
            primary = display["primary_adapter"]
            assert registry.has(primary), (
                f"{model_id} declares primary_adapter={primary!r} "
                "which is not registered"
            )

    def test_primary_adapter_supports_the_model(self):
        registry = get_registry()
        for model_id, display in _MODEL_DISPLAY.items():
            primary = display["primary_adapter"]
            adapter = registry.get(primary)
            assert model_id in adapter.supported_models(), (
                f"{model_id} declares primary_adapter={primary!r}, "
                f"but {primary}.supported_models() does not list it"
            )

    def test_validate_primary_adapters_emits_no_warning_on_healthy(
        self, caplog
    ):
        with caplog.at_level(logging.WARNING):
            validate_primary_adapters()
        warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING"
            and "model_catalog" in r.name.lower()
        ]
        assert not warnings, (
            "validate_primary_adapters emitted unexpected warnings: "
            f"{[r.getMessage() for r in warnings]}"
        )
