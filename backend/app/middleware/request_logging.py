"""Request id + access log middleware.

Three responsibilities, in order:

1. Assign a request id. Honour an incoming ``X-Request-ID`` header when
   it's present (some upstream proxies / load tests inject their own)
   but otherwise mint a fresh ``uuid4().hex``.
2. Bind the id (and a best-effort client IP) into the per-request
   :mod:`app.utils.log_context` so every downstream ``logger.*`` call
   automatically attaches it.
3. After the response is dispatched, emit one structured access record
   on the ``txt2img.access`` logger.

The access logger is configured in :mod:`app.utils.logging_setup` to
route to ``access.log``. We still propagate to the root ``txt2img``
namespace so the same record also lands in ``app.log`` for one-stop
grepping (turn off via ``LOG_ACCESS_ENABLED=false`` to silence the
dedicated file but keep the audit trail in ``app.log``).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils import log_context
from app.utils.client_ip import client_ip_with_source
from app.utils.logging_setup import ACCESS_LOGGER_NAME

access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
boundary_logger = logging.getLogger("txt2img.request")


_X_REQUEST_ID = "X-Request-ID"
_MAX_REQUEST_ID_LEN = 128


def _sanitise_inbound_request_id(raw: str | None) -> str | None:
    """Trust only short, printable inbound request ids.

    A header from a public client could otherwise be any bytes; we keep
    only alphanumerics + a handful of safe delimiters and clamp the
    length so the id can't bloat every log record.
    """
    if not raw:
        return None
    cleaned = "".join(
        ch for ch in raw if ch.isalnum() or ch in "-_." or ch == ":"
    )
    if not cleaned:
        return None
    if len(cleaned) > _MAX_REQUEST_ID_LEN:
        cleaned = cleaned[:_MAX_REQUEST_ID_LEN]
    return cleaned


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a stable id and emit an access record.

    The middleware is intentionally permissive on the way in: even
    bogus inputs (missing client, weird headers) should not crash the
    request — that's the call site we're trying to make observable.
    """

    async def dispatch(  # noqa: D401
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (
            _sanitise_inbound_request_id(request.headers.get(_X_REQUEST_ID))
            or uuid.uuid4().hex
        )
        client_ip, ip_source = client_ip_with_source(request)

        rid_token = log_context.set_request_id(request_id)
        ip_token = log_context.set_client_ip(client_ip)
        # Always clear stale user/job ids — a worker reusing a coroutine
        # context could otherwise leak the previous request's values.
        user_token = log_context.set_user_id(None)
        job_token = log_context.set_job_id(None)

        start = time.perf_counter()
        response: Response | None = None
        error_kind: str | None = None
        status_code: int = 500
        try:
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception as exc:
                error_kind = type(exc).__name__
                boundary_logger.exception(
                    "request crashed: %s %s",
                    request.method,
                    request.url.path,
                )
                raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            extra = {
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) if request.url.query else None,
                "status": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
                # Which header / signal the IP came from. Useful for
                # spotting "all requests look like 172.23.0.1" when the
                # CF tunnel is bypassed or the frp container is exposed
                # directly. ``cf`` = CF-Connecting-IP, ``xff`` =
                # X-Forwarded-For, ``peer`` = socket peer, ``unknown`` =
                # neither.
                "ip_source": ip_source,
                "user_agent": request.headers.get("user-agent"),
                "request_id": request_id,
                "user_id": log_context.get_user_id(),
                "error_kind": error_kind,
            }
            # Hide noisy infrastructure pings unless they fail. ``/api/health``
            # is hit by docker healthchecks every couple of seconds; logging
            # those at INFO would drown the file.
            level = logging.INFO
            if request.url.path == "/api/health" and status_code < 400:
                level = logging.DEBUG
            elif status_code >= 500:
                level = logging.ERROR
            elif status_code >= 400:
                level = logging.WARNING
            try:
                access_logger.log(level, "access", extra=extra)
            except Exception:
                # Never let access-logging fail the request.
                pass
            # Echo the id back so a client / proxy can correlate.
            if response is not None:
                response.headers[_X_REQUEST_ID] = request_id
            # Reset context vars in reverse order; defensive against the
            # rare loop-hopping case.
            for token in (job_token, user_token, ip_token, rid_token):
                try:
                    token.var.reset(token)
                except (ValueError, LookupError):
                    pass
        # ``response`` is set after the await; mypy doesn't know that.
        return response  # type: ignore[return-value]


__all__ = ("RequestLoggingMiddleware",)
