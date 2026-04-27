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
    """Per-(station × model) config: which models a relay station serves, and the
    parameter capabilities (size / quality / aspect_ratio / ...) for that pairing.

    `param_capabilities` is a JSON document describing which generation parameters
    this station accepts for this model and their allowed values, e.g.::

        {
          "size":         {"supported": true,  "values": ["1024x1024", "auto"]},
          "aspect_ratio": {"supported": true,  "values": ["1:1", "16:9"]},
          "image_size":   {"supported": false},
          "quality":      {"supported": true,  "values": ["auto","low","medium","high"]},
          "n":            {"supported": true,  "min": 1, "max": 12}
        }
    """

    __tablename__ = "relay_station_models"
    __table_args__ = (
        UniqueConstraint("relay_station_id", "model_id", name="uq_relay_station_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    relay_station_id: Mapped[int] = mapped_column(
        ForeignKey("relay_stations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # per-model overrides; NULL means "fall back to the station-level value"
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
