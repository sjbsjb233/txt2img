"""Gemini ``v1beta`` ``:generateContent`` adapter.

Targets Google's official Gemini image-generation protocol and any relay
that speaks the same wire format. Supports both image-preview models in
v1:

- ``gemini-3-pro-image-preview`` (Nano Banana Pro)
- ``gemini-3.1-flash-image-preview`` (Nano Banana 2)
- ``gemini-2.5-flash-image`` (legacy, kept for relays that still expose it)

Endpoint: ``POST {base_url}/models/{model_id}:generateContent``
Auth header: ``x-goog-api-key: <api_key>``

Wire-format notes (design doc §2 / §3 / §5):

- ``responseModalities`` must include ``IMAGE`` — without it the API
  returns text only. We always set ``["TEXT","IMAGE"]`` so the response
  parser can also surface accompanying narration (some relays put the
  image inline, some put a leading text part).
- ``imageSize`` requires capital ``K`` (``1K``, ``2K``, ``4K``). The
  3.1-flash-only ``"512"`` carries no suffix.
- 3.1 Flash exposes ``thinkingConfig`` and ``imageSearch``; 3 Pro does
  not. We feature-detect by model id and silently drop 3.1-only fields
  when targeting 3 Pro — the executor's parameter check is the
  authoritative gate; we only defend against bad routing here.
- Reference images are inline_data parts in ``contents.parts`` ordered
  strictly by ``order`` ascending (design doc §5.2 / §6.5).
- Each candidate part may carry ``thought_signature``; we collect them
  into ``NormalizedResponse.metadata['thought_signatures']`` so future
  multi-turn flows can echo them back.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.adapters.base import BaseAdapter
from app.schemas.normalized import (
    NormalizedImage,
    NormalizedRequest,
    NormalizedResponse,
    ProviderConfig,
    StandardError,
    StandardErrorKind,
)


# Model id → feature set. Anything not in this dict is rejected as
# unsupported. ``flash_31_features`` enables thinkingConfig +
# imageSearch + the extreme aspect ratios + ``imageSize="512"``.
_MODELS: dict[str, dict[str, bool]] = {
    "gemini-3-pro-image-preview": {"flash_31_features": False},
    "gemini-3.1-flash-image-preview": {"flash_31_features": True},
    # Legacy relay model — same wire format, conservative feature set.
    "gemini-2.5-flash-image": {"flash_31_features": False},
}

_ASPECT_RATIOS_PRO = {
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
}
_ASPECT_RATIOS_FLASH_31_EXTRA = {"1:4", "4:1", "1:8", "8:1"}

_IMAGE_SIZES_PRO = {"1K", "2K", "4K"}
_IMAGE_SIZES_FLASH_31_EXTRA = {"512"}

_THINKING_LEVELS = {"minimal", "high"}

_MAX_REFERENCES = 14
_PROMPT_MAX_CHARS = 32_000
_BODY_EXCERPT_CHARS = 800


class GeminiV1BetaAdapter(BaseAdapter):
    adapter_type = "gemini_v1beta"
    display_name = "Gemini v1beta"
    description = (
        "Google's native :generateContent protocol for the Gemini image "
        "preview models. Supports gemini-3-pro-image-preview, "
        "gemini-3.1-flash-image-preview, and gemini-2.5-flash-image."
    )

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    def supported_models(self) -> list[str]:
        return list(_MODELS.keys())

    def _model_features(self, model: str) -> dict[str, bool]:
        try:
            return _MODELS[model]
        except KeyError as exc:
            raise StandardError(
                StandardErrorKind.UNSUPPORTED_MODEL,
                f"model {model!r} is not supported by gemini_v1beta",
                field="model",
            ) from exc

    def _validate(self, request: NormalizedRequest) -> dict[str, bool]:
        """Run pre-flight checks; returns the model feature flags so the
        body builder doesn't have to look them up again.
        """
        features = self._model_features(request.model)

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

        # Gemini emits one image per call (design doc §4.1 / §5.3).
        if request.n != 1:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "gemini_v1beta returns one image per call; n must be 1",
                field="n",
            )

        # Aspect ratio.
        if request.aspect_ratio is not None:
            allowed = set(_ASPECT_RATIOS_PRO)
            if features["flash_31_features"]:
                allowed |= _ASPECT_RATIOS_FLASH_31_EXTRA
            if request.aspect_ratio not in allowed:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"aspect_ratio {request.aspect_ratio!r} not supported "
                    f"by {request.model}",
                    field="aspect_ratio",
                )

        # Image size — case-sensitive; lowercase ``1k`` would be silently
        # accepted by some relays but the design doc forbids it.
        if request.image_size is not None:
            allowed_sizes = set(_IMAGE_SIZES_PRO)
            if features["flash_31_features"]:
                allowed_sizes |= _IMAGE_SIZES_FLASH_31_EXTRA
            if request.image_size not in allowed_sizes:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"image_size {request.image_size!r} not supported by "
                    f"{request.model} (case-sensitive)",
                    field="image_size",
                )

        # thinking_level / include_thoughts: 3.1-flash only.
        if request.thinking_level is not None:
            if not features["flash_31_features"]:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    "thinking_level is only available on 3.1-flash",
                    field="thinking_level",
                )
            if request.thinking_level not in _THINKING_LEVELS:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"thinking_level {request.thinking_level!r} not in "
                    f"{_THINKING_LEVELS}",
                    field="thinking_level",
                )
        if request.include_thoughts and not features["flash_31_features"]:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "include_thoughts is only available on 3.1-flash",
                field="include_thoughts",
            )

        # imageSearch is 3.1-flash only.
        if request.image_search and not features["flash_31_features"]:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "image_search is only available on 3.1-flash",
                field="image_search",
            )

        # OpenAI-only fields must not leak through.
        if request.size is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "size is openai_v1-only; use aspect_ratio + image_size",
                field="size",
            )
        if request.quality is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "quality is openai_v1-only",
                field="quality",
            )
        if request.background is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "background is openai_v1-only",
                field="background",
            )
        if request.moderation is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "moderation is openai_v1-only",
                field="moderation",
            )
        if request.stream:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "streaming is not supported by gemini_v1beta",
                field="stream",
            )
        if request.mask is not None:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                "mask-based editing is not supported by gemini; describe "
                "the region in the prompt instead",
                field="mask",
            )

        # References: at most 14, distinct orders, all >= 1.
        if len(request.references) > _MAX_REFERENCES:
            raise StandardError(
                StandardErrorKind.INVALID_PARAMETER,
                f"references exceed maximum of {_MAX_REFERENCES}",
                field="references",
            )
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

        return features

    # ------------------------------------------------------------------
    # Wire format
    # ------------------------------------------------------------------

    def _build_body(
        self,
        request: NormalizedRequest,
        features: dict[str, bool],
    ) -> dict[str, Any]:
        """Build the JSON body for ``:generateContent``.

        Key invariants:

        - ``contents.parts`` starts with the text prompt, followed by
          ``inline_data`` parts in ``order`` ascending. Some relays
          require the prompt first; sticking to that order avoids surprise
          400s.
        - ``responseModalities`` is always ``["TEXT","IMAGE"]`` per
          design doc §2.4 — without it, image output is suppressed.
        - ``imageConfig``, ``thinkingConfig``, and ``tools`` are only
          included when there's something to put inside them, so relays
          that whitelist fields don't reject empty objects.
        """
        # Parts: text first, then references in ascending order.
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        for ref in sorted(request.references, key=lambda r: r.order):
            # Validate the b64 here so a bad ref surfaces consistently
            # with openai_v1 rather than as an upstream 400.
            try:
                base64.b64decode(ref.data_b64, validate=False)
            except Exception as exc:
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"reference[order={ref.order}] base64 invalid: {exc}",
                    field="references",
                ) from exc
            parts.append(
                {
                    "inline_data": {
                        "mime_type": ref.mime,
                        "data": ref.data_b64,
                    }
                }
            )

        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }

        image_config: dict[str, Any] = {}
        if request.aspect_ratio is not None:
            image_config["aspectRatio"] = request.aspect_ratio
        if request.image_size is not None:
            image_config["imageSize"] = request.image_size
        if image_config:
            body["generationConfig"]["imageConfig"] = image_config

        if features["flash_31_features"]:
            thinking: dict[str, Any] = {}
            if request.thinking_level is not None:
                thinking["thinkingLevel"] = request.thinking_level
            if request.include_thoughts:
                thinking["includeThoughts"] = True
            if thinking:
                body["generationConfig"]["thinkingConfig"] = thinking

        # Tools: googleSearch is shared across both models; imageSearch is
        # 3.1-flash only and rides inside ``searchTypes``.
        if request.google_search or (
            features["flash_31_features"] and request.image_search
        ):
            if features["flash_31_features"] and request.image_search:
                # 3.1-flash uses the searchTypes selector.
                search_types: dict[str, Any] = {}
                if request.google_search:
                    search_types["webSearch"] = {}
                if request.image_search:
                    search_types["imageSearch"] = {}
                body["tools"] = [
                    {"google_search": {"searchTypes": search_types}}
                ]
            else:
                body["tools"] = [{"google_search": {}}]

        return body

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        features = self._validate(request)
        body = self._build_body(request, features)

        headers = {
            "x-goog-api-key": provider.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        base = provider.base_url.rstrip("/")
        url = f"{base}/models/{request.model}:generateContent"

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    content=json.dumps(body).encode("utf-8"),
                )
        except StandardError:
            raise
        except Exception as exc:
            raise self.normalize_error(exc) from exc

        return self._parse_response(resp)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, resp: httpx.Response) -> NormalizedResponse:
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

        if not isinstance(body, dict):
            raise StandardError(
                StandardErrorKind.UPSTREAM_ERROR,
                "upstream JSON is not an object",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise StandardError(
                StandardErrorKind.EMPTY_RESPONSE,
                "upstream returned no candidates",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        images: list[NormalizedImage] = []
        text_parts: list[str] = []
        signatures: list[str] = []
        grounding_metadata: dict[str, Any] | None = None

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if grounding_metadata is None and isinstance(
                candidate.get("groundingMetadata"), dict
            ):
                grounding_metadata = candidate["groundingMetadata"]
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                # Thought parts (3.1-flash) carry text or image but no
                # signature; surface them in metadata if includeThoughts
                # was requested. Don't count thought images as outputs.
                is_thought = bool(part.get("thought"))

                if isinstance(part.get("text"), str) and not is_thought:
                    text_parts.append(part["text"])

                inline = part.get("inline_data")
                # Some relays use ``inlineData`` (camelCase) — accept both.
                if inline is None:
                    inline = part.get("inlineData")
                if isinstance(inline, dict) and not is_thought:
                    b64 = inline.get("data")
                    mime = inline.get("mime_type") or inline.get("mimeType")
                    if isinstance(b64, str) and isinstance(mime, str):
                        try:
                            image_bytes = base64.b64decode(b64, validate=False)
                        except Exception:
                            image_bytes = b""
                        if image_bytes:
                            image_meta: dict[str, Any] = {}
                            sig = part.get("thought_signature") or part.get(
                                "thoughtSignature"
                            )
                            if isinstance(sig, str):
                                image_meta["thought_signature"] = sig
                                signatures.append(sig)
                            images.append(
                                NormalizedImage(
                                    data=image_bytes,
                                    mime=mime,
                                    metadata=image_meta,
                                )
                            )

        if not images:
            # Upstream answered text-only. The caller treats this as a
            # soft failure (per design doc §8.7); we surface a typed
            # empty-response error instead of a successful empty payload
            # so the executor records it on the metrics engine.
            raise StandardError(
                StandardErrorKind.EMPTY_RESPONSE,
                "upstream succeeded but returned no inline images",
                upstream_status=resp.status_code,
                upstream_body_excerpt=str(body)[:_BODY_EXCERPT_CHARS],
            )

        response_meta: dict[str, Any] = {}
        if signatures:
            response_meta["thought_signatures"] = signatures
        if grounding_metadata is not None:
            response_meta["grounding_metadata"] = grounding_metadata

        # Truncated raw payload for the per-attempt debug log. Strip the
        # base64 image data so the file stays small.
        redacted_raw = _redact_gemini_payload(body)

        return NormalizedResponse(
            images=images,
            image_count=len(images),
            text="\n".join(text_parts) if text_parts else None,
            metadata=response_meta,
            raw=redacted_raw,
        )

    def _raise_http_error(self, resp: httpx.Response) -> None:
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
            raise StandardError(
                StandardErrorKind.AUTH,
                f"upstream auth failure: {message}",
                upstream_status=resp.status_code,
                upstream_body_excerpt=excerpt,
            )
        if resp.status_code == 429:
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
        raise StandardError(
            StandardErrorKind.UPSTREAM_ERROR,
            f"upstream {resp.status_code}: {message}",
            upstream_status=resp.status_code,
            upstream_body_excerpt=excerpt,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_gemini_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the upstream body with image data summarised.

    We replace the binary blobs with their length so the per-attempt log
    is small enough to keep around indefinitely.
    """
    candidates = body.get("candidates")
    if not isinstance(candidates, list):
        return {"raw": "non-standard"}

    redacted_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            redacted_candidates.append({"raw": "non-dict"})
            continue
        content = candidate.get("content")
        redacted_parts: list[dict[str, Any]] = []
        if isinstance(content, dict):
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if "text" in part:
                    redacted_parts.append({"text_len": len(part.get("text") or "")})
                inline = part.get("inline_data") or part.get("inlineData")
                if isinstance(inline, dict):
                    redacted_parts.append(
                        {
                            "inline_data": {
                                "mime_type": inline.get("mime_type")
                                or inline.get("mimeType"),
                                "data_len": len(inline.get("data") or ""),
                            }
                        }
                    )
        redacted_candidates.append(
            {
                "parts": redacted_parts,
                "groundingMetadata": candidate.get("groundingMetadata"),
                "finishReason": candidate.get("finishReason"),
            }
        )

    return {"candidates": redacted_candidates}
