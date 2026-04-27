"""SQLAlchemy ORM models for the txt2img app.

Schema overview
---------------

::

    users                 -- application accounts (independent)

    image_models          -- catalog of the ~5 image-generation models
        ▲                    we know about (gpt-image-2, gemini-..., ...).
        │                    Holds each model's *full* tunable-parameter
        │                    schema (`param_schema`).
        │
    relay_station_models  -- join row: this station serves this model.
        │                    Holds per-(station, model) overrides
        │                    (cost / rpm / concurrency) AND a `param_capabilities`
        │                    JSON that *narrows* the parent model's `param_schema`
        │                    for this specific station.
        ▼
    relay_stations        -- 中转站 (upstream API relays). Holds the API key,
                             base URL, balance and station-wide limits.

The two JSON columns (`image_models.param_schema` and
`relay_station_models.param_capabilities`) are intentional: the model-level
schema describes what a model *can* do, the per-(station, model) capabilities
describe what a given upstream actually *exposes*. See the docstrings on
`ImageModel` and `RelayStationModel` for the exact merge rules.

Migrations: there is no Alembic; tables are created on startup via
`Base.metadata.create_all()` (see `init_db.py`). That means **adding a column
to an existing table will NOT take effect on a running deployment** — you'd
have to drop the table or hand-write an ALTER. New tables are fine.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    """Application account. Created either by admin bootstrap (`init_db.py`)
    or by an admin via `/api/users`. `tier` is a coarse capability level used
    by quota / feature gates; `is_admin` is the binary admin flag."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Soft "role" flag. Admin endpoints check this rather than `tier`.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Capability tier (0 = default user, higher = more privileged). The bootstrap
    # admin is created at tier 99. Quotas / feature gates can compare against this.
    tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Soft delete / disable: `False` blocks login without removing history.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Login throttle bookkeeping. Reset on successful login.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ImageModel(Base):
    """Catalog of the image-generation models the system supports.

    The set is small and roughly fixed (~5 entries today: ``gpt-image-2``,
    ``gemini-2.5-flash-image``, ``gemini-3-pro-image-preview``, ...). Rows
    here are referenced by `RelayStationModel.image_model_id` to express
    "which models a given relay station can serve".

    Capability layering
    -------------------
    Each model has a *canonical* parameter schema in :attr:`param_schema` —
    the **superset** of every parameter the model itself can accept. A relay
    station that serves this model then provides its **own narrowing** of
    that schema in :attr:`RelayStationModel.param_capabilities`.

    The frontend resolves "what controls to render for (station, model)" by
    starting from `param_schema` and overlaying the station's
    `param_capabilities` (see `RelayStationModel` for merge rules).
    """

    __tablename__ = "image_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Stable string identifier matching what the upstream API expects, e.g.
    # "gpt-image-2", "gemini-2.5-flash-image". Used by request dispatchers
    # to pick a client; UNIQUE so callers can also look up by key.
    model_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    # Human-friendly display name for the UI ("ChatGPT Images 2.0").
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Protocol family this model belongs to. Drives which client class is used
    # and which `param_schema` keys are meaningful. Examples:
    #   "openai_images"  -> uses size/quality/n/background/output_format
    #   "gemini"         -> uses aspect_ratio/image_size
    family: Mapped[str] = mapped_column(String(64), nullable=False)

    # Globally hide a model from selection (e.g. deprecated upstream).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # UI ordering hint; ascending. Ties broken by `id`.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # JSON description of the FULL set of parameters this model can accept.
    # Shape: { "<param_name>": {"values": [...]} | {"min": N, "max": M} | {...} }
    # NULL means "no opinion — defer entirely to per-station capabilities".
    # Example for gpt-image-2:
    #   {
    #     "size":          {"values": ["1024x1024","1024x1536","1536x1024","auto"]},
    #     "quality":       {"values": ["auto","low","medium","high"]},
    #     "n":             {"min": 1, "max": 10},
    #     "background":    {"values": ["auto","transparent","opaque"]},
    #     "output_format": {"values": ["png","jpeg","webp"]}
    #   }
    # Example for gemini-2.5-flash-image (note: `image_size` is intentionally
    # absent because this model ignores it — see backend/app/image_clients):
    #   {
    #     "aspect_ratio": {"values": ["1:1","4:3","3:4","16:9","9:16",...]}
    #   }
    param_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # All (station, this-model) bindings. CASCADE so deleting a model rips out
    # its relay-station bindings as well — there's no reason to keep an orphan
    # binding pointing at a model that no longer exists.
    station_bindings: Mapped[list["RelayStationModel"]] = relationship(
        back_populates="image_model",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RelayStation(Base):
    """A 中转站 — an upstream API relay that proxies image-generation calls.

    A station has a base URL, an API key, station-wide rate limits and a
    balance. The set of models it serves is *not* stored here but on
    `RelayStationModel` rows (one per (station, model) pairing), because each
    pairing has its own price, rate limits and parameter capabilities.
    """

    __tablename__ = "relay_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Stable business key for this station, e.g. "bltcy". UNIQUE so config can
    # reference it by name without coupling to surrogate ids. Don't reuse a
    # provider_id for a different upstream — the dispatcher caches by it.
    provider_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Display name for the admin UI ("BLTCY").
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Which protocol adapter to use when calling this station. The string is
    # matched against the registered client classes in `app.image_clients`.
    # Typical values: "openai_images", "gemini_v1beta".
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Where to hit. Should NOT include a trailing slash; the adapter appends
    # its own path (e.g. "/v1/images/generations").
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # Bearer / API key for the upstream. Stored as plaintext today; treat the
    # DB as a secret. If we ever encrypt-at-rest, do it transparently here.
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)

    # Default cost per generated image in CNY. Used when a `RelayStationModel`
    # row leaves its own `cost_per_image_cny` NULL.
    cost_per_image_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    # The balance we recorded when the station was registered / topped up.
    # Immutable record (we don't decrement this) — useful for "spent so far"
    # calculations: spent = initial_balance_cny - balance_cny.
    initial_balance_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    # Live remaining balance. Decremented as jobs succeed; the dispatcher
    # should refuse to route to a station whose `balance_cny` <= 0.
    balance_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )

    # Master switch: `False` removes this station from routing entirely
    # (regardless of per-model `RelayStationModel.enabled`).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Station-wide concurrency cap (max in-flight requests). Per-(station,model)
    # caps in `RelayStationModel.max_concurrency` further constrain a single
    # model on this station.
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Station-wide requests-per-minute cap. NULL means no station-level limit
    # (the dispatcher then only enforces per-(station,model) limits, if any).
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # All (this-station, model) bindings. Deleting a station deletes its
    # bindings — those rows have no meaning without the parent.
    models: Mapped[list["RelayStationModel"]] = relationship(
        back_populates="relay_station",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RelayStationModel(Base):
    """Join row binding one `RelayStation` to one `ImageModel`.

    Beyond saying "this station serves this model", the row carries:

    * Optional **per-(station, model) overrides** for cost / rpm / concurrency.
      A NULL override means "fall back to the station-level value" — see the
      effective-value rules below.
    * A **`param_capabilities`** JSON document that *narrows* the parent
      `ImageModel.param_schema` for this specific station. Different stations
      reselling the same model often expose different parameter subsets
      (e.g. one station gates `quality=high`, another doesn't accept `size`).

    Effective-value resolution
    --------------------------
    For numeric/scalar overrides on this row, the runtime should use::

        effective = row.<field> if row.<field> is not None else station.<field>

    so e.g. `effective_rpm_limit = self.rpm_limit or self.relay_station.rpm_limit`.

    Effective parameter capabilities (merge of `param_schema` + `param_capabilities`)
    --------------------------------------------------------------------------------
    Start from the model's `param_schema` (the superset). For each key in
    `param_capabilities`:

    * ``{"supported": false}`` → hard-disable that param for this station,
      even if the model supports it. The UI must not render a control.
    * ``{"supported": true, "values": [...]}`` → the station accepts only this
      subset of the model's allowed values. Intersect with
      `param_schema[key]["values"]` defensively.
    * ``{"supported": true, "min": .., "max": ..}`` → narrows the numeric
      range. Clamp to the model's range as a sanity check.
    * Key absent from `param_capabilities` → use the model-level entry as-is.

    Example (`param_capabilities` for a station that resells `gpt-image-2`)::

        {
          "size":         {"supported": true,  "values": ["1024x1024","1024x1536"]},
          "quality":      {"supported": true,  "values": ["auto","low","medium"]},
          "background":   {"supported": false},
          "n":            {"supported": true,  "min": 1, "max": 4}
        }
    """

    __tablename__ = "relay_station_models"
    __table_args__ = (
        # A given relay station serves a given model at most once. Without this
        # we could end up with multiple conflicting capability rows for the
        # same pairing.
        UniqueConstraint(
            "relay_station_id", "image_model_id", name="uq_relay_station_image_model"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # CASCADE on both sides: this row is meaningless without either parent.
    relay_station_id: Mapped[int] = mapped_column(
        ForeignKey("relay_stations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    image_model_id: Mapped[int] = mapped_column(
        ForeignKey("image_models.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Per-pairing toggle. The dispatcher should treat this binding as routable
    # only if `self.enabled AND self.relay_station.enabled AND self.image_model.enabled`.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ---- Per-(station, model) overrides. NULL = inherit from `relay_station`. ----
    # Override the price for this specific model on this station (some stations
    # charge different prices for different upstream models).
    cost_per_image_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    # Override RPM cap when this station serves this specific model. Useful
    # when an upstream rate-limits per-model rather than per-account.
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Override concurrency cap when this station serves this specific model.
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # JSON narrowing of the parent model's `param_schema`. See class docstring
    # for the merge rules. NULL means "no narrowing — use the model's schema
    # verbatim".
    param_capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    relay_station: Mapped[RelayStation] = relationship(back_populates="models")
    image_model: Mapped[ImageModel] = relationship(back_populates="station_bindings")
