"""Symmetric encryption for at-rest secrets (provider API keys).

Background
----------
Provider rows store an upstream ``api_key`` so the executor can call it.
We never want that value to land on disk in cleartext: a stolen
``data/txt2img.db`` should not hand the attacker working credentials for
the underlying upstreams.

Key derivation
--------------
The encryption key is derived from ``Settings.JWT_SECRET`` via HKDF-SHA256
with a fixed application-scoped ``info`` label. Reusing the JWT secret
keeps the operator's secret-management surface to one variable; the HKDF
``info`` label provides domain separation so this code derives a distinct
AES key for provider-secret encryption rather than reusing the JWT signing
key directly. ``info`` and ``salt`` are fixed constants in source — they
are not secrets, just labels.

Wire format
-----------
``encrypt`` returns a self-describing string::

    "v1:" + base64( nonce[12] || aes_gcm_ciphertext_with_tag )

The ``v1:`` prefix is the one piece of forward-compatibility we keep —
if we ever rotate to AES-256-SIV or a different KDF, ``decrypt`` can
branch on the prefix and existing rows still decrypt.

Notes for callers
-----------------
- ``encrypt`` is deterministic *only* in its caller-visible behaviour:
  the underlying nonce is fresh on every call, so two encryptions of the
  same plaintext produce different ciphertexts. This is the AES-GCM
  norm; any "compare encrypted blobs to detect duplicates" pattern is
  wrong here.
- ``decrypt`` raises ``CryptoError`` (a ``ValueError`` subclass) on any
  failure — wrong key, tampered ciphertext, malformed prefix, junk
  base64. Callers should propagate this as a 500: a provider row with
  an undecodable ``api_key_enc`` is broken and we can't paper over it.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


_VERSION_PREFIX: Final[str] = "v1:"
_NONCE_LEN: Final[int] = 12  # GCM standard nonce length
_KEY_LEN: Final[int] = 32  # AES-256
_HKDF_INFO: Final[bytes] = b"txt2img/provider-api-key/v1"
_HKDF_SALT: Final[bytes] = b"txt2img-static-salt"


class CryptoError(ValueError):
    """Raised when ``decrypt`` cannot recover the plaintext.

    Subclass of ``ValueError`` so existing pydantic / FastAPI validation
    flows that catch ``ValueError`` keep working; the ``CryptoError``
    type lets specific handlers branch when they need to.
    """


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def _derive_key(secret: str) -> bytes:
    """Stretch ``secret`` into a 32-byte AES key via HKDF-SHA256.

    ``salt`` and ``info`` are fixed strings (not secrets) — they exist
    purely for domain separation, so the AES key derived here is a
    different bytestring than any other HKDF use of the same ``secret``.
    Per-row salt is unnecessary because every encrypted blob already
    carries its own random nonce.
    """
    if not secret:
        raise CryptoError("JWT_SECRET is empty; cannot derive crypto key")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return hkdf.derive(secret.encode("utf-8"))


def _aesgcm() -> AESGCM:
    """Return an ``AESGCM`` primed with the process-wide derived key.

    Computed on every call to keep this module stateless; HKDF over a
    32-byte key is fast (sub-microsecond on commodity hardware) and we
    never call this on a hot path — provider create/edit happens once
    per provider per admin action, and decrypts happen at the rate of
    upstream calls (already orders of magnitude slower).
    """
    settings = get_settings()
    key = _derive_key(settings.JWT_SECRET)
    return AESGCM(key)


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` with AES-256-GCM, return ``v1:<b64>`` string.

    A fresh 12-byte nonce is generated per call. The returned blob is
    URL-safe base64 (no padding) so it round-trips through SQLite TEXT
    columns and JSON exports without surprises.
    """
    if plaintext is None:  # type: ignore[unreachable]
        raise CryptoError("plaintext is None")
    aead = _aesgcm()
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = aead.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    blob = nonce + ciphertext
    encoded = base64.urlsafe_b64encode(blob).rstrip(b"=").decode("ascii")
    return f"{_VERSION_PREFIX}{encoded}"


def decrypt(ciphertext: str) -> str:
    """Inverse of ``encrypt``. Raises ``CryptoError`` on any failure.

    We accept the (unpadded) base64 output we produced. If the input is
    missing the version prefix or contains bytes outside the URL-safe
    alphabet we surface ``CryptoError`` — it's never a recoverable
    user-visible state, only a corrupted DB or attacker-substituted blob.
    """
    if not isinstance(ciphertext, str) or not ciphertext.startswith(_VERSION_PREFIX):
        raise CryptoError("ciphertext missing v1 prefix")
    encoded = ciphertext[len(_VERSION_PREFIX) :]
    # Re-pad to a multiple of 4 for ``urlsafe_b64decode``.
    padding = "=" * (-len(encoded) % 4)
    try:
        blob = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"ciphertext is not valid base64: {exc}") from exc

    if len(blob) <= _NONCE_LEN:
        raise CryptoError("ciphertext too short to contain nonce + payload")

    nonce, body = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    aead = _aesgcm()
    try:
        plaintext = aead.decrypt(nonce, body, associated_data=None)
    except Exception as exc:
        # Don't leak the underlying exception type — InvalidTag,
        # InvalidKey, etc. are all "this blob is unusable". Callers
        # should treat them identically.
        raise CryptoError("decryption failed") from exc
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def mask_api_key(plaintext: str) -> str:
    """Render an API key for admin UI display: ``sk-***LAST4``.

    The exact prefix isn't load-bearing — this is purely UX. We keep
    the first three characters when they look like a recognisable
    provider scheme prefix (``sk-``, ``AIz``, ``glpat-``...), otherwise
    the first three characters of whatever was given. The final four
    characters are always preserved so an admin can sanity-check the
    end of a key against the provider dashboard.
    """
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        # Too short to mask meaningfully; replace entirely.
        return "***"
    last4 = plaintext[-4:]
    head = plaintext[:3] if len(plaintext) > 7 else ""
    if head:
        return f"{head}***{last4}"
    return f"***{last4}"
