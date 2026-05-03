"""ModelUIField pydantic-level validation (test plan §3.1).

Schema-layer guarantees only — semantic constraints (e.g. min ≤ max,
options non-empty) are enforced by the adapter implementations.
"""

import pytest
from pydantic import ValidationError

from app.schemas.models import ModelUIField


class TestModelUIFieldDefaults:

    def test_minimal_chip_grid_field_valid(self):
        f = ModelUIField(
            k="size", control="chip-grid", label="Size", options=["a", "b"]
        )
        assert f.group == "primary"
        assert f.order == 100
        assert f.hint is None
        assert f.value_key is None

    def test_value_key_explicit_overrides_default(self):
        f = ModelUIField(
            k="n_max", value_key="n", control="number", label="Output count",
            min=1, max=10, presets=[1, 2, 4, 8],
        )
        assert f.value_key == "n"


class TestModelUIFieldRejection:

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ModelUIField(
                k="x", control="chip-row", label="L",
                options=["a"], unknown_field="boom",
            )

    def test_unknown_control_rejected(self):
        with pytest.raises(ValidationError):
            ModelUIField(k="x", control="dropdown", label="L")

    def test_missing_required_k(self):
        with pytest.raises(ValidationError):
            ModelUIField(control="chip-row", label="L", options=["a"])

    def test_missing_required_label(self):
        with pytest.raises(ValidationError):
            ModelUIField(k="x", control="chip-row", options=["a"])

    def test_invalid_group_rejected(self):
        with pytest.raises(ValidationError):
            ModelUIField(
                k="x", control="chip-row", label="L",
                options=["a"], group="other",
            )


class TestModelUIFieldEdgeValues:

    def test_empty_options_list_accepted(self):
        """Schema layer doesn't enforce non-empty options;
        adapter-level validation does."""
        f = ModelUIField(k="x", control="chip-row", label="L", options=[])
        assert f.options == []

    def test_options_with_special_chars(self):
        f = ModelUIField(
            k="x", control="chip-row", label="L",
            options=["1024x1024", "auto", "16:9", "21:9"],
        )
        assert "21:9" in f.options

    def test_label_with_unicode(self):
        f = ModelUIField(
            k="x", control="chip-row",
            label="形状（aspect ratio）", options=["a"],
        )
        assert f.label == "形状（aspect ratio）"

    def test_negative_order_allowed(self):
        f = ModelUIField(
            k="x", control="chip-row", label="L",
            options=["a"], order=-10,
        )
        assert f.order == -10

    def test_min_greater_than_max_not_validated_at_schema_layer(self):
        f = ModelUIField(k="x", control="number", label="L", min=10, max=1)
        assert f.min == 10 and f.max == 1

    def test_presets_outside_max_not_rejected_at_schema_layer(self):
        f = ModelUIField(
            k="x", control="number", label="L",
            min=1, max=4, presets=[1, 2, 100],
        )
        assert 100 in f.presets

    @pytest.mark.parametrize("group", ["primary", "advanced"])
    def test_valid_groups(self, group):
        f = ModelUIField(
            k="x", control="chip-row", label="L",
            options=["a"], group=group,
        )
        assert f.group == group

    @pytest.mark.parametrize(
        "control", ["chip-grid", "chip-row", "number", "toggle", "select"]
    )
    def test_all_valid_controls_accepted(self, control):
        f = ModelUIField(k="x", control=control, label="L")
        assert f.control == control
