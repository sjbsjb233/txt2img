from datetime import datetime

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
