"""Unit tests for ``app.utils.redact``."""

from __future__ import annotations

import json

from app.utils.redact import (
    excerpt_for_response,
    redact_payload,
    redact_upstream_body,
)


def test_redact_strips_bearer_tokens() -> None:
    text = "401 Authorization: Bearer sk-AAAAAAAAAAAAAAAAAAAAAAAA broke"
    cleaned = redact_upstream_body(text)
    assert "sk-AAAA" not in cleaned
    assert "REDACTED" in cleaned


def test_redact_strips_sensitive_json_keys() -> None:
    body = json.dumps(
        {
            "ok": False,
            "api_key": "sk-secret-1234567890ABCDEF",
            "details": {
                "Authorization": "Bearer abcdefghijklmnopqrstuv",
                "msg": "fail",
            },
        }
    )
    cleaned = redact_upstream_body(body)
    assert "sk-secret" not in cleaned
    assert "abcdefghijkl" not in cleaned
    parsed = json.loads(cleaned)
    assert parsed["api_key"] == "[REDACTED]"


def test_redact_payload_walks_nested_lists() -> None:
    parsed = redact_payload(
        {
            "list": [
                {"token": "abcdefghijklmnopqrstuv"},
                "Bearer sk-AAAAAAAAAAAAAAAAAAAA",
            ]
        }
    )
    assert parsed["list"][0]["token"] == "[REDACTED]"
    assert "REDACTED" in parsed["list"][1]


def test_redact_truncates_to_max_len() -> None:
    body = "x" * 4000
    out = redact_upstream_body(body, max_len=200)
    assert len(out) <= 203  # 200 + "..."
    assert out.endswith("...")


def test_excerpt_for_response_default_200() -> None:
    assert excerpt_for_response("short") == "short"
    long = "y" * 500
    out = excerpt_for_response(long)
    assert out is not None
    assert len(out) <= 203
    assert out.endswith("...")


def test_excerpt_passes_none_through() -> None:
    assert excerpt_for_response(None) is None
    assert redact_upstream_body(None) is None
