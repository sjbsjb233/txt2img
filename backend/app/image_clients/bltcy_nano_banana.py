"""bltcy.ai relay client for Google's Nano Banana / Gemini image models.

Uses the **native** Gemini REST shape::

    POST {base_url}/v1beta/models/{model}:generateContent

Verified upstream behavior on bltcy:

- ``gemini-2.5-flash-image`` — honors ``aspectRatio``; **ignores** ``imageSize``
  (output stays around a 1024-pixel long edge). Returns PNG.
- ``gemini-3-pro-image-preview`` — honors both ``aspectRatio`` and ``imageSize``
  (1K / 2K / 4K). Returns JPEG. May deliver dimensions slightly larger than
  the requested ``imageSize``.
- ``gemini-3.1-flash-image-preview`` — same control surface as Pro, JPEG output.

The relay accepts OpenAI-style ``Authorization: Bearer sk-...`` headers (the
keys it issues are ``sk-*``-shaped), so we use that by default. The native
``x-goog-api-key`` and ``?key=`` styles are also supported for portability.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

import httpx

DEFAULT_BASE_URL = "https://api.bltcy.ai"
DEFAULT_MODEL = "gemini-3-pro-image-preview"

NanoBananaModel = Literal[
    "gemini-2.5-flash-image",
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
]

AspectRatio = Literal[
    "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
    "21:9", "9:21", "4:5", "5:4", "1:4", "4:1", "1:8", "8:1",
]

ImageSize = Literal["512", "1K", "2K", "4K"]

AuthStyle = Literal["bearer", "x-goog", "query"]


@dataclass
class NanoBananaResult:
    image_bytes: bytes
    mime_type: str
    text: str | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    raw: dict = field(default_factory=dict)


class BltcyNanoBananaError(RuntimeError):
    def __init__(self, status: int, message: str, payload: dict | str | None = None):
        super().__init__(f"bltcy nanobanana error {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class BltcyNanoBananaClient:
    """Async client for Nano Banana image generation/editing via bltcy.ai."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        default_model: NanoBananaModel = DEFAULT_MODEL,
        auth_style: AuthStyle = "bearer",
        timeout: float = 600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._auth_style = auth_style
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "BltcyNanoBananaClient":
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
        model: NanoBananaModel | None = None,
        aspect_ratio: AspectRatio | None = None,
        image_size: ImageSize | None = None,
    ) -> NanoBananaResult:
        """Text-to-image. ``image_size`` is honored only by the Pro / 3.1-flash
        models; ``gemini-2.5-flash-image`` ignores it."""
        return await self._call(
            prompt=prompt,
            model=model or self._default_model,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            input_images=None,
        )

    async def edit(
        self,
        prompt: str,
        input_images: Sequence[tuple[str, bytes]],
        *,
        model: NanoBananaModel | None = None,
        aspect_ratio: AspectRatio | None = None,
        image_size: ImageSize | None = None,
    ) -> NanoBananaResult:
        """Multi-modal edit: input image(s) + prompt -> new image.

        ``input_images`` is a sequence of ``(mime_type, raw_bytes)`` tuples.
        """
        if not input_images:
            raise ValueError("edit() requires at least one input image")
        return await self._call(
            prompt=prompt,
            model=model or self._default_model,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            input_images=list(input_images),
        )

    # ---- Internals ---------------------------------------------------------

    async def _call(
        self,
        *,
        prompt: str,
        model: NanoBananaModel,
        aspect_ratio: AspectRatio | None,
        image_size: ImageSize | None,
        input_images: Iterable[tuple[str, bytes]] | None,
    ) -> NanoBananaResult:
        url = f"{self._base_url}/v1beta/models/{model}:generateContent"
        parts: list[dict] = [{"text": prompt}]
        for mime, raw in input_images or []:
            parts.append({
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(raw).decode("ascii"),
                }
            })

        body: dict = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        image_config: dict = {}
        if aspect_ratio is not None:
            image_config["aspectRatio"] = aspect_ratio
        if image_size is not None:
            image_config["imageSize"] = image_size
        if image_config:
            body["generationConfig"]["imageConfig"] = image_config

        headers = {"Content-Type": "application/json"}
        params: dict = {}
        if self._auth_style == "bearer":
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif self._auth_style == "x-goog":
            headers["x-goog-api-key"] = self._api_key
        elif self._auth_style == "query":
            params["key"] = self._api_key

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_temp = self._client is None
        try:
            resp = await client.post(url, headers=headers, params=params, json=body)
        finally:
            if owns_temp:
                await client.aclose()

        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw_text": resp.text}

        if resp.status_code >= 400:
            err = (payload.get("error") if isinstance(payload, dict) else None) or {}
            raise BltcyNanoBananaError(
                resp.status_code,
                err.get("message") or str(payload)[:300],
                payload,
            )

        image_bytes, meta = _extract_first_image(payload)
        if image_bytes is None:
            raise BltcyNanoBananaError(
                resp.status_code,
                "response contained no inline_data image part",
                payload,
            )
        return NanoBananaResult(
            image_bytes=image_bytes,
            mime_type=meta.get("mime", "image/png"),
            text=meta.get("text"),
            finish_reason=meta.get("finish_reason"),
            usage=payload.get("usageMetadata") if isinstance(payload, dict) else None,
            raw=payload if isinstance(payload, dict) else {},
        )


def _extract_first_image(payload: dict) -> tuple[bytes | None, dict]:
    meta: dict = {}
    candidates = payload.get("candidates") or []
    if not candidates:
        return None, meta
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_chunks: list[str] = []
    for part in parts:
        inline = part.get("inline_data") or part.get("inlineData")
        if inline and inline.get("data"):
            mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
            try:
                data = base64.b64decode(inline["data"])
            except Exception:  # noqa: BLE001
                continue
            meta["mime"] = mime
            meta["text"] = "\n".join(text_chunks) if text_chunks else None
            meta["finish_reason"] = candidates[0].get("finishReason")
            return data, meta
        if "text" in part:
            text_chunks.append(part["text"])
    meta["text"] = "\n".join(text_chunks) if text_chunks else None
    meta["finish_reason"] = candidates[0].get("finishReason")
    return None, meta
