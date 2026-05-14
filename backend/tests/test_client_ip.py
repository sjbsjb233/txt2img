"""Unit + integration tests for :mod:`app.utils.client_ip`.

The unit half builds synthetic ``Request`` objects to exercise the
header-priority logic without an event loop. The integration half goes
through the real FastAPI app so we also confirm:

* ``RequestLoggingMiddleware`` writes the ``ip_source`` extra field.
* Other routes (``/api/auth/login``) record the same resolved IP for
  audit purposes via ``login_attempts.ip``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.utils.client_ip import client_ip_from, client_ip_with_source


def _make_request(headers: dict[str, str], *, peer: tuple[str, int] | None = ("1.2.3.4", 56789)) -> Request:
    """Build an ASGI scope just rich enough to back ``Request``.

    ``Request`` consults ``scope['headers']`` (list of bytes pairs) for
    ``request.headers`` and ``scope['client']`` for ``request.client``.
    Everything else can stay empty.
    """
    raw_headers: list[tuple[bytes, bytes]] = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()
    ]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": raw_headers,
        "client": peer,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Unit: priority order
# ---------------------------------------------------------------------------


def test_cf_connecting_ip_wins_over_everything():
    req = _make_request(
        {
            "cf-connecting-ip": "203.0.113.42",
            "x-forwarded-for": "198.51.100.10, 203.0.113.42",
        },
        peer=("172.23.0.1", 1234),
    )
    ip, source = client_ip_with_source(req)
    assert ip == "203.0.113.42"
    assert source == "cf"


def test_xff_used_when_no_cf_header():
    """No CF; honour the leftmost XFF entry."""
    req = _make_request(
        {"x-forwarded-for": "198.51.100.10, 10.0.0.5"},
        peer=("172.23.0.1", 1234),
    )
    ip, source = client_ip_with_source(req)
    assert ip == "198.51.100.10"
    assert source == "xff"


def test_peer_used_when_no_proxy_headers():
    """Fully LAN direct: socket peer is the answer."""
    req = _make_request({}, peer=("192.168.1.16", 50000))
    ip, source = client_ip_with_source(req)
    assert ip == "192.168.1.16"
    assert source == "peer"


def test_unknown_when_no_signal_at_all():
    """Both headers and socket peer missing → (None, 'unknown')."""
    req = _make_request({}, peer=None)
    ip, source = client_ip_with_source(req)
    assert ip is None
    assert source == "unknown"


def test_cf_header_whitespace_stripped():
    req = _make_request({"cf-connecting-ip": "  203.0.113.42 "})
    ip, source = client_ip_with_source(req)
    assert ip == "203.0.113.42"
    assert source == "cf"


def test_empty_cf_header_falls_through():
    """A blank CF header must not shadow the socket peer."""
    req = _make_request({"cf-connecting-ip": ""}, peer=("192.168.1.16", 1))
    ip, source = client_ip_with_source(req)
    assert ip == "192.168.1.16"
    assert source == "peer"


def test_empty_xff_entry_falls_through():
    req = _make_request({"x-forwarded-for": ", 10.0.0.5"}, peer=("172.23.0.1", 1))
    ip, source = client_ip_with_source(req)
    # First non-empty entry isn't what we promise; the contract is the
    # leftmost entry. An empty leftmost means we fall through to peer.
    assert source == "peer"
    assert ip == "172.23.0.1"


def test_xff_single_entry():
    req = _make_request({"x-forwarded-for": "203.0.113.42"})
    ip, source = client_ip_with_source(req)
    assert ip == "203.0.113.42"
    assert source == "xff"


def test_convenience_wrapper_returns_just_ip():
    req = _make_request({"cf-connecting-ip": "203.0.113.42"})
    assert client_ip_from(req) == "203.0.113.42"


# ---------------------------------------------------------------------------
# Integration: middleware + access log + downstream audit IP
# ---------------------------------------------------------------------------


class _RecordCapture(logging.Handler):
    """Attach to a specific logger and grab every record it emits.

    We use this instead of ``caplog`` because:
    * The access logger sits under ``txt2img.*`` which has
      ``propagate=False`` after ``configure_logging`` runs, so caplog's
      root handler never sees it.
    * The middleware demotes ``/api/health`` access records to DEBUG to
      avoid drowning the file. caplog's default INFO threshold filters
      those out — easy to forget.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _attach_access_capture():
    cap = _RecordCapture()
    logger = logging.getLogger("txt2img.access")
    logger.addHandler(cap)
    return cap, logger


