"""Cloudflare Turnstile siteverify wrapper.

Single responsibility: given the user-supplied ``cf-turnstile-response``
token, ask Cloudflare's siteverify endpoint whether it's valid and return
True/False. Anything else (looking up settings, deciding whether captcha
is required, recording attempts) belongs to the caller in ``app.api.auth``.

Design doc references: §2.3 (login flow), §13.6 (force_captcha_global).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("txt2img.turnstile")

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# 5s is enough for the round-trip in normal conditions; we'd rather time
# out and surface a captcha failure than block the whole login pipeline.
_REQUEST_TIMEOUT_SECONDS = 5.0


async def verify(token: str, *, remote_ip: str | None = None) -> bool:
    """Validate ``token`` against Cloudflare siteverify.

    Returns True only when Cloudflare reports ``success=true``. Any
    transport error, malformed response, or explicit failure reason
    counts as a verification failure — we never default-allow.

    If ``TURNSTILE_SECRET`` is empty (e.g. local dev with captcha disabled)
    we treat any non-empty token as a pass. This is documented in the env
    example so it's not a hidden trapdoor: setups that need real captcha
    must populate the secret.
    """
    settings = get_settings()
    secret = settings.TURNSTILE_SECRET.strip()

    if not token:
        return False

    if not secret:
        # No secret configured — treat captcha as a no-op. Logged at debug
        # so production can verify the secret really is set.
        logger.debug("turnstile secret not configured; accepting token by default")
        return True

    payload: dict[str, Any] = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(SITEVERIFY_URL, data=payload)
    except httpx.HTTPError as exc:
        logger.warning("turnstile siteverify transport failure: %s", exc)
        return False

    if resp.status_code != 200:
        logger.warning(
            "turnstile siteverify non-200 status=%s body=%s",
            resp.status_code,
            resp.text[:200],
        )
        return False

    try:
        body = resp.json()
    except ValueError:
        logger.warning("turnstile siteverify returned non-JSON body")
        return False

    if not isinstance(body, dict):
        return False

    if bool(body.get("success")):
        return True

    # Cloudflare returns ``error-codes: [...]`` on failure; log for ops.
    logger.info(
        "turnstile rejected token error_codes=%s", body.get("error-codes")
    )
    return False
