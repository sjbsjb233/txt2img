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
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ImageModel(Base):
    """Catalog of the image-generation models the system knows about (the fixed
    set of ~5 models, e.g. ``gpt-image-2``, ``gemini-2.5-flash-image``,
    ``gemini-3-pro-image-preview`` ...).

    ``param_schema`` is the *canonical* / superset description of every
    parameter this model can ever accept (used by the frontend to know what
    UI controls to render in principle). Each relay station then narrows
    that further on a per-(station, model) basis via
    ``RelayStationModel.param_capabilities``.

    Example ``param_schema``::

        {
          "size":         {"values": ["1024x1024","1024x1536","1536x1024","auto"]},
          "quality":      {"values": ["auto","low","medium","high"]},
          "n":            {"min": 1, "max": 10},
          "background":   {"values": ["auto","transparent","opaque"]},
          "output_format":{"values": ["png","jpeg","webp"]}
        }
    """

    __tablename__ = "image_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    model_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    param_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    station_bindings: Mapped[list["RelayStationModel"]] = relationship(
        back_populates="image_model",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RelayStation(Base):
    """A 中转站 (upstream API relay) that image-generation requests can be routed to."""

    __tablename__ = "relay_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    provider_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)

    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)

    cost_per_image_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    initial_balance_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    balance_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    max_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    models: Mapped[list["RelayStationModel"]] = relationship(
        back_populates="relay_station",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RelayStationModel(Base):
    """Join row: which model (from ``image_models``) a given relay station serves,
    and the station's *narrowing* of the model's parameter schema for that
    pairing.

    ``param_capabilities`` is a JSON document describing which of the model's
    parameters this station actually exposes and their allowed values for this
    station, e.g. for a station that gates ``gpt-image-2``::

        {
          "size":         {"supported": true,  "values": ["1024x1024","1024x1536"]},
          "quality":      {"supported": true,  "values": ["auto","low","medium"]},
          "background":   {"supported": false},
          "n":            {"supported": true,  "min": 1, "max": 4}
        }

    A missing key falls back to the model-level entry in
    ``ImageModel.param_schema``; ``"supported": false`` hard-disables a param
    for this station even if the model itself supports it.
    """

    __tablename__ = "relay_station_models"
    __table_args__ = (
        UniqueConstraint(
            "relay_station_id", "image_model_id", name="uq_relay_station_image_model"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

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

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # per-(station, model) overrides; NULL means "fall back to the station-level value"
    cost_per_image_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
