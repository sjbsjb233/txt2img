"""ID generator tests — shape, alphabet, uniqueness, regex compatibility."""

from __future__ import annotations

import re

import pytest

from app.utils.ids import (
    new_announcement_id,
    new_image_id,
    new_job_hash_id,
    new_job_internal_id,
    new_session_id,
    new_set_id,
    new_user_id,
)


# Pre-compiled regexes that mirror the design-doc Appendix B contract
# and the validators used elsewhere in the codebase. Keeping them here
# in test code rather than importing from the production validator means
# a regression in production code can't silently mask a regression in
# the id format.
_HASH_ID_RE = re.compile(r"^j_[A-Za-z0-9]{12}$")
_USER_ID_RE = re.compile(r"^u_[A-Za-z0-9]{12}$")
_INTERNAL_JOB_ID_RE = re.compile(r"^job_[A-Za-z0-9]{12}$")
_SET_ID_RE = re.compile(r"^set_[A-Za-z0-9]{10}$")
_IMAGE_ID_RE = re.compile(r"^img_[A-Za-z0-9]{10}$")
_SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{10}$")
_ANN_ID_RE = re.compile(r"^ann_[A-Za-z0-9]{10}$")


# Each ``(generator, regex, total_length, prefix)`` row encodes the
# Appendix B contract for one entity. Driving tests through this table
# avoids cut-and-paste between similar test cases and makes it obvious
# at a glance which entities are under test.
_CASES = [
    (new_user_id, _USER_ID_RE, 14, "u_"),
    (new_job_internal_id, _INTERNAL_JOB_ID_RE, 16, "job_"),
    (new_job_hash_id, _HASH_ID_RE, 14, "j_"),
    (new_set_id, _SET_ID_RE, 14, "set_"),
    (new_image_id, _IMAGE_ID_RE, 14, "img_"),
    (new_session_id, _SESSION_ID_RE, 15, "sess_"),
    (new_announcement_id, _ANN_ID_RE, 14, "ann_"),
]


@pytest.mark.parametrize("gen, regex, total_len, prefix", _CASES)
def test_id_shape(gen, regex, total_len, prefix) -> None:
    """Each generator emits a string with the right prefix, length, and alphabet."""
    for _ in range(50):
        value = gen()
        assert isinstance(value, str)
        assert len(value) == total_len, value
        assert value.startswith(prefix), value
        assert regex.match(value), value


@pytest.mark.parametrize("gen", [c[0] for c in _CASES])
def test_id_uniqueness_at_scale(gen) -> None:
    """1000 calls should never collide — stronger than any production load.

    62^10 is ~8.4 × 10^17 and 62^12 is ~3.2 × 10^21; the chance of a
    collision in 1000 draws is negligible. A failure here means the
    generator has stopped using a CSPRNG (e.g. someone replaced
    ``secrets`` with a deterministic source).
    """
    seen = {gen() for _ in range(1000)}
    assert len(seen) == 1000


def test_hash_id_regex_matches_image_io_validator() -> None:
    """The hash id format must satisfy ``image_io.validate_hash_id``.

    This is the single load-bearing constraint between the generator
    and the filesystem path validator. If the regex in image_io ever
    drifts from the alphabet here, every freshly generated hash_id
    starts to fail validation before it lands on disk.
    """
    from app.services.image_io import validate_hash_id

    for _ in range(20):
        validate_hash_id(new_job_hash_id())  # raises if mismatch
