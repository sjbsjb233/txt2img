from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    tier: int
    is_active: bool
    created_at: datetime


class CaptchaCheckIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class CaptchaCheckOut(BaseModel):
    required: bool


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    captcha_token: str | None = None


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    is_admin: bool = False
    tier: int = 0


class UserUpdateIn(BaseModel):
    is_admin: bool | None = None
    tier: int | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256)


class OkOut(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Image-model catalog (used by the Create page to render parameter controls)
# ---------------------------------------------------------------------------


class StationOption(BaseModel):
    """One relay station that serves the parent model.

    ``effective_param_schema`` is the per-(model, station) merge of the model's
    full ``param_schema`` and the station's narrowing ``param_capabilities``.
    The frontend should render controls from this when the user picks this
    station explicitly; for the synthetic ``"auto"`` option it should fall back
    to the parent ``ModelOption.param_schema`` (the most permissive view).
    """

    model_config = ConfigDict(from_attributes=True)

    provider_id: str
    label: str
    cost_per_image_cny: Decimal | None = None
    balance_cny: Decimal
    param_capabilities: dict[str, Any] | None = None
    effective_param_schema: dict[str, Any]


class ModelOption(BaseModel):
    # protected_namespaces=() avoids Pydantic's warning about ``model_key`` /
    # ``model_config`` overlapping its reserved ``model_*`` namespace.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    model_key: str
    label: str
    family: str
    sort_order: int
    note: str | None = None
    param_schema: dict[str, Any] | None = None
    stations: list[StationOption]


class ModelCatalogOut(BaseModel):
    models: list[ModelOption]


# ---------------------------------------------------------------------------
# Generation request / response
# ---------------------------------------------------------------------------


class InputImage(BaseModel):
    mime_type: str = Field(min_length=1, max_length=64)
    b64: str = Field(min_length=1)


class GenerationIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_key: str = Field(min_length=1, max_length=128)
    # "auto" picks a routable station at random; otherwise a relay's provider_id.
    station: str = Field(default="auto", min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    params: dict[str, Any] = Field(default_factory=dict)
    input_images: list[InputImage] | None = None


class GeneratedImageOut(BaseModel):
    mime_type: str
    b64: str


class GenerationOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_key: str
    station_provider_id: str
    images: list[GeneratedImageOut]
    revised_prompt: str | None = None
    usage: dict[str, Any] | None = None
