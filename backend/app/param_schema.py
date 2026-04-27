"""Parameter-schema merge & validation helpers.

The catalog stores parameter info in two layers:

* ``ImageModel.param_schema`` — the *full* set of parameters a model can accept.
* ``RelayStationModel.param_capabilities`` — a per-(station, model) narrowing.

The frontend wants to render a single resolved schema for a chosen
(model, station) pair. :func:`merge_param_schema` produces it. The dispatcher
also needs to validate user-supplied values against that resolved schema —
:func:`validate_params` does that.

Schema entry shape (intentionally permissive — the JSON columns are
``dict[str, Any]``)::

    {
      "type":     "enum" | "int" | "float" | "string" | "bool",
      "values":   [...]               # for enum
      "min":      N, "max": M,        # for int / float
      "step":     S,                  # optional, int / float
      "default":  V,                  # optional
      "label":    "Human label",      # optional, for the UI
      "description": "...",           # optional
      "required": false,              # optional, default false
      "applies_when": {...}           # optional, conditional visibility hint
    }

A station capability entry adds one extra key ``supported`` (bool). When
``supported`` is ``False``, the parameter is hard-disabled for that station
even if the model supports it.
"""

from __future__ import annotations

from typing import Any, Mapping

ParamSchema = dict[str, dict[str, Any]]


def merge_param_schema(
    model_schema: Mapping[str, Any] | None,
    station_caps: Mapping[str, Any] | None,
) -> ParamSchema:
    """Return the resolved parameter schema for one (model, station) pair.

    Rules (also documented on ``RelayStationModel``):

    * Start from ``model_schema`` (the superset).
    * For each key in ``station_caps``:

      - ``{"supported": false}`` removes the parameter for this station.
      - ``values`` on the cap intersects with the model's ``values`` (or
        replaces them if the model entry didn't list any).
      - ``min`` / ``max`` further clamps the model's range.
      - ``default`` overrides the model's default.

    * Capability keys absent from the model schema are added as-is (allows
      a station to expose extra knobs the catalog hasn't promoted yet).
    """
    result: ParamSchema = {}
    model_schema = dict(model_schema or {})
    station_caps = dict(station_caps or {})

    for key, spec in model_schema.items():
        if not isinstance(spec, Mapping):
            continue
        cap = station_caps.get(key)
        if isinstance(cap, Mapping) and cap.get("supported") is False:
            continue
        merged = dict(spec)
        if isinstance(cap, Mapping):
            if "values" in cap and isinstance(cap["values"], list):
                if isinstance(spec.get("values"), list):
                    allowed = set(spec["values"])
                    merged["values"] = [v for v in cap["values"] if v in allowed]
                else:
                    merged["values"] = list(cap["values"])
            if "min" in cap:
                merged["min"] = (
                    max(spec["min"], cap["min"]) if "min" in spec else cap["min"]
                )
            if "max" in cap:
                merged["max"] = (
                    min(spec["max"], cap["max"]) if "max" in spec else cap["max"]
                )
            for opt in ("default", "step", "label", "description", "applies_when", "required"):
                if opt in cap:
                    merged[opt] = cap[opt]
        result[key] = merged

    for key, cap in station_caps.items():
        if key in result or key in model_schema:
            continue
        if not isinstance(cap, Mapping):
            continue
        if cap.get("supported") is False:
            continue
        result[key] = {k: v for k, v in cap.items() if k != "supported"}

    return result


class ParamValidationError(ValueError):
    """Raised when user-supplied params don't satisfy the merged schema."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_params(
    schema: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate ``params`` against a resolved schema.

    Returns a copy of ``params`` containing only keys present in the schema
    (unknown keys are dropped — the dispatcher should not silently forward
    arbitrary garbage to upstream). Raises :class:`ParamValidationError` if
    a required field is missing or a value is out of range.
    """
    errors: list[str] = []
    cleaned: dict[str, Any] = {}

    for key, spec in schema.items():
        if not isinstance(spec, Mapping):
            continue
        if key not in params or params[key] is None:
            if spec.get("required"):
                errors.append(f"{key}: required")
            continue

        value = params[key]
        ptype = spec.get("type")

        if "values" in spec and isinstance(spec["values"], list):
            if value not in spec["values"]:
                errors.append(
                    f"{key}: {value!r} is not one of {spec['values']}"
                )
                continue

        if ptype in ("int", "float"):
            try:
                num = int(value) if ptype == "int" else float(value)
            except (TypeError, ValueError):
                errors.append(f"{key}: expected {ptype}")
                continue
            if "min" in spec and num < spec["min"]:
                errors.append(f"{key}: {num} < min {spec['min']}")
                continue
            if "max" in spec and num > spec["max"]:
                errors.append(f"{key}: {num} > max {spec['max']}")
                continue
            value = num
        elif ptype == "bool" and not isinstance(value, bool):
            errors.append(f"{key}: expected bool")
            continue
        elif ptype == "string" and not isinstance(value, str):
            errors.append(f"{key}: expected string")
            continue

        cleaned[key] = value

    if errors:
        raise ParamValidationError(errors)
    return cleaned
