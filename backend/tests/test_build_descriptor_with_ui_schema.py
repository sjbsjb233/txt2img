"""_build_descriptor wiring (test plan §3.6).

The descriptor object is what /api/models ships to the frontend.
Make sure the ui_schema field is populated end-to-end inside the
catalog's build path, both for known and exotic model_ids.
"""

import pytest

from app.adapters.base import get_registry
from app.domain.model_catalog import _build_descriptor


@pytest.fixture(autouse=True)
def _registry_ready(initialized_db):
    get_registry().discover()


class TestBuildDescriptorUISchema:

    def test_descriptor_includes_ui_schema_for_known_model(self):
        # Pass empty rows — ui_schema is provider-independent.
        desc = _build_descriptor("gpt-image-2", [])
        assert isinstance(desc.ui_schema, list)
        assert len(desc.ui_schema) > 0
        keys = {f.k for f in desc.ui_schema}
        assert "size" in keys
        assert "n_max" in keys

    def test_descriptor_ui_schema_empty_for_unknown_model(self):
        """Models not in _MODEL_DISPLAY (e.g. an experimental relay-only
        model_id) should silently get an empty ui_schema — the descriptor
        still ships, the frontend just renders an empty panel."""
        desc = _build_descriptor("experimental-relay-only-model", [])
        assert desc.ui_schema == []

    def test_descriptor_carries_value_key_through(self):
        """The n_max field declares value_key='n' — make sure the
        descriptor reaching the frontend preserves the override."""
        desc = _build_descriptor("gpt-image-2", [])
        n_max = next((f for f in desc.ui_schema if f.k == "n_max"), None)
        assert n_max is not None
        assert n_max.value_key == "n", (
            "n_max field must declare value_key='n' so the renderer "
            "writes batch size to params.n, not params.n_max"
        )

    def test_gemini_descriptor_carries_value_key(self):
        desc = _build_descriptor("gemini-3.1-flash-image-preview", [])
        n_max = next((f for f in desc.ui_schema if f.k == "n_max"), None)
        assert n_max is not None
        assert n_max.value_key == "n"
