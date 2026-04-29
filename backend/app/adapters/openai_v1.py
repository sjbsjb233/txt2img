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
import ipaddress
import json
from typing import Any
from urllib.parse import urlparse, urlunparse

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

# How many characters of an upstream response body to keep around for
# debug logs. Enough to see error.message but not enough to dump base64.
_BODY_EXCERPT_CHARS = 800

_PROMPT_MAX_CHARS = 32_000


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
            # Custom resolutions are technically allowed by the design doc
            # ("超过即视为实验性 2K") — we forward them as-is rather than
            # block. Only reject obviously malformed strings that aren't
            # WIDTHxHEIGHT either.
            if "x" not in request.size or not all(
                part.isdigit() for part in request.size.split("x", 1)
            ):
                raise StandardError(
                    StandardErrorKind.INVALID_PARAMETER,
                    f"size {request.size!r} is not a recognised value",
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
        if len(sorted_refs) == 1 and request.mask is None:
            ref = sorted_refs[0]
            files.append(
                (
                    "image",
                    (ref.filename or f"ref_{ref.order:02d}", _decode_b64(ref), ref.mime),
                )
            )
        else:
            # Multi-image fusion or single ref + mask: use repeated image[]
            # which OpenAI documents for fusion mode.
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

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                if self._is_edits_request(request):
                    files, data = self._build_edits_multipart(request)
                    resp = await client.post(
                        f"{base}/images/edits",
                        headers=headers,
                        files=files,
                        data=data,
                    )
                else:
                    body = self._build_generations_body(request)
                    resp = await client.post(
                        f"{base}/images/generations",
                        headers={**headers, "Content-Type": "application/json"},
                        content=json.dumps(body).encode("utf-8"),
                    )
                # The same client is reused to fetch any ``url``-format
                # items the upstream returned (BLTCY-style relays do
                # this), so we don't pay TLS-handshake twice.
                return await self._parse_response(
                    resp,
                    request.output_format or "png",
                    client,
                )
        except StandardError:
            raise
        except Exception as exc:
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
