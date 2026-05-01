"""Pydantic shapes for announcement endpoints (PR-17 / design doc §11).

Two surfaces share these schemas:

- **Admin** — list / create / update / delete (``/api/admin/announcements``)
- **User-facing** — read-only "active for me" + mark-as-read
  (``/api/announcements/...``)

The split is intentional: the user-facing list deliberately omits
operational fields like ``created_by`` / ``audience_kind`` / ``reach``
so an end-user can never tell which other tier got an announcement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# v1 supports only ``text`` and ``image``; ``react`` is reserved for v1.1
# (design doc §20 problem #2). The schema rejects ``react`` here so an
# admin attempting it gets a clear 422 today.
ContentKind = Literal["text", "image"]
AudienceKind = Literal["all", "tier", "user_list"]


# Soft length cap on the textual body. Matches the on-disk announcements
# spec — anything longer should be a separate doc / blog post linked from
# the announcement.
_MAX_TITLE_LEN = 200
_MAX_TEXT_LEN = 8000
_MAX_IMAGE_PATH_LEN = 512


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _validate_audience_data(
    kind: str,
    data: list[str] | None,
) -> list[str] | None:
    """Cross-field check: ``audience_data`` must match ``audience_kind``.

    - ``all`` → ``audience_data`` must be omitted / null / empty.
    - ``tier`` → list of tier names from {vip, premium, standard, free},
      non-empty, deduplicated.
    - ``user_list`` → list of user-id strings, non-empty.
    """
    if kind == "all":
        if data:
            raise ValueError("audience_data must be empty when audience_kind='all'")
        return None
    if kind == "tier":
        if not data:
            raise ValueError("audience_data must be a non-empty tier list")
        valid = {"vip", "premium", "standard", "free"}
        bad = [t for t in data if t not in valid]
        if bad:
            raise ValueError(
                f"audience_data tiers must be in {sorted(valid)}; got invalid: {bad}"
            )
        # Dedupe while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for t in data:
            if t in seen:
                continue
            seen.add(t)
            deduped.append(t)
        return deduped
    if kind == "user_list":
        if not data:
            raise ValueError("audience_data must be a non-empty user-id list")
        bad = [u for u in data if not isinstance(u, str) or not u.startswith("u_")]
        if bad:
            raise ValueError(
                f"audience_data user ids must start with 'u_'; got invalid: {bad}"
            )
        return list(dict.fromkeys(data))
    raise ValueError(f"unknown audience_kind {kind!r}")


# ---------------------------------------------------------------------------
# Admin: create / patch
# ---------------------------------------------------------------------------


class AnnouncementCreateRequest(BaseModel):
    """Body of ``POST /api/admin/announcements``.

    The optional ``cover`` upload is handled separately by the route as
    multipart; here we describe the JSON-only fields so the admin can
    POST a text announcement with a single ``application/json`` request.
    For ``content_kind='image'`` the route accepts the cover file as a
    multipart part — see the route docstring.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=_MAX_TITLE_LEN)
    content_kind: ContentKind = Field(default="text")
    # For ``text`` this is the body markdown. For ``image`` it's the
    # rel-path to the uploaded file under ``data/announcements/<id>/``;
    # admin POST sets it implicitly from the multipart upload, so JSON-only
    # text creates supply the body here directly.
    content: str = Field(..., min_length=1, max_length=_MAX_TEXT_LEN)

    audience_kind: AudienceKind = Field(default="all")
    audience_data: list[str] | None = None

    starts_at: datetime
    ends_at: datetime | None = None
    dismissable: bool = True
    priority: int = Field(default=0, ge=-100, le=100)

    @field_validator("content")
    @classmethod
    def _trim_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content cannot be empty after trimming")
        return v

    @field_validator("audience_data")
    @classmethod
    def _validate_audience_data_field(
        cls, v: list[str] | None, info
    ) -> list[str] | None:
        kind = info.data.get("audience_kind", "all")
        return _validate_audience_data(kind, v)


class AnnouncementPatchRequest(BaseModel):
    """Body of ``PATCH /api/admin/announcements/<id>``.

    Every field is optional. ``audience_kind`` and ``audience_data`` must
    come together when changing audience: a lone ``audience_data`` patch
    without the kind is ambiguous so we reject it 422.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=_MAX_TITLE_LEN)
    content: str | None = Field(default=None, min_length=1, max_length=_MAX_TEXT_LEN)
    audience_kind: AudienceKind | None = None
    audience_data: list[str] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    dismissable: bool | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)


# ---------------------------------------------------------------------------
# Admin: read
# ---------------------------------------------------------------------------


class AnnouncementAdminView(BaseModel):
    """Full admin-view of one announcement row (incl. operational stats)."""

    id: str
    title: str | None
    content_kind: ContentKind
    content: str
    audience_kind: AudienceKind
    audience_data: list[str] | None
    starts_at: datetime
    ends_at: datetime | None
    dismissable: bool
    priority: int
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Computed:
    is_live: bool
    """True iff ``starts_at <= now < ends_at`` (or ``ends_at`` is null)."""
    reach: int
    """Estimated audience size at the time the response was built."""
    read_count: int
    """Number of distinct users who have called ``mark-as-read``."""


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementAdminView]


# ---------------------------------------------------------------------------
# User-facing: read
# ---------------------------------------------------------------------------


class AnnouncementUserView(BaseModel):
    """The strictly minimum payload a user needs to render one banner.

    Operational fields (audience, reach, read counts, created_by) are
    deliberately absent — see module docstring.
    """

    id: str
    title: str | None
    content_kind: ContentKind
    content: str
    dismissable: bool
    priority: int
    starts_at: datetime
    ends_at: datetime | None
    cover_url: str | None
    """For ``content_kind='image'`` — convenience absolute path to the
    cover endpoint. ``None`` for text announcements.
    """


class ActiveAnnouncementsResponse(BaseModel):
    items: list[AnnouncementUserView]


class MarkReadResponse(BaseModel):
    id: str
    read: bool = True
