from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import (
    CaptchaCheckIn,
    CaptchaCheckOut,
    LoginIn,
    LoginOut,
    OkOut,
    UserOut,
)
from ..security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

CAPTCHA_FAIL_THRESHOLD = 3
CAPTCHA_FAIL_WINDOW = timedelta(minutes=15)


def _captcha_required_for(user: User | None) -> bool:
    if settings.FORCE_CAPTCHA:
        return True
    if user is None or user.last_failed_login_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - CAPTCHA_FAIL_WINDOW
    last = user.last_failed_login_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return user.failed_login_attempts >= CAPTCHA_FAIL_THRESHOLD and last >= cutoff


@router.post("/captcha-check", response_model=CaptchaCheckOut)
def captcha_check(payload: CaptchaCheckIn, db: Session = Depends(get_db)) -> CaptchaCheckOut:
    user = db.query(User).filter(User.username == payload.username).first()
    return CaptchaCheckOut(required=_captcha_required_for(user))


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> LoginOut:
    user = db.query(User).filter(User.username == payload.username).first()

    creds_ok = user is not None and verify_password(payload.password, user.password_hash)

    if not creds_ok:
        if user is not None:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            user.last_failed_login_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials"
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_inactive")

    if _captcha_required_for(user) and not (payload.captcha_token or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="captcha_required"
        )

    user.failed_login_attempts = 0
    user.last_failed_login_at = None
    db.commit()
    db.refresh(user)

    token = create_access_token(
        sub=str(user.id), username=user.username, is_admin=user.is_admin
    )
    return LoginOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current)


@router.post("/logout", response_model=OkOut)
def logout(_: User = Depends(get_current_user)) -> OkOut:
    return OkOut()