@pytest.mark.asyncio
async def test_middleware_records_ip_source_cf(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Inject CF-Connecting-IP → access record gets ip_source=cf."""
    cap, logger = _attach_access_capture()
    try:
        resp = await seeded_app.get(
            "/api/me",  # unauthenticated → 401, warning-level access record
            headers={
                "CF-Connecting-IP": "203.0.113.42",
                "X-Forwarded-For": "198.51.100.99",
            },
        )
        assert resp.status_code == 401
        access = [r for r in cap.records if r.name == "txt2img.access"]
        assert access, "no access record captured"
        record = access[-1]
        assert record.client_ip == "203.0.113.42"
        assert getattr(record, "ip_source", None) == "cf"
    finally:
        logger.removeHandler(cap)


@pytest.mark.asyncio
async def test_middleware_records_ip_source_xff(
    seeded_app: httpx.AsyncClient,
) -> None:
    cap, logger = _attach_access_capture()
    try:
        resp = await seeded_app.get(
            "/api/me",
            headers={"X-Forwarded-For": "198.51.100.99, 10.0.0.5"},
        )
        assert resp.status_code == 401
        access = [r for r in cap.records if r.name == "txt2img.access"]
        assert access
        record = access[-1]
        assert record.client_ip == "198.51.100.99"
        assert getattr(record, "ip_source", None) == "xff"
    finally:
        logger.removeHandler(cap)


@pytest.mark.asyncio
async def test_middleware_records_ip_source_peer(
    seeded_app: httpx.AsyncClient,
) -> None:
    cap, logger = _attach_access_capture()
    try:
        # httpx + ASGITransport produces no client tuple by default, so
        # the peer fallback lands on (None, 'unknown'). The unit tests
        # already cover the value; here we just confirm we did *not*
        # accidentally fall back to xff/cf.
        resp = await seeded_app.get("/api/me")
        assert resp.status_code == 401
        access = [r for r in cap.records if r.name == "txt2img.access"]
        assert access
        record = access[-1]
        assert getattr(record, "ip_source", None) in {"peer", "unknown"}
        assert record.client_ip in (None, "127.0.0.1", "testclient")
    finally:
        logger.removeHandler(cap)


@pytest.mark.asyncio
async def test_login_attempt_records_cf_ip(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A failed login records the CF-resolved IP, not the frp peer."""
    from app.db.engine import get_session
    from app.db.models import LoginAttempt

    resp = await seeded_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "definitely-wrong"},
        headers={"CF-Connecting-IP": "203.0.113.42"},
    )
    assert resp.status_code == 401

    async with get_session() as session:
        row = (
            await session.execute(
                select(LoginAttempt)
                .where(LoginAttempt.username == "admin")
                .order_by(LoginAttempt.attempted_at.desc())
                .limit(1)
            )
        ).scalar_one()
    assert row.ip == "203.0.113.42"


@pytest.mark.asyncio
async def test_client_logs_records_cf_ip(
    seeded_app: httpx.AsyncClient,
) -> None:
    """/api/client-logs should also pick up CF-Connecting-IP."""
    caplog_level = logging.DEBUG
    logger = logging.getLogger("txt2img.client")
    logger.setLevel(caplog_level)
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=caplog_level)
    logger.addHandler(handler)
    try:
        resp = await seeded_app.post(
            "/api/client-logs",
            json={"items": [{"level": "info", "msg": "ip test"}]},
            headers={"CF-Connecting-IP": "203.0.113.42"},
        )
        assert resp.status_code == 200
        # Our records carry the extra dict with ``client_ip``.
        ours = [r for r in captured if getattr(r, "msg", "").startswith("client:")]
        assert ours, "no txt2img.client record captured"
        assert getattr(ours[-1], "client_ip", None) == "203.0.113.42"
    finally:
        logger.removeHandler(handler)
