"""OpenAI ``/v1/images`` style adapter (``openai_v1``).

Targets the official OpenAI image API and any relay that mirrors it.
The single supported model in v1 is ``gpt-image-2`` (snapshot
``gpt-image-2-2026-04-21``); other model ids that ride the same wire
format can be added via the ``_SUPPORTED_MODELS`` set without changing
any of the request-construction logic.

Two endpoints, picked automatically:

- ``POST {base_url}/images/generations`` for plain text-to-image
  requests (no references, no mask).
- ``POST {base_url}/images/edits`` when ``request.references`` or
  ``request.mask`` is present. References go up as ``image[]`` (or just
  ``image`` when there's a single one), strictly in ``order`` ascending
  per design doc §6.5.

Authentication header: ``Authorization: Bearer <api_key>``.

Quirks codified here (design doc §1.4):

- ``background='transparent'`` is rejected with INVALID_PARAMETER —
  gpt-image-2 specifically does not support transparency, sending it
  would be an upstream 4xx.
- ``input_fidelity`` is not exposed: gpt-image-2 always treats inputs at
  high fidelity. We never put it on the wire.
- ``output_compression`` is only sent when ``output_format`` is jpeg/webp
  to avoid surprising 400s from OpenAI.
- ``moderation`` is sent as the OpenAI top-level ``moderation`` field.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.adapters.base import BaseAdapter
from app.schemas.models import ModelUIField
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)
from app.schemas.provider import (
    CapabilityField,
    CapabilityFieldBool,
    CapabilityFieldInt,
    CapabilityFieldList,
)


_SUPPORTED_MODELS: tuple[str, ...] = (
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
)

# Allowed values for OpenAI-side parameters. We mirror the model spec from
# the design doc so a malformed ``NormalizedRequest`` is rejected before
# we burn an upstream call.
_ALLOWED_BACKGROUND = {"auto", "opaque"}
_ALLOWED_OUTPUT_FORMAT = {"png", "jpeg", "webp"}
_ALLOWED_MODERATION = {"auto", "low"}
_ALLOWED_QUALITY = {"low", "medium", "high", "auto"}
_ALLOWED_SIZE_PRESETS = {"1024x1024", "1536x1024", "1024x1536", "auto"}
_ALLOWED_THINKING = {"off", "low", "medium", "high"}

# How many characters of an upstream response body to keep around for
# debug logs. Enough to see error.message but not enough to dump base64.
_BODY_EXCERPT_CHARS = 800

_PROMPT_MAX_CHARS = 32_000


# Pure size helpers live in app.utils.size to keep the validator's
# import graph free of adapter-layer dependencies (httpx etc). Re-export
# here for backward compat with existing tests that import them from
# this module — the adapter is the historical home, not the canonical one.
from app.utils.size import parse_custom_size, validate_custom_size  # noqa: E402,F401


logger = logging.getLogger("txt2img.adapter.openai")


def _prompt_fingerprint(prompt: str | None) -> str:
    if not prompt:
        return ""
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()[:12]


class OpenAIV1Adapter(BaseAdapter):
    adapter_type = "openai_v1"
    display_name = "OpenAI Compatible v1"
    description = (
        "OpenAI's official /v1/images/generations and /v1/images/edits "
        "endpoint shape. Drives gpt-image-2 and any relay that mirrors "
        "the same protocol."
    )

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    def supported_models(self) -> list[str]:
        return list(_SUPPORTED_MODELS)

    def capability_schema(self) -> list[CapabilityField]:
        """Fields admin can configure for an openai_v1 provider model.

        Options mirror the ``_ALLOWED_*`` sets above so a UI selection
        cannot produce a value ``_validate`` would reject.
        """
        return [
            CapabilityFieldList(
                k="size",
                options=sorted(_ALLOWED_SIZE_PRESETS),
                help=(
                    "Allowed size presets. Empty list disables the control. "
                    "Toggle ``size_allow_custom`` to additionally let users "
                    "input arbitrary WIDTHxHEIGHT (must satisfy OpenAI's "
                    "16-multiple / 3:1 / 3840-edge / pixel-budget rules)."
                ),
            ),
            CapabilityFieldList(
                k="quality",
                options=sorted(_ALLOWED_QUALITY),
            ),
            CapabilityFieldList(
                k="output_format",
                options=sorted(_ALLOWED_OUTPUT_FORMAT),
            ),
            CapabilityFieldList(
                k="background",
                options=sorted(_ALLOWED_BACKGROUND),
                help=(
                    "gpt-image-2 does not support transparent; toggle "
                    "supports_transparent_bg separately if a relay does."
                ),
            ),
            CapabilityFieldList(
                k="moderation",
                options=sorted(_ALLOWED_MODERATION),
            ),
            CapabilityFieldList(
                k="thinking",
                # Semantic order matches the dial intent (off → high).
                options=["off", "low", "medium", "high"],
                help=(
                    "v2 reasoning dial. Higher = better complex composition, "
                    "longer latency. Empty list disables the control."
                ),
            ),
            CapabilityFieldInt(k="n_max", min=1, max=10),
            CapabilityFieldInt(
                k="n_max_upstream",
                min=1,
                max=10,
                help=(
                    "Maximum n a single upstream call returns. Leave at "
                    "n_max for native multi-image providers (gpt-image-2 "
                    "natively returns n=4). Set strictly below n_max only "
                    "on providers/models that single-shot one image per "
                    "request; the Create page will fan a user pick of "
                    "n=K out into K parallel n=1 calls."
                ),
            ),
            CapabilityFieldInt(k="partial_images_max", min=0, max=3),
            CapabilityFieldInt(k="max_reference_images", min=0, max=16),
            CapabilityFieldInt(
                k="max_prompt_chars",
                min=1,
                max=_PROMPT_MAX_CHARS,
            ),
            CapabilityFieldBool(k="stream"),
            CapabilityFieldBool(k="supports_mask"),
            CapabilityFieldBool(
                k="supports_transparent_bg",
                help=(
                    "gpt-image-2 does not actually support transparent "
                    "background; reserved for future relays."
                ),
            ),
            CapabilityFieldBool(
                k="size_allow_custom",
                help=(
                    "Let users input arbitrary WIDTHxHEIGHT in addition to "
                    "the preset chips above. Backend enforces OpenAI v2's "
                    "16-multiple / 3:1 / max-edge 3840 / 655 360 – "
                    "8 294 400 px rules. Enable only on relays that honour "
                    "custom sizes (e.g. canonical OpenAI)."
                ),
            ),
        ]

    def ui_schema(self, model_id: str) -> list[ModelUIField]:
        """Create-page parameter layout for the gpt-image-2 family.

        Both ``gpt-image-2`` and the pinned snapshot share the same
        panel; if a future snapshot diverges, branch on ``model_id``.

        ``supports_mask`` / ``supports_transparent_bg`` aren't surfaced
        as panel fields — the Create page doesn't currently expose
        reference-image + mask uploads. Add toggles here once that flow
        ships.
        """
        if model_id not in _SUPPORTED_MODELS:
            raise ValueError(f"unsupported model: {model_id!r}")

        return [
            ModelUIField(
                k="n_max",
                value_key="n",
                control="number",
                label="Output count",
                hint="per generation",
                group="primary",
                order=10,
                min=1,
                max=10,
                presets=[1, 2, 4, 8],
            ),
            ModelUIField(
                k="size",
                control="chip-grid",
                label="Size",
                hint="output dimensions",
                group="primary",
                order=20,
                options=sorted(_ALLOWED_SIZE_PRESETS),
            ),
            ModelUIField(
                k="quality",
                control="chip-row",
                label="Quality",
                hint="render fidelity",
                group="advanced",
                order=10,
                # Semantic order, not alphabetical — low → auto reads
                # like a quality dial.
                options=["low", "medium", "high", "auto"],
            ),
            ModelUIField(
                k="output_format",
                control="chip-row",
                label="Output format",
                hint="encoded as",
                group="advanced",
                order=20,
                options=sorted(_ALLOWED_OUTPUT_FORMAT),
            ),
            ModelUIField(
                k="background",
                control="chip-row",
                label="Background",
                group="advanced",
                order=30,
                options=sorted(_ALLOWED_BACKGROUND),
            ),
            ModelUIField(
                k="moderation",
                control="chip-row",
                label="Moderation",
                hint="content filter",
                group="advanced",
                order=40,
                options=sorted(_ALLOWED_MODERATION),
            ),
            ModelUIField(
                k="thinking",
                control="chip-row",
                label="Thinking",
                hint="reasoning depth · v2",
                group="advanced",
                order=50,
                # Semantic order — off → high reads as a dial.
                options=["off", "low", "medium", "high"],
            ),
        ]

    def _validate(self, request: NormalizedRequest) -> None:
        """Raise ``StandardError(INVALID_PARAMETER)`` for anything we
        know upstream will reject or that the design doc forbids.

        Field names returned in ``StandardError.field`` use the same
        ``snake_case`` names as ``NormalizedRequest`` so the executor /
        API layer can surface them to the user without re-mapping.
        """
        if request.model not in _SUPPORTED_MODELS:
            raise StandardError(
                StandardErrorKind.UNSUPPORTED_MODEL,
                f"model {request.model!r} is not supported by openai_v1",
                field="model",
            )

        if not request.prompt or not request.prompt.strip():
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "prompt must be non-empty",
                field="prompt",
            )
        if len(request.prompt) > _PROMPT_MAX_CHARS:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"prompt exceeds {_PROMPT_MAX_CHARS} characters",
                field="prompt",
            )

        if request.n < 1 or request.n > 10:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "n must be in 1..10",
                field="n",
            )

        if request.size is not None and request.size not in _ALLOWED_SIZE_PRESETS:
            # Non-preset value: must satisfy the documented OpenAI v2
            # custom-size contract (16-multiple / max-edge / total-pixels /
            # aspect-ratio). Whether the *provider* even allows custom
            # sizes is a separate decision that lives on
            # ``capabilities.size_allow_custom`` and is enforced in the
            # job validator before the request reaches us. Here we only
            # guard the wire format itself.
            ok, reason = validate_custom_size(request.size)
            if not ok:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"size {request.size!r} is invalid: {reason}",
                    field="size",
                )

        if request.quality is not None and request.quality not in _ALLOWED_QUALITY:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"quality {request.quality!r} is not in {_ALLOWED_QUALITY}",
                field="quality",
            )

        if (
            request.output_format is not None
            and request.output_format not in _ALLOWED_OUTPUT_FORMAT
        ):
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"output_format {request.output_format!r} not supported",
                field="output_format",
            )

        if (
            request.output_compression is not None
            and request.output_format not in {"jpeg", "webp"}
        ):
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "output_compression requires output_format jpeg or webp",
                field="output_compression",
            )

        if request.background is not None:
            if request.background == "transparent":
                # gpt-image-2 specifically rejects transparent (design doc
                # §1.4 / §5.1). Catch it here so we never send it.
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    "gpt-image-2 does not support transparent background",
                    field="background",
                )
            if request.background not in _ALLOWED_BACKGROUND:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"background {request.background!r} not supported",
                    field="background",
                )

        if (
            request.moderation is not None
            and request.moderation not in _ALLOWED_MODERATION
        ):
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"moderation {request.moderation!r} not supported",
                field="moderation",
            )

        if request.thinking is not None and request.thinking not in _ALLOWED_THINKING:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"thinking {request.thinking!r} not supported "
                f"(must be one of {sorted(_ALLOWED_THINKING)})",
                field="thinking",
            )

        if request.partial_images and not request.stream:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "partial_images requires stream=true",
                field="partial_images",
            )

        # Gemini-only fields must not leak through.
        if request.aspect_ratio is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "aspect_ratio is not supported by gpt-image-2; use size instead",
                field="aspect_ratio",
            )
        if request.image_size is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "image_size is not supported by gpt-image-2; use size instead",
                field="image_size",
            )
        if request.thinking_level is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "thinking_level is gemini-only",
                field="thinking_level",
            )
        if request.google_search or request.image_search:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "google_search / image_search are gemini-only",
                field="google_search",
            )

        # Reference order must be unique and sequential 1..N. Adapters
        # don't reorder, so a duplicate or zero-indexed ``order`` would
        # silently corrupt the multipart layout.
        seen: set[int] = set()
        for ref in request.references:
            if ref.order < 1:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    "reference.order must be >= 1",
                    field="references",
                )
            if ref.order in seen:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"duplicate reference.order={ref.order}",
                    field="references",
                )
            seen.add(ref.order)

    # ------------------------------------------------------------------
    # Wire format
    # ------------------------------------------------------------------

    def _is_edits_request(self, request: NormalizedRequest) -> bool:
        return bool(request.references) or request.mask is not None

    def _build_generations_body(self, request: NormalizedRequest) -> dict[str, Any]:
        """Construct the JSON body for ``/images/generations``.

        Only fields the caller actually set go on the wire; this keeps
        relays that whitelist parameters happy and avoids sending defaults
        like ``"auto"`` we don't have an explicit opinion on.
        """
        body: dict[str, Any] = {"model": request.model, "prompt": request.prompt}
        if request.n != 1:
            body["n"] = request.n
        if request.size is not None:
            body["size"] = request.size
        if request.quality is not None:
            body["quality"] = request.quality
        if request.output_format is not None:
            body["output_format"] = request.output_format
        if (
            request.output_compression is not None
            and request.output_format in {"jpeg", "webp"}
        ):
            body["output_compression"] = request.output_compression
        if request.background is not None:
            body["background"] = request.background
        if request.moderation is not None:
            body["moderation"] = request.moderation
        if request.thinking is not None:
            body["thinking"] = request.thinking
        if request.stream:
            body["stream"] = True
            if request.partial_images:
                body["partial_images"] = request.partial_images
        if request.user is not None:
            body["user"] = request.user
        return body

    def _build_edits_multipart(
        self, request: NormalizedRequest
    ) -> tuple[list[tuple[str, Any]], dict[str, str]]:
        """Construct the multipart payload + form fields for /images/edits.

        Returns ``(files, data)`` suitable for ``httpx.AsyncClient.post``.

        Reference images are emitted in ascending ``order``. The edits
        endpoint accepts either a single ``image`` field or repeated
        ``image[]`` fields when there are multiple references.
        """
        # Ascending by order. design doc §5.2 / §6.5.
        sorted_refs = sorted(request.references, key=lambda r: r.order)

        files: list[tuple[str, Any]] = []
        if request.mask is not None:
            # Mask edit mode — OpenAI's /v1/images/edits accepts mask
            # ONLY paired with the singular ``image`` field, never
            # ``image[]`` (which is the multi-image fusion mode and
            # silently drops any mask). The first reference is the
            # canvas being edited; any additional references are
            # currently ignored on this code path because OpenAI's
            # mask-edit contract is single-image.
            primary = sorted_refs[0]
            files.append(
                (
                    "image",
                    (
                        primary.filename or f"ref_{primary.order:02d}",
                        _decode_b64(primary),
                        primary.mime,
                    ),
                )
            )
        elif len(sorted_refs) == 1:
            # Plain image-to-image: single reference, no mask.
            ref = sorted_refs[0]
            files.append(
                (
                    "image",
                    (ref.filename or f"ref_{ref.order:02d}", _decode_b64(ref), ref.mime),
                )
            )
        else:
            # Multi-image fusion: repeated ``image[]`` per OpenAI docs.
            for ref in sorted_refs:
                files.append(
                    (
                        "image[]",
                        (
                            ref.filename or f"ref_{ref.order:02d}",
                            _decode_b64(ref),
                            ref.mime,
                        ),
                    )
                )

        if request.mask is not None:
            # Standard OpenAI /v1/images/edits contract: separate
            # ``mask`` form field. PNG with alpha channel where
            # alpha=0 indicates the region to edit.
            files.append(
                (
                    "mask",
                    (
                        request.mask.filename or "mask.png",
                        _decode_b64(request.mask),
                        request.mask.mime,
                    ),
                )
            )

        # Multipart fields are strings; numeric values are str()ed.
        data: dict[str, str] = {
            "model": request.model,
            "prompt": request.prompt,
        }
        if request.n != 1:
            data["n"] = str(request.n)
        if request.size is not None:
            data["size"] = request.size
        if request.quality is not None:
            data["quality"] = request.quality
        if request.output_format is not None:
            data["output_format"] = request.output_format
        if (
            request.output_compression is not None
            and request.output_format in {"jpeg", "webp"}
        ):
            data["output_compression"] = str(request.output_compression)
        if request.background is not None:
            data["background"] = request.background
        if request.moderation is not None:
            data["moderation"] = request.moderation
        if request.thinking is not None:
            data["thinking"] = request.thinking
        if request.user is not None:
            data["user"] = request.user
        return files, data

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        self._validate(request)

        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Accept": "application/json",
        }
        base = provider.base_url.rstrip("/")
        is_edits = self._is_edits_request(request)
        endpoint = "/images/edits" if is_edits else "/images/generations"
        prompt_hash = _prompt_fingerprint(request.prompt)
        prompt_len = len(request.prompt or "")
        n_refs = len(request.references or [])
        logger.info(
            "upstream call: provider=%s model=%s endpoint=%s n=%d size=%s "
            "prompt_hash=%s prompt_len=%d n_refs=%d has_mask=%s",
            provider.id,
            request.model,
            endpoint,
            request.n,
            request.size,
            prompt_hash,
            prompt_len,
            n_refs,
            request.mask is not None,
        )

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                if is_edits:
                    files, data = self._build_edits_multipart(request)
                    resp = await client.post(
                        f"{base}{endpoint}",
                        headers=headers,
                        files=files,
                        data=data,
                    )
                else:
                    body = self._build_generations_body(request)
                    resp = await client.post(
                        f"{base}{endpoint}",
                        headers={**headers, "Content-Type": "application/json"},
                        content=json.dumps(body).encode("utf-8"),
                    )
                latency_ms = int((time.perf_counter() - t0) * 1000)
                logger.info(
                    "upstream response: provider=%s model=%s status=%d "
                    "latency_ms=%d bytes=%d",
                    provider.id,
                    request.model,
                    resp.status_code,
                    latency_ms,
                    len(resp.content or b""),
                )
                # The same client is reused to fetch any ``url``-format
                # items the upstream returned (BLTCY-style relays do
                # this), so we don't pay TLS-handshake twice.
                return await self._parse_response(
                    resp,
                    request.output_format or "png",
                    client,
                )
        except StandardError as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "upstream business error: provider=%s model=%s kind=%s "
                "status=%s latency_ms=%d msg=%s",
                provider.id,
                request.model,
                getattr(exc, "kind", None),
                getattr(exc, "upstream_status", None),
                latency_ms,
                str(exc)[:200],
            )
            raise
        except httpx.TimeoutException as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "upstream timeout: provider=%s model=%s latency_ms=%d exc=%r",
                provider.id,
                request.model,
                latency_ms,
                exc,
            )
            raise self.normalize_error(exc) from exc
        except httpx.TransportError as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "upstream transport: provider=%s model=%s latency_ms=%d exc=%r",
                provider.id,
                request.model,
                latency_ms,
                exc,
            )
            raise self.normalize_error(exc) from exc
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception(
                "upstream unexpected: provider=%s model=%s latency_ms=%d",
                provider.id,
                request.model,
                latency_ms,
            )
            raise self.normalize_error(exc) from exc

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    async def _resolve_item(
        self,
        item: Any,
        client: httpx.AsyncClient,
        requested_format: str,
        sem: asyncio.Semaphore,
    ) -> NormalizedImage | None:
        """Decode one ``data[]`` item into a ``NormalizedImage`` or skip it.

        Tries inline ``b64_json`` first; falls back to fetching ``url`` if
        present. The CDN GET runs under ``sem`` so multi-image responses
        download concurrently without overwhelming the relay.

        Returns ``None`` when the item is neither decodable nor fetchable
        — the executor's partial-failure semantics handle that upstream
        of us.
        """
        if not isinstance(item, dict):
            return None

        image_bytes: bytes | None = None
        item_mime: str | None = None

        b64 = item.get("b64_json")
        if isinstance(b64, str) and b64:
            try:
                image_bytes = base64.b64decode(b64, validate=False)
            except Exception:
                image_bytes = None

        # Fallback to ``url``: the standard OpenAI response format when
        # ``response_format`` isn't pinned to ``b64_json``. Relays such
        # as BLTCY always return URL even for gpt-image-2.
        if image_bytes is None:
            url = item.get("url")
            if isinstance(url, str) and url:
                if not await _is_safe_url(url):
                    # Defense in depth against a relay returning an
                    # internal-network URL — refuse to fetch.
                    return None
                async with sem:
                    try:
                        dl = await client.get(url)
                    except Exception:
                        dl = None
                if dl is not None and dl.status_code < 400 and dl.content:
                    ctype = dl.headers.get("content-type", "")
                    ctype_main = ctype.split(";")[0].strip().lower()
                    # If Content-Type is set and isn't an image, treat
                    # the download as a failure rather than persisting
                    # an HTML error page as if it were a PNG.
                    if ctype_main and not ctype_main.startswith("image/"):
                        return None
                    image_bytes = dl.content
                    if ctype_main.startswith("image/"):
                        item_mime = ctype_main

        if image_bytes is None:
            logger.warning(
                "upstream item undecodable: format=%s has_b64=%s has_url=%s",
                requested_format,
                isinstance(item.get("b64_json"), str),
                isinstance(item.get("url"), str),
            )
            return None

        image_meta: dict[str, Any] = {}
        if isinstance(item.get("revised_prompt"), str):
            image_meta["revised_prompt"] = item["revised_prompt"]
        if isinstance(item.get("url"), str):
            # Keep the relay's CDN URL around for the per-attempt log
            # (handy when debugging "why did this image expire").
            # Strip query/fragment so signed-token URLs don't leak.
            image_meta["url"] = _sanitize_url_for_log(item["url"])

        return NormalizedImage(
            data=image_bytes,
            mime=item_mime or _mime_for_format(requested_format),
            metadata=image_meta,
        )

    async def _parse_response(
        self,
        resp: httpx.Response,
        requested_format: str,
        client: httpx.AsyncClient,
    ) -> NormalizedResponse:
        """Translate the upstream HTTP response into our normalized shape.

        We treat anything below 200 or ≥ 300 as an error and let
        ``_raise_http_error`` build a typed ``StandardError`` from the
        payload. On 2xx OpenAI's API can return either of two shapes per
        item — both are accepted::

            {"data": [{"b64_json": "...", "revised_prompt": "..."}]}
            {"data": [{"url": "https://...", "revised_prompt": "..."}]}

        Standalone OpenAI returns ``b64_json`` for gpt-image-2; relays
        like BLTCY proxy the image to their CDN and return ``url``. We
        support both: ``url`` items get GET'ed via ``client`` (sharing the
        connection pool the request was made on), and the resulting bytes
        are stored just like a base64-decoded item.

        ``requested_format`` is plumbed in because OpenAI's response items
        do not echo the format back; the request-time choice is what the
        bytes actually are when they arrive over the wire.
        """
        if resp.status_code >= 400:
            self._raise_http_error(resp)

        try:
            body = resp.json()
        except ValueError as exc:
            raise StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                "upstream returned non-JSON 2xx response",
                upstream_status=resp.status_code,
                upstream_body_excerpt=resp.text[:_BODY_EXCERPT_CHARS],
            ) from exc

        if not isinstance(body, dict) or "data" not in body:
            raise StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                "upstream JSON missing 'data' field",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        data = body.get("data")
        if not isinstance(data, list):
            raise StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                "upstream 'data' is not a list",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        # Each item resolves independently. b64-only items finish
        # synchronously; url-only items kick off a CDN GET. We dispatch
        # them concurrently with a small semaphore so a response with
        # n=10 doesn't take 10× a single download's latency.
        sem = asyncio.Semaphore(_MAX_DOWNLOAD_CONCURRENCY)
        tasks = [
            self._resolve_item(item, client, requested_format, sem)
            for item in data
        ]
        resolved = await asyncio.gather(*tasks)
        images: list[NormalizedImage] = [img for img in resolved if img is not None]

        if not images:
            raise StandardError(
                StandardErrorKind.EMPTY_RESPONSE,
                "upstream succeeded but returned no decodable images",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        # ``raw`` is for the per-attempt debug log. We strip the b64
        # blobs so the log doesn't balloon to MB; keep their lengths so
        # we can still reason about them after the fact. URLs are
        # sanitised — the host+path is useful for debugging but signed
        # query tokens have no business being persisted to disk.
        raw: dict[str, Any] = {
            "created": body.get("created"),
            "data": [
                {
                    "b64_json_len": (
                        len(item.get("b64_json") or "")
                        if isinstance(item, dict)
                        else 0
                    ),
                    "url": (
                        _sanitize_url_for_log(item["url"])
                        if isinstance(item, dict)
                        and isinstance(item.get("url"), str)
                        else None
                    ),
                    "revised_prompt": (
                        item.get("revised_prompt")
                        if isinstance(item, dict)
                        else None
                    ),
                }
                for item in data
            ],
        }

        return NormalizedResponse(
            images=images,
            image_count=len(images),
            text=None,
            metadata={},
            raw=raw,
        )

    def _raise_http_error(self, resp: httpx.Response) -> None:
        """Map an upstream non-2xx into a typed ``StandardError``.

        OpenAI's error envelope looks like::

            {"error": {"message": "...", "type": "...", "code": "..."}}
        """
        excerpt = resp.text[:_BODY_EXCERPT_CHARS]
        message = excerpt or f"HTTP {resp.status_code}"
        try:
            payload = resp.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                message = err["message"]
        except ValueError:
            pass

        if resp.status_code in (401, 403):
            logger.warning(
                "upstream auth failure: status=%d msg=%s", resp.status_code, message[:200]
            )
            raise StandardError(
                StandardErrorKind.AUTH,
                f"upstream auth failure: {message}",
                upstream_status=resp.status_code,
                upstream_body_excerpt=excerpt,
            )
        if resp.status_code == 429:
            logger.warning(
                "upstream rate-limited: status=%d msg=%s", resp.status_code, message[:200]
            )
            raise StandardError(
                StandardErrorKind.RATE_LIMITED,
                f"upstream rate-limited: {message}",
                upstream_status=resp.status_code,
                upstream_body_excerpt=excerpt,
            )
        if 400 <= resp.status_code < 500:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"upstream rejected request: {message}",
                upstream_status=resp.status_code,
                upstream_body_excerpt=excerpt,
            )
        # 5xx
        raise StandardError(
            StandardErrorKind.UPSTREAM_ERROR,
            f"upstream {resp.status_code}: {message}",
            upstream_status=resp.status_code,
            upstream_body_excerpt=excerpt,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_b64(ref) -> bytes:  # type: ignore[no-untyped-def]
    """Decode a ``NormalizedReference``'s ``data_b64`` to bytes.

    Wrapped in a helper for symmetric error wrapping with the response
    side. Raises ``StandardError(INVALID_PARAMETER)`` on bad base64
    rather than letting a ``binascii.Error`` escape.
    """
    try:
        return base64.b64decode(ref.data_b64, validate=False)
    except Exception as exc:
        raise StandardError(
            StandardErrorKind.INVALID_PARAMETER,
            f"reference[order={ref.order}] base64 invalid: {exc}",
            field="references",
        ) from exc


def _merge_mask_into_image_alpha(
    image_bytes: bytes, mask_bytes: bytes
) -> bytes:
    """Bake the mask's alpha channel into the source image's alpha.

    OpenAI's ``/v1/images/edits`` documents the mask parameter as:
        "An additional image whose fully transparent areas (e.g. where
         alpha is zero) indicate where image should be edited. ...
         If mask is not provided, image must have transparency, which
         will be used as the mask."

    Empirically, gpt-image-2 reads the alpha of the *image* parameter
    in preference to the separate ``mask`` parameter — sending both
    with non-trivial image alpha makes the model fall back to the
    image's alpha and ignore the mask entirely. Concretely:

    - Source image we receive is RGBA with alpha=255 everywhere
      (because the editor's source canvas is opaque).
    - Mask is RGBA with alpha=0 in the user-painted "edit" region
      and alpha=255 elsewhere.

    The merge replaces the source image's alpha channel with the
    mask's alpha. The result is a single PNG that simultaneously
    represents the source content + the edit region, which gpt-image-2
    interprets correctly.

    We still send the separate ``mask`` parameter from the caller —
    it remains useful for older OpenAI models (gpt-image-1) that
    consult the mask field directly. The two encodings agree, so
    whichever path the upstream model picks lands on the same intent.
    """
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    try:
        src = Image.open(BytesIO(image_bytes)).convert("RGBA")
        mask = Image.open(BytesIO(mask_bytes)).convert("RGBA")
    except (UnidentifiedImageError, OSError):
        # Bytes that PIL can't open — pass them through unchanged and
        # let the upstream return a structured error (or, in tests,
        # exercise the rest of the pipeline with placeholder bytes).
        return image_bytes
    if src.size != mask.size:
        return image_bytes
    r, g, b, _ = src.split()
    _, _, _, mask_a = mask.split()
    merged = Image.merge("RGBA", (r, g, b, mask_a))
    out = BytesIO()
    merged.save(out, format="PNG")
    return out.getvalue()


def _mime_for_format(fmt: str) -> str:
    fmt = fmt.lower()
    if fmt == "jpeg" or fmt == "jpg":
        return "image/jpeg"
    if fmt == "webp":
        return "image/webp"
    return "image/png"


# ---------------------------------------------------------------------------
# URL safety helpers for the relay-CDN download path.
#
# A relay we trust to hold our API key still shouldn't be able to bounce us
# at internal HTTP services if it ever gets compromised. We defend in depth
# by validating the URL before fetching:
#
# - Scheme must be http or https.
# - Hostname (literal IP or resolved) must not match the narrow danger list
#   in :func:`_is_blocked_ip`: loopback, link-local (incl. cloud metadata),
#   multicast, unspecified, 0.0.0.0/8.
#
# RFC1918 (10/8, 172.16/12, 192.168/16) and 198.18.0.0/15 are
# intentionally NOT blocked — they're the IP ranges Mainland China
# transparent-proxy / VPN setups use as fake-IP anchors that route
# through the proxy to a real public CDN. Blocking them would refuse
# legitimate downloads for any user whose local resolver hands those
# back. The API key we already entrust to the relay is the actual
# security boundary; refusing one extra GET buys us little against an
# attacker who can already see the key.
#
# DNS rebinding is still possible in theory (the IP can change between
# the resolve and the actual fetch); we accept that risk because the
# alternative is breaking the proxy use case above.
# ---------------------------------------------------------------------------


_MAX_DOWNLOAD_CONCURRENCY = 8

# Narrow blocklist — only the IPs that genuinely target an attacker's
# preferred pivots when SSRF'd:
#
#   - 127.0.0.0/8 + IPv6 ::1     loopback to our own server
#   - 169.254.0.0/16 + fe80::/10 link-local (catches AWS/GCP/Azure metadata
#                                at 169.254.169.254 and friends)
#   - 0.0.0.0/8                  unspecified
#   - multicast / reserved       not a valid HTTP target
#
# RFC1918 (10/8, 172.16/12, 192.168/16) and the benchmark range
# 198.18.0.0/15 are intentionally NOT blocked: many legitimate Mainland
# China network setups (transparent proxies, VPN split-DNS, corporate
# proxies) hand out IPs in those ranges as fake-IP anchors that the OS
# routes through the proxy to a real public CDN. Blocking them would
# break image downloads from relays whose CDN hostnames the local
# resolver maps into those ranges, while the API key we already entrust
# to the relay is the actual security boundary.
_RESERVED_IPV4_NETS_BLOCKED = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ``ip`` is one of the blocked dangerous ranges."""
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        for net in _RESERVED_IPV4_NETS_BLOCKED:
            if ip in net:
                return True
    return False


async def _is_safe_url(url: str) -> bool:
    """Cheap, defense-in-depth check before GET'ing a relay-supplied URL.

    Resolves the host once via the running loop's ``getaddrinfo`` so we
    don't block the event loop. False on any classification we can't
    verify — refusing to fetch is safer than fetching the wrong thing.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    # Host is already a literal IP — check directly without DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return not _is_blocked_ip(ip)

    # Hostname: resolve via the running loop and check every result.
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if _is_blocked_ip(resolved):
            return False
    return bool(infos)


def _sanitize_url_for_log(url: str) -> str:
    """Drop query / fragment from a URL before persisting it.

    CDN URLs from relays often carry signed-token query strings that we
    do not want sitting in the per-attempt debug log under
    ``data/jobs/<hash>/upstream/``. Stripping query + fragment keeps the
    debug-useful host + path while removing the secret.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
