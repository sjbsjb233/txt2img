"""Pre-flight parameter check for ``POST /api/jobs``.

Design doc §5.4 / §6.4 say the route must reject any request whose
parameters fall outside the *effective capabilities* — i.e. the union
of the per-(provider, model) capability whitelists for every provider
the user could reach.

This module hosts that one function so the route handler stays focused
on the I/O / multipart wiring. The check is purely structural: it does
not consult the metrics engine or pick a provider, it just validates
each user-supplied scalar against the merged capability shape returned
by :func:`app.domain.model_catalog.effective_capabilities_for_user`.

The error code surfaced on rejection is ``INVALID_PARAMETER`` (HTTP
422) per design doc §17, with a ``field`` indicating which knob was
illegal — that's what the frontend needs to highlight the offending
control.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.jobs import JobCreatePayload
from app.schemas.models import ModelCapabilities


@dataclass(frozen=True)
class ValidationFailure:
    """Detail packet for a rejected parameter.

    The route handler maps this to ``api_error(422, "INVALID_PARAMETER",
    message, field=field)``.
    """

    field: str
    message: str


def validate_against_capabilities(
    payload: JobCreatePayload,
    caps: ModelCapabilities,
    *,
    reference_count: int,
) -> ValidationFailure | None:
    """Check ``payload`` against the merged capability surface.

    Returns ``None`` on success, or the first :class:`ValidationFailure`
    encountered. We stop on first failure because the frontend renders
    field-by-field — pointing at one control at a time is the right UX,
    and a single 422 is enough to abort the request.

    Order of checks roughly follows the design doc §5.1 table so a user
    reading the doc finds the same ordering they'd see in tests.
    """
    # 1. n_max.
    if caps.n_max is not None and payload.n > caps.n_max:
        return ValidationFailure(
            field="n",
            message=(
                f"n={payload.n} exceeds the maximum {caps.n_max} allowed "
                f"for model {payload.model!r}."
            ),
        )

    # 2. List-valued discrete choices. Each pair: payload field + cap field.
    list_pairs: tuple[tuple[str, str | None, list[str] | None], ...] = (
        ("size", payload.size, caps.size),
        ("aspect_ratio", payload.aspect_ratio, caps.aspect_ratio),
        ("image_size", payload.image_size, caps.image_size),
        ("quality", payload.quality, caps.quality),
        ("output_format", payload.output_format, caps.output_format),
        ("background", payload.background, caps.background),
        ("moderation", payload.moderation, caps.moderation),
        ("thinking_level", payload.thinking_level, caps.thinking_level),
    )
    for field, value, allowed in list_pairs:
        if value is None:
            continue
        if allowed is None:
            # Capability doesn't expose this control → user must not set it.
            return ValidationFailure(
                field=field,
                message=(
                    f"{field}={value!r} is not allowed for model "
                    f"{payload.model!r}."
                ),
            )
        if value not in allowed:
            return ValidationFailure(
                field=field,
                message=(
                    f"{field}={value!r} is not in the allowed set "
                    f"{sorted(allowed)} for model {payload.model!r}."
                ),
            )

    # 3. partial_images upper bound — only meaningful with stream=true.
    if payload.partial_images:
        if caps.partial_images_max is None:
            return ValidationFailure(
                field="partial_images",
                message=f"partial_images is not supported for model "
                f"{payload.model!r}.",
            )
        if payload.partial_images > caps.partial_images_max:
            return ValidationFailure(
                field="partial_images",
                message=(
                    f"partial_images={payload.partial_images} exceeds "
                    f"the maximum {caps.partial_images_max}."
                ),
            )
        # OpenAI requires stream=true alongside partial_images; reject
        # before we burn a queue slot rather than letting the adapter
        # 422 us later.
        if not payload.stream:
            return ValidationFailure(
                field="partial_images",
                message="partial_images requires stream=true.",
            )

    # 4. Boolean opt-ins. If the cap is missing or False, refuse a True
    #    request. Stream gets the same treatment — design doc lists it
    #    as a boolean capability.
    bool_pairs: tuple[tuple[str, bool, bool | None], ...] = (
        ("stream", payload.stream, caps.stream),
        ("include_thoughts", payload.include_thoughts, caps.include_thoughts),
        ("google_search", payload.google_search, caps.google_search),
        ("image_search", payload.image_search, caps.image_search),
    )
    for field, value, allowed in bool_pairs:
        if value and not allowed:
            return ValidationFailure(
                field=field,
                message=(
                    f"{field}=true is not allowed for model "
                    f"{payload.model!r}."
                ),
            )

    # 5. References cap.
    if (
        caps.max_reference_images is not None
        and reference_count > caps.max_reference_images
    ):
        return ValidationFailure(
            field="references",
            message=(
                f"{reference_count} reference image(s) exceed the maximum "
                f"{caps.max_reference_images} for model {payload.model!r}."
            ),
        )

    # 6. Prompt length cap.
    if (
        caps.max_prompt_chars is not None
        and len(payload.prompt) > caps.max_prompt_chars
    ):
        return ValidationFailure(
            field="prompt",
            message=(
                f"prompt length {len(payload.prompt)} exceeds the maximum "
                f"{caps.max_prompt_chars} for model {payload.model!r}."
            ),
        )

    # 7. output_compression sanity: only legitimate alongside jpeg/webp.
    if payload.output_compression is not None and payload.output_format not in (
        "jpeg",
        "webp",
    ):
        return ValidationFailure(
            field="output_compression",
            message=(
                "output_compression requires output_format to be jpeg or webp."
            ),
        )

    # 8. background='transparent' requires explicit cap support.
    if payload.background == "transparent" and not caps.supports_transparent_bg:
        return ValidationFailure(
            field="background",
            message=(
                "transparent background is not supported for model "
                f"{payload.model!r}."
            ),
        )

    return None


__all__ = ("ValidationFailure", "validate_against_capabilities")
