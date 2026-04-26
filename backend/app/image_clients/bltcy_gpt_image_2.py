"""bltcy.ai relay client for OpenAI ``gpt-image-2``.

Uses the **native** OpenAI REST shape::

    POST {base_url}/v1/images/generations          (JSON body)
    POST {base_url}/v1/images/edits                (multipart/form-data)

Verified upstream behavior on bltcy:

- "Low-tier" keys silently ignore most ``size`` values (everything except
  ``1024x1536``) and quietly fall back to a ``1254x1254`` PNG default.
  ``quality`` has no observable effect.
- "High-tier" keys validate ``size`` strictly; the longest edge must be
  ``<= 3840`` (the upstream returns HTTP 400 otherwise) but generation
  may take long enough to trigger a Cloudflare 524 gateway timeout when
  the upstream is congested.

Both tiers expose an ``images.edits`` endpoint that takes a multipart upload
of one input image (any standard format) plus a text prompt.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

DEFAULT_BASE_URL = "https://api.bltcy.ai"
DEFAULT_MODEL = "gpt-image-2"

GptImageQuality = Literal["auto", "low", "medium", "high"]
GptImageBackground = Literal["auto", "transparent", "opaque"]
GptImageOutputFormat = Literal["png", "jpeg", "webp"]


@dataclass
class GptImageResult:
    image_bytes: bytes
    mime_type: str  # inferred from output_format / png default
    revised_prompt: str | None = None
    usage: dict | None = None
    echoed_size: str | None = None
    echoed_quality: str | None = None
    echoed_format: str | None = None
    raw: dict = field(default_factory=dict)


class BltcyGptImage2Error(RuntimeError):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"bltcy gpt-image-2 error {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class BltcyGptImage2Client:
    """Async client for ``gpt-image-2`` (and OpenAI-compatible siblings) on
    the bltcy.ai relay."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        default_model: str = DEFAULT_MODEL,
        timeout: float = 600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "BltcyGptImage2Client":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- Public API --------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        quality: GptImageQuality | None = None,
        n: int = 1,
        model: str | None = None,
        background: GptImageBackground | None = None,
        output_format: GptImageOutputFormat | None = None,
        output_compression: int | None = None,
        moderation: Literal["auto", "low"] | None = None,
        user: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GptImageResult:
        """Text-to-image via ``POST /v1/images/generations`` (JSON)."""
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "prompt": prompt,
            "n": n,
        }
        if size is not None:
            body["size"] = size
        if quality is not None:
            body["quality"] = quality
        if background is not None:
            body["background"] = background
        if output_format is not None:
            body["output_format"] = output_format
        if output_compression is not None:
            body["output_compression"] = output_compression
        if moderation is not None:
            body["moderation"] = moderation
        if user is not None:
            body["user"] = user
        if extra:
            body.update(extra)

        return await self._post_json("/v1/images/generations", body)

    async def edit(
        self,
        prompt: str,
        image: bytes | Path,
        *,
        image_filename: str = "input.png",
        image_mime: str = "image/png",
        mask: bytes | None = None,
        mask_filename: str = "mask.png",
        size: str | None = None,
        quality: GptImageQuality | None = None,
        n: int = 1,
        model: str | None = None,
        background: GptImageBackground | None = None,
        output_format: GptImageOutputFormat | None = None,
        output_compression: int | None = None,
        input_fidelity: Literal["high", "low"] | None = None,
        user: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GptImageResult:
        """Image edit via ``POST /v1/images/edits`` (multipart/form-data).

        ``image`` may be a file path or raw bytes. Single-image edit only;
        the relay's compatibility with the multi-image ``images[]`` form has
        not been verified.
        """
        if isinstance(image, Path):
            image_bytes = image.read_bytes()
            image_filename = image.name
            if image.suffix.lower() in {".jpg", ".jpeg"}:
                image_mime = "image/jpeg"
            elif image.suffix.lower() == ".webp":
                image_mime = "image/webp"
            else:
                image_mime = "image/png"
        else:
            image_bytes = image

        data: dict[str, Any] = {
            "model": model or self._default_model,
            "prompt": prompt,
            "n": str(n),
        }
        if size is not None:
            data["size"] = size
        if quality is not None:
            data["quality"] = quality
        if background is not None:
            data["background"] = background
        if output_format is not None:
            data["output_format"] = output_format
        if output_compression is not None:
            data["output_compression"] = str(output_compression)
        if input_fidelity is not None:
            data["input_fidelity"] = input_fidelity
        if user is not None:
            data["user"] = user
        if extra:
            for k, v in extra.items():
                data[k] = v if isinstance(v, (str, int, float)) else str(v)

        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("image", (image_filename, image_bytes, image_mime)),
        ]
        if mask is not None:
            files.append(("mask", (mask_filename, mask, "image/png")))

        return await self._post_multipart("/v1/images/edits", data, files)

    # ---- Internals ---------------------------------------------------------

    async def _post_json(self, path: str, body: dict[str, Any]) -> GptImageResult:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_temp = self._client is None
        try:
            resp = await client.post(url, headers=headers, json=body)
        finally:
            if owns_temp:
                await client.aclose()
        return self._parse_response(resp)

    async def _post_multipart(
        self,
        path: str,
        data: dict[str, Any],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> GptImageResult:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_temp = self._client is None
        try:
            resp = await client.post(url, headers=headers, data=data, files=files)
        finally:
            if owns_temp:
                await client.aclose()
        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> GptImageResult:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw_text": resp.text}

        if resp.status_code >= 400:
            err = (payload.get("error") if isinstance(payload, dict) else None) or {}
            raise BltcyGptImage2Error(
                resp.status_code,
                err.get("message") or str(payload)[:300],
                payload,
            )

        if not isinstance(payload, dict):
            raise BltcyGptImage2Error(resp.status_code, "non-JSON success body", payload)
        data = payload.get("data") or []
        if not data:
            raise BltcyGptImage2Error(
                resp.status_code, "response had empty data array", payload,
            )
        first = data[0] if isinstance(data[0], dict) else {}
        b64 = first.get("b64_json")
        url = first.get("url")
        if b64:
            try:
                image_bytes = base64.b64decode(b64)
            except Exception as exc:  # noqa: BLE001
                raise BltcyGptImage2Error(
                    resp.status_code, f"could not decode b64_json: {exc}", payload,
                ) from exc
        elif url:
            # Fall back to fetching the URL synchronously through httpx.
            with httpx.Client(timeout=self._timeout) as fetcher:
                fetched = fetcher.get(url)
            if fetched.status_code >= 400:
                raise BltcyGptImage2Error(
                    fetched.status_code,
                    f"fetched image URL returned {fetched.status_code}",
                    payload,
                )
            image_bytes = fetched.content
        else:
            raise BltcyGptImage2Error(
                resp.status_code, "no b64_json or url in first data entry", payload,
            )

        echoed_format = payload.get("output_format")
        mime_map = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
        mime_type = mime_map.get(echoed_format or "", "image/png")

        return GptImageResult(
            image_bytes=image_bytes,
            mime_type=mime_type,
            revised_prompt=first.get("revised_prompt"),
            usage=payload.get("usage"),
            echoed_size=payload.get("size"),
            echoed_quality=payload.get("quality"),
            echoed_format=echoed_format,
            raw=payload,
        )
