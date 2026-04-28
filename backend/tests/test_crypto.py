"""Tests for ``app.utils.crypto`` — AES-GCM encrypt/decrypt + masking.

The tests run *without* the FastAPI lifespan because the crypto helpers
only need ``Settings.JWT_SECRET``. They cover:

- Round-trip integrity for a variety of plaintexts (ASCII, unicode, empty).
- Key separation: same plaintext, two different secrets → different
  ciphertexts and unrecoverable across secrets.
- Tamper detection: any byte flip inside the blob raises ``CryptoError``.
- Masking format: ``sk-***LAST4`` for typical keys; safe fallback for
  short inputs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def crypto_env(tmp_path: Path) -> None:
    """Set the minimal env Settings needs and clear the lru_cache.

    Crypto doesn't talk to the DB so we don't need the ``initialized_db``
    fixture; just make sure JWT_SECRET is non-empty.
    """
    os.environ["JWT_SECRET"] = "test_secret_at_least_16_chars_long_value"
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    os.environ["DATA_ROOT"] = str(tmp_path)
    os.environ["DB_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}"

    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_ascii(crypto_env: None) -> None:
    from app.utils.crypto import decrypt, encrypt

    blob = encrypt("sk-MyApiKey-12345")
    assert blob.startswith("v1:")
    assert decrypt(blob) == "sk-MyApiKey-12345"


def test_round_trip_unicode(crypto_env: None) -> None:
    from app.utils.crypto import decrypt, encrypt

    plaintext = "key-with-äöü-and-中文-and-🚀"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_round_trip_empty_string(crypto_env: None) -> None:
    """Empty string is a valid (if useless) plaintext.

    AES-GCM handles zero-length input fine; we don't add a guard so
    the test pins behaviour rather than imposing a constraint.
    """
    from app.utils.crypto import decrypt, encrypt

    assert decrypt(encrypt("")) == ""


def test_two_encryptions_of_same_plaintext_differ(crypto_env: None) -> None:
    """Random nonce per call → two ciphertexts must not be byte-equal."""
    from app.utils.crypto import encrypt

    a = encrypt("sk-same-input")
    b = encrypt("sk-same-input")
    assert a != b


# ---------------------------------------------------------------------------
# Persistence: ciphertext is base64-only chars after the v1: prefix
# ---------------------------------------------------------------------------


def test_ciphertext_is_url_safe_base64(crypto_env: None) -> None:
    """Output should round-trip through SQLite TEXT and JSON without escaping."""
    from app.utils.crypto import encrypt

    blob = encrypt("sk-1234567890")
    body = blob[len("v1:") :]
    # URL-safe alphabet plus optional '=' padding (we strip it but allow it).
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
    )
    assert all(c in allowed for c in body)


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_decrypt_missing_prefix_raises(crypto_env: None) -> None:
    from app.utils.crypto import CryptoError, decrypt

    with pytest.raises(CryptoError):
        decrypt("garbage-no-prefix")


def test_decrypt_bad_base64_raises(crypto_env: None) -> None:
    from app.utils.crypto import CryptoError, decrypt

    with pytest.raises(CryptoError):
        decrypt("v1:not!valid!base64!")


def test_decrypt_truncated_raises(crypto_env: None) -> None:
    """Removing trailing bytes invalidates the GCM tag."""
    from app.utils.crypto import CryptoError, decrypt, encrypt

    blob = encrypt("sk-something-long-enough")
    truncated = blob[:-4]
    with pytest.raises(CryptoError):
        decrypt(truncated)


def test_decrypt_with_different_secret_raises(
    crypto_env: None, tmp_path: Path
) -> None:
    """Encrypt under one JWT_SECRET, switch secret, decrypt should fail.

    Verifies key derivation actually depends on the secret (i.e. we
    didn't accidentally hard-code the key).
    """
    from app.config import get_settings
    from app.utils.crypto import CryptoError, decrypt, encrypt

    blob = encrypt("sk-original-key")

    os.environ["JWT_SECRET"] = "different_secret_at_least_16_chars_x"
    get_settings.cache_clear()

    with pytest.raises(CryptoError):
        decrypt(blob)


# ---------------------------------------------------------------------------
# Mask helper
# ---------------------------------------------------------------------------


def test_mask_typical_openai_style(crypto_env: None) -> None:
    from app.utils.crypto import mask_api_key

    assert mask_api_key("sk-FQBR1234567890abcdef") == "sk-***cdef"


def test_mask_short_input(crypto_env: None) -> None:
    from app.utils.crypto import mask_api_key

    assert mask_api_key("abcd") == "***"


def test_mask_empty(crypto_env: None) -> None:
    from app.utils.crypto import mask_api_key

    assert mask_api_key("") == ""


def test_mask_medium_input(crypto_env: None) -> None:
    """Medium-length keys still mask the middle but show the tail."""
    from app.utils.crypto import mask_api_key

    masked = mask_api_key("xxxxxxx")  # 7 chars: head=='', last4='xxxx'
    assert masked == "***xxxx"
