"""Canonical id generators for every entity in the system.

All identifiers in the design doc carry a stable prefix + a fixed-width
random suffix. Centralising the alphabet, suffix length and prefix here
means a stray ``f"u_{uuid4().hex}"`` somewhere can never produce an id
that violates Appendix B's contract.

Format (design doc Appendix B):

================  ========  ==========  ====================
entity            prefix    suffix len  combined len
================  ========  ==========  ====================
user              ``u_``    12          14
job (internal)    ``job_``  12          16
job (hash_id)     ``j_``    12          14
set               ``set_``  10          14
image             ``img_``  10          14
session           ``sess_`` 10          15
announcement      ``ann_``  10          14
================  ========  ==========  ====================

Alphabet
--------
We use a single 62-character alphanumeric alphabet (``0-9A-Za-z``) for
every id. The design doc calls out base-32 specifically for ``hash_id``
but the strict regex enforced in ``app.services.image_io`` is
``^j_[A-Za-z0-9]{12}$`` — a 62-char alphabet matches that regex while
giving us the same shape as every other entity id, which avoids an
ergonomic split between "ids you can paste into a path" and "ids you
can't". Collision probability at 12 random characters is ~1 in 10^21
even with 100M existing ids; well within tolerance for a per-process
generator.

We use :mod:`secrets` (CSPRNG) rather than ``random`` because some
of these ids are user-visible — a guessable hash_id would let one user
enumerate another's archive even with auth in place.
"""

from __future__ import annotations

import secrets

# Alphanumeric, no separators or ambiguous-looking symbols. Matches the
# regex enforced by :func:`app.services.image_io.validate_hash_id` and
# every other id-shape check in the project.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _random_suffix(length: int) -> str:
    """Return ``length`` random characters from :data:`_ALPHABET`.

    ``secrets.choice`` runs in constant time per character against the
    62-element alphabet; total cost for the longest 12-char id is well
    under 10 microseconds.
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
# Per-entity helpers
# ---------------------------------------------------------------------------


def new_user_id() -> str:
    """Return ``u_`` + 12 random alphanumeric chars."""
    return f"u_{_random_suffix(12)}"


def new_job_internal_id() -> str:
    """Return ``job_`` + 12 chars — the surrogate primary key on ``jobs.id``.

    Distinct from ``new_job_hash_id`` because the internal id is the
    foreign-key target on ``job_references`` / ``images`` / ``session_jobs``
    and never crosses a process boundary, while ``hash_id`` is the only
    job identifier the frontend ever sees.
    """
    return f"job_{_random_suffix(12)}"


def new_job_hash_id() -> str:
    """Return ``j_`` + 12 chars — the public-facing job id.

    Validated by :func:`app.services.image_io.validate_hash_id` and used
    as a path segment under ``data/jobs/<hash_id>/``. Must match the
    regex ``^j_[A-Za-z0-9]{12}$``; the alphabet here is a strict subset.
    """
    return f"j_{_random_suffix(12)}"


def new_set_id() -> str:
    """Return ``set_`` + 10 chars.

    Only generated when the request asks for ``n > 1`` — single-image
    jobs leave ``jobs.set_id`` as ``NULL``.
    """
    return f"set_{_random_suffix(10)}"


def new_image_id() -> str:
    """Return ``img_`` + 10 chars."""
    return f"img_{_random_suffix(10)}"


def new_session_id() -> str:
    """Return ``sess_`` + 10 chars."""
    return f"sess_{_random_suffix(10)}"


def new_announcement_id() -> str:
    """Return ``ann_`` + 10 chars."""
    return f"ann_{_random_suffix(10)}"
