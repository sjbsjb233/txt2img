"""Frontend log ingestion endpoint.

The browser-side logger (``frontend/src/utils/logger.js``) buffers a
handful of records and posts them here every few seconds, plus a final
``sendBeacon`` flush on ``beforeunload``. We want the channel to be
useful but cheap to abuse-proof:

* Auth is *optional* — a user staring at a white-screen during the
  login flow can't reach an authenticated endpoint, and that's the
  exact bug we most want telemetry for.
* Per-user / per-IP token-bucket rate limiting so a runaway tab can't
  spam disk.
* Hard caps on batch size + per-item bytes so a single malicious POST
  can't blow up the logger pipeline.

Records are emitted on ``txt2img.client`` which the logging setup
routes to ``client-error.log`` (plus the regular ``app.log``).
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import ValidationError

from app.config import get_settings
from app.deps import get_auth_context
from app.schemas.client_logs import (
    ClientLogItem,
    ClientLogsRequest,
    ClientLogsResponse,
)
from app.utils.errors import api_error

logger = logging.getLogger("txt2img.client")
api_logger = logging.getLogger("txt2img.api.client_logs")

router = APIRouter(prefix="/api", tags=["client-logs"])


# Token-bucket rate limit state. One bucket per actor; we don't share
# the limiter across processes because the backend ships with a single
# uvicorn worker (see backend/Dockerfile — design doc §1.4).
_USER_HITS: dict[str, deque[float]] = {}
_IP_HITS: dict[str, deque[float]] = {}
_WINDOW_SECONDS = 60.0


def _hit(bucket: dict[str, deque[float]], key: str, limit: int) -> bool:
    """Return True if the actor is *under* the limit, False if over."""
    if limit <= 0:
        return False
    now = time.monotonic()
    q = bucket.setdefault(key, deque())
    cutoff = now - _WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def reset_rate_limit_for_tests() -> None:
    _USER_HITS.clear()
    _IP_HITS.clear()


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _safe_user_id_from_token(
    authorization: str | None,
) -> str | None:
    """Best-effort token decode that never raises.

    The endpoint is open to anonymous callers, so we don't fail on a
    bad / missing token — we just log without a user_id.
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    try:
        from app.utils.security import decode_access_token

        payload = decode_access_token(parts[1].strip())
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except Exception:
        return None


def _level_to_logging(level: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }.get((level or "info").lower(), logging.INFO)


def _validate_item_size(item: ClientLogItem, max_bytes: int) -> bool:
    if max_bytes <= 0:
        return True
    try:
        size = len(json.dumps(item.model_dump(exclude_none=True), default=str))
    except (TypeError, ValueError):
        return False
    return size <= max_bytes


@router.post("/client-logs", response_model=ClientLogsResponse)
async def post_client_logs(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ClientLogsResponse:
    """Accept a batch of frontend-emitted log items."""
    settings = get_settings()
    if not settings.LOG_CLIENT_LOGS_ENABLED:
        raise api_error(
            503,
            "FEATURE_DISABLED",
            "Client log ingestion is disabled.",
        )

    # We parse the body manually so a single bad item doesn't reject the
    # entire batch — the schema lives next door but we want explicit
    # per-item control over the drop count.
    try:
        raw = await request.json()
    except ValueError:
        # ``sendBeacon`` posts with Content-Type "text/plain;charset=UTF-8"
        # by default; try the raw body as a fallback.
        try:
            text = (await request.body()).decode("utf-8")
            raw = json.loads(text) if text else None
        except (ValueError, UnicodeDecodeError):
            raise api_error(400, "BAD_REQUEST", "Body must be JSON.")
    if not isinstance(raw, dict):
        raise api_error(400, "BAD_REQUEST", "Body must be a JSON object.")

    try:
        batch = ClientLogsRequest.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = first.get("loc") or ()
        field = ".".join(str(p) for p in loc) or None
        raise api_error(
            422,
            "INVALID_PARAMETER",
            str(first.get("msg") or "Invalid client log batch."),
            field=field,
        )

    if len(batch.items) > settings.LOG_CLIENT_LOGS_BATCH_MAX:
        raise api_error(
            413,
            "BATCH_TOO_LARGE",
            f"max {settings.LOG_CLIENT_LOGS_BATCH_MAX} items per batch.",
        )

    user_id = _safe_user_id_from_token(authorization)
    ip = _client_ip(request)

    if user_id is not None:
        if not _hit(_USER_HITS, user_id, settings.LOG_CLIENT_LOGS_RATE_LIMIT):
            api_logger.warning(
                "client-logs: per-user rate limit hit user_id=%s", user_id
            )
            raise api_error(
                429,
                "RATE_LIMITED",
                "Too many client log batches.",
            )
    else:
        if ip and not _hit(
            _IP_HITS, ip, settings.LOG_CLIENT_LOGS_RATE_LIMIT_ANON
        ):
            api_logger.warning("client-logs: per-IP rate limit hit ip=%s", ip)
            raise api_error(
                429,
                "RATE_LIMITED",
                "Too many client log batches.",
            )

    accepted = 0
    dropped = 0
    for item in batch.items:
        if not _validate_item_size(item, settings.LOG_CLIENT_LOGS_ITEM_MAX_BYTES):
            dropped += 1
            continue
        level = _level_to_logging(item.level)
        extra = {
            "client_ts": item.ts,
            "route": item.route,
            "build_version": item.build_version,
            "session_id": item.session_id,
            "user_agent": item.user_agent or request.headers.get("user-agent"),
            "stack": item.stack,
            "client_user_id": user_id,
            "client_ip": ip,
            "client_extra": item.extra or {},
        }
        try:
            logger.log(level, "client: %s", item.msg, extra=extra)
        except Exception:  # pragma: no cover — never crash on a bad record
            dropped += 1
            continue
        accepted += 1

    api_logger.debug(
        "client-logs accepted=%d dropped=%d user_id=%s ip=%s",
        accepted,
        dropped,
        user_id,
        ip,
    )
    return ClientLogsResponse(accepted=accepted, dropped=dropped)


# Re-export the auth context dep so future routes that want to fold
# admin gating on top can still use it (and so static analyzers don't
# nag about the unused import — we keep it for one consumer plus
# downstream extension).
_ = get_auth_context

__all__ = ("router", "reset_rate_limit_for_tests")
