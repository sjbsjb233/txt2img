"""Unit tests for ``app.services.turnstile``.

We don't hit Cloudflare. ``httpx.MockTransport`` lets us assert what the
service sends and stub the response.
"""

from __future__ import annotations

import os

import httpx
import pytest


@pytest.mark.asyncio
async def test_verify_returns_true_when_secret_unset(fresh_env: None) -> None:
    """Empty TURNSTILE_SECRET → service treats any non-empty token as ok."""
    from app.services import turnstile

    assert await turnstile.verify("any-token") is True
    assert await turnstile.verify("") is False


@pytest.mark.asyncio
async def test_verify_posts_secret_and_response_to_cloudflare(
    fresh_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ["TURNSTILE_SECRET"] = "0xtestsecret"

    from app.config import get_settings

    get_settings.cache_clear()

    from app.services import turnstile

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    ok = await turnstile.verify("good-token", remote_ip="1.2.3.4")
    assert ok is True
    assert captured["url"] == turnstile.SITEVERIFY_URL
    body = captured["body"].decode()  # type: ignore[union-attr]
    # Form-encoded payload should contain secret, response, remoteip.
    assert "secret=0xtestsecret" in body
    assert "response=good-token" in body
    assert "remoteip=1.2.3.4" in body


@pytest.mark.asyncio
async def test_verify_returns_false_on_cloudflare_failure(
    fresh_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ["TURNSTILE_SECRET"] = "0xtestsecret"

    from app.config import get_settings

    get_settings.cache_clear()

    from app.services import turnstile

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, json={"success": False, "error-codes": ["invalid-input-response"]}
        )
    )
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    assert await turnstile.verify("bad-token") is False


@pytest.mark.asyncio
async def test_verify_returns_false_on_transport_error(
    fresh_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ["TURNSTILE_SECRET"] = "0xtestsecret"

    from app.config import get_settings

    get_settings.cache_clear()

    from app.services import turnstile

    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network gone")

    transport = httpx.MockTransport(boom)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    assert await turnstile.verify("any") is False
