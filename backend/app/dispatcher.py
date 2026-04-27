"""Pick a relay station for a (model, params) request and call the upstream.

Two responsibilities:

* :func:`select_station_binding` — given ``model_key`` and a station selector
  (``"auto"`` or a specific ``provider_id``), return one routable
  ``RelayStationModel`` row whose effective param schema accepts the
  user-supplied params. ``"auto"`` picks uniformly at random.
* :func:`dispatch_generate` — using the chosen binding, route to the right
  image client (``openai_images`` vs ``gemini_v1beta``), translate the schema
  parameter names into the client's argument names, run the call, and return
  the resulting image bytes.

The dispatcher is intentionally thin: schema validation lives in
``param_schema.py``, transport lives in ``image_clients/``.
"""

from __future__ import annotations

import base64
import logging
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy.orm import Session, selectinload

from .image_clients import BltcyGptImage2Client, BltcyNanoBananaClient
from .image_clients.bltcy_gpt_image_2 import BltcyGptImage2Error
from .image_clients.bltcy_nano_banana import BltcyNanoBananaError
from .models import ImageModel, RelayStation, RelayStationModel
from .param_schema import (
    ParamValidationError,
    merge_param_schema,
    validate_params,
)

logger = logging.getLogger(__name__)

ADAPTER_OPENAI = "openai_images"
ADAPTER_GEMINI = "gemini_v1beta"


