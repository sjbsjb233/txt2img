"""Per-request log context propagated through asyncio ContextVars.

A handful of fields are useful on virtually every log line — the request
id we minted in the middleware, the authenticated user id, the job hash
the executor is currently processing. ContextVars carry those across
``await`` boundaries automatically, which means a task spawned deep
inside a request handler still inherits the same context without us
threading the values through every call signature.

The :class:`~app.utils.logging_setup.RequestContextFilter` reads these
vars on every record and injects them into ``record.request_id`` etc., so
the JSON formatter can emit them at top level. Code that wants to set
the context should use :func:`bind` (a context manager that automatically
restores the previous value) or directly call the ``set_*`` helpers when
the binding spans the whole logical request (e.g. middleware).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

REQUEST_ID_VAR: ContextVar[str | None] = ContextVar("txt2img_request_id", default=None)
USER_ID_VAR: ContextVar[str | None] = ContextVar("txt2img_user_id", default=None)
JOB_ID_VAR: ContextVar[str | None] = ContextVar("txt2img_job_id", default=None)
CLIENT_IP_VAR: ContextVar[str | None] = ContextVar("txt2img_client_ip", default=None)


def get_request_id() -> str | None:
    return REQUEST_ID_VAR.get()


def get_user_id() -> str | None:
    return USER_ID_VAR.get()


def get_job_id() -> str | None:
    return JOB_ID_VAR.get()


def get_client_ip() -> str | None:
    return CLIENT_IP_VAR.get()


def set_request_id(value: str | None) -> Token[str | None]:
    return REQUEST_ID_VAR.set(value)


def set_user_id(value: str | None) -> Token[str | None]:
    return USER_ID_VAR.set(value)


def set_job_id(value: str | None) -> Token[str | None]:
    return JOB_ID_VAR.set(value)


def set_client_ip(value: str | None) -> Token[str | None]:
    return CLIENT_IP_VAR.set(value)


@contextmanager
def bind(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    job_id: str | None = None,
    client_ip: str | None = None,
) -> Iterator[None]:
    """Temporarily bind context fields, restoring previous values on exit.

    Only the keyword args that were passed are mutated; unspecified
    fields keep their current binding. Designed to be cheap enough to
    drop into hot paths — three context-var sets is on the order of a
    couple of microseconds.
    """
    tokens: list[Token] = []
    if request_id is not None:
        tokens.append(REQUEST_ID_VAR.set(request_id))
    if user_id is not None:
        tokens.append(USER_ID_VAR.set(user_id))
    if job_id is not None:
        tokens.append(JOB_ID_VAR.set(job_id))
    if client_ip is not None:
        tokens.append(CLIENT_IP_VAR.set(client_ip))
    try:
        yield
    finally:
        # Reset in reverse so nested binds compose predictably.
        for token in reversed(tokens):
            # Defensive: a token from a different context (unlikely but
            # possible if a caller hops loops) would raise; we ignore
            # because failing to reset shouldn't crash the request.
            try:
                token.var.reset(token)
            except (ValueError, LookupError):
                pass


__all__ = (
    "REQUEST_ID_VAR",
    "USER_ID_VAR",
    "JOB_ID_VAR",
    "CLIENT_IP_VAR",
    "get_request_id",
    "get_user_id",
    "get_job_id",
    "get_client_ip",
    "set_request_id",
    "set_user_id",
    "set_job_id",
    "set_client_ip",
    "bind",
)
