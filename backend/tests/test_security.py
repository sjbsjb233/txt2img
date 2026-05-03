"""Unit tests for ``app.utils.security`` primitives."""

from __future__ import annotations

import time

import pytest


def test_hash_and_verify_password_round_trip(fresh_env: None) -> None:
    from app.utils.security import hash_password, verify_password

    h = hash_password("hunter2-correct-horse")
    assert h.startswith("$argon2id$")
    assert verify_password("hunter2-correct-horse", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_handles_garbage(fresh_env: None) -> None:
    """Malformed hash strings must return False, not raise."""
    from app.utils.security import verify_password

    assert verify_password("any", "not-a-real-hash") is False
    assert verify_password("any", "") is False


def test_jwt_round_trip_payload_shape(fresh_env: None) -> None:
    """Encoded token must decode back to the §2.1 payload exactly."""
    from app.utils.security import decode_access_token, issue_access_token

    token = issue_access_token("u_abc1234567890", "alice", "user")
    payload = decode_access_token(token)
    # ``jti`` was added with auth_sessions tracking — the §2.1 payload
    # now carries it so the auth dependency can revoke a single device.
    assert set(payload.keys()) == {"sub", "u", "r", "iat", "exp", "jti"}
    assert payload["sub"] == "u_abc1234567890"
    assert payload["u"] == "alice"
    assert payload["r"] == "user"
    assert payload["exp"] > payload["iat"]
    assert isinstance(payload["jti"], str) and payload["jti"]


def test_jwt_decode_rejects_tampered_token(fresh_env: None) -> None:
    """Flip a byte in the signature → TokenError."""
    from app.utils.security import (
        TokenError,
        decode_access_token,
        issue_access_token,
    )

    token = issue_access_token("u_abc1234567890", "alice", "user")
    head, _, sig = token.rpartition(".")
    bad_token = f"{head}.{sig[:-2]}AA"
    with pytest.raises(TokenError):
        decode_access_token(bad_token)


def test_jwt_decode_rejects_expired_token(fresh_env: None) -> None:
    from app.utils.security import (
        TokenError,
        decode_access_token,
        issue_access_token,
    )

    token = issue_access_token(
        "u_abc1234567890", "alice", "user", expires_in_days=-1
    )
    # Sleep a tiny bit so jose's leeway doesn't accidentally accept it.
    time.sleep(0.01)
    with pytest.raises(TokenError):
        decode_access_token(token)