class DispatcherError(RuntimeError):
    """Wraps any routing / upstream failure with a stable shape for the API layer."""

    def __init__(self, status: int, code: str, message: str, payload: Any = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.payload = payload


@dataclass
class GeneratedImage:
    mime_type: str
    image_bytes: bytes

    @property
    def b64(self) -> str:
        return base64.b64encode(self.image_bytes).decode("ascii")


@dataclass
class DispatchResult:
    images: list[GeneratedImage]
    model_key: str
    station_provider_id: str
    revised_prompt: str | None = None
    usage: dict | None = None


# ---------------------------------------------------------------------------
# Station selection
# ---------------------------------------------------------------------------


def _is_routable(binding: RelayStationModel) -> bool:
    station = binding.relay_station
    model = binding.image_model
    if not (binding.enabled and station.enabled and model.enabled):
        return False
    if station.balance_cny is not None and station.balance_cny <= Decimal("0"):
        return False
    return True


def _params_fit_schema(schema: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    """Return True iff every user-supplied param is acceptable to ``schema``.

    Unknown keys are tolerated (the dispatcher drops them later); the question
    here is only "does THIS station support what the user asked for".
    """
    for key, value in params.items():
        spec = schema.get(key)
        if not isinstance(spec, Mapping):
            # Param not exposed by this station — soft tolerate, validator
            # will drop it before forwarding.
            continue
        if "values" in spec and isinstance(spec["values"], list):
            if value not in spec["values"]:
                return False
        if spec.get("type") in ("int", "float"):
            try:
                num = float(value)
            except (TypeError, ValueError):
                return False
            if "min" in spec and num < spec["min"]:
                return False
            if "max" in spec and num > spec["max"]:
                return False
    return True


def select_station_binding(
    db: Session,
    *,
    model_key: str,
    station: str,
    params: Mapping[str, Any],
) -> tuple[RelayStationModel, dict[str, Any]]:
    """Pick a routable (station, model) binding.

    Returns the binding and the merged effective parameter schema. Raises
    :class:`DispatcherError` (404 / 409) if there's nothing to route to.
    """
    image_model = (
        db.query(ImageModel)
        .options(
            selectinload(ImageModel.station_bindings).selectinload(
                RelayStationModel.relay_station
            )
        )
        .filter(ImageModel.model_key == model_key, ImageModel.enabled.is_(True))
        .first()
    )
    if image_model is None:
        raise DispatcherError(404, "model_not_found", f"unknown model_key {model_key!r}")

    candidates: list[tuple[RelayStationModel, dict[str, Any]]] = []
    for binding in image_model.station_bindings:
        if not _is_routable(binding):
            continue
        if station != "auto" and binding.relay_station.provider_id != station:
            continue
        effective = merge_param_schema(image_model.param_schema, binding.param_capabilities)
        if not _params_fit_schema(effective, params):
            continue
        candidates.append((binding, effective))

    if not candidates:
        if station != "auto":
            raise DispatcherError(
                409,
                "station_not_routable",
                f"station {station!r} cannot serve {model_key!r} with the given params",
            )
        raise DispatcherError(
            409,
            "no_routable_station",
            f"no enabled station can serve {model_key!r} with the given params",
        )

    if station == "auto":
        return random.choice(candidates)
    return candidates[0]


# ---------------------------------------------------------------------------
# Adapter dispatch
# ---------------------------------------------------------------------------


def _decode_input_images(
    raw_images: list[dict[str, Any]] | None,
) -> list[tuple[str, bytes]]:
    if not raw_images:
        return []
    decoded: list[tuple[str, bytes]] = []
    for entry in raw_images:
        b64 = entry.get("b64") if isinstance(entry, Mapping) else None
        mime = entry.get("mime_type") if isinstance(entry, Mapping) else None
        if not b64 or not mime:
            raise DispatcherError(
                422, "invalid_input_image", "input_images entries need mime_type + b64"
            )
        try:
            data = base64.b64decode(b64)
        except Exception as exc:
            raise DispatcherError(
                422, "invalid_input_image", f"could not decode b64: {exc}"
            ) from exc
        decoded.append((mime, data))
    return decoded


async def _dispatch_openai(
    *,
    station: RelayStation,
    model_key: str,
    prompt: str,
    params: dict[str, Any],
    input_images: list[tuple[str, bytes]],
) -> DispatchResult:
    client = BltcyGptImage2Client(
        api_key=station.api_key,
        base_url=station.base_url,
        default_model=model_key,
    )
    async with client:
        try:
            if input_images:
                mime, data = input_images[0]
                ext = "png"
                if mime == "image/jpeg":
                    ext = "jpg"
                elif mime == "image/webp":
                    ext = "webp"
                result = await client.edit(
                    prompt=prompt,
                    image=data,
                    image_filename=f"input.{ext}",
                    image_mime=mime,
                    size=params.get("size"),
                    quality=params.get("quality"),
                    n=int(params.get("n", 1) or 1),
                    background=params.get("background"),
                    output_format=params.get("output_format"),
                    output_compression=params.get("output_compression"),
                )
            else:
                result = await client.generate(
                    prompt=prompt,
                    size=params.get("size"),
                    quality=params.get("quality"),
                    n=int(params.get("n", 1) or 1),
                    background=params.get("background"),
                    output_format=params.get("output_format"),
                    output_compression=params.get("output_compression"),
                    moderation=params.get("moderation"),
                )
        except BltcyGptImage2Error as exc:
            raise DispatcherError(
                502, "upstream_error", exc.message, payload=exc.payload
            ) from exc

    return DispatchResult(
        images=[GeneratedImage(mime_type=result.mime_type, image_bytes=result.image_bytes)],
        model_key=model_key,
        station_provider_id=station.provider_id,
        revised_prompt=result.revised_prompt,
        usage=result.usage,
    )


async def _dispatch_gemini(
    *,
    station: RelayStation,
    model_key: str,
    prompt: str,
    params: dict[str, Any],
    input_images: list[tuple[str, bytes]],
) -> DispatchResult:
    client = BltcyNanoBananaClient(
        api_key=station.api_key,
        base_url=station.base_url,
        default_model=model_key,  # type: ignore[arg-type]
    )
    async with client:
        try:
            if input_images:
                result = await client.edit(
                    prompt=prompt,
                    input_images=input_images,
                    model=model_key,  # type: ignore[arg-type]
                    aspect_ratio=params.get("aspect_ratio"),
                    image_size=params.get("image_size"),
                )
            else:
                result = await client.generate(
                    prompt=prompt,
                    model=model_key,  # type: ignore[arg-type]
                    aspect_ratio=params.get("aspect_ratio"),
                    image_size=params.get("image_size"),
                )
        except BltcyNanoBananaError as exc:
            raise DispatcherError(
                502, "upstream_error", exc.message, payload=exc.payload
            ) from exc

    return DispatchResult(
        images=[GeneratedImage(mime_type=result.mime_type, image_bytes=result.image_bytes)],
        model_key=model_key,
        station_provider_id=station.provider_id,
        usage=result.usage,
    )


async def dispatch_generate(
    db: Session,
    *,
    model_key: str,
    station: str,
    prompt: str,
    params: dict[str, Any],
    input_images: list[dict[str, Any]] | None = None,
) -> DispatchResult:
    """End-to-end: pick a station, validate params, call upstream, decrement balance."""
    if not prompt or not prompt.strip():
        raise DispatcherError(422, "invalid_prompt", "prompt is required")

    binding, effective_schema = select_station_binding(
        db, model_key=model_key, station=station, params=params
    )
    try:
        cleaned = validate_params(effective_schema, params)
    except ParamValidationError as exc:
        raise DispatcherError(422, "invalid_params", str(exc), payload=exc.errors) from exc

    decoded_inputs = _decode_input_images(input_images)

    adapter = binding.relay_station.adapter_type
    if adapter == ADAPTER_OPENAI:
        result = await _dispatch_openai(
            station=binding.relay_station,
            model_key=model_key,
            prompt=prompt,
            params=cleaned,
            input_images=decoded_inputs,
        )
    elif adapter == ADAPTER_GEMINI:
        result = await _dispatch_gemini(
            station=binding.relay_station,
            model_key=model_key,
            prompt=prompt,
            params=cleaned,
            input_images=decoded_inputs,
        )
    else:
        raise DispatcherError(
            500,
            "unknown_adapter",
            f"station {binding.relay_station.provider_id!r} has unknown adapter_type {adapter!r}",
        )

    # Best-effort balance bookkeeping. We don't fail the request if commit fails;
    # the image was already generated.
    cost = binding.cost_per_image_cny or binding.relay_station.cost_per_image_cny
    if cost:
        try:
            station_row = binding.relay_station
            charged = cost * Decimal(len(result.images))
            station_row.balance_cny = (station_row.balance_cny or Decimal("0")) - charged
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("Failed to decrement balance for station %s", binding.relay_station.provider_id)

    return result
