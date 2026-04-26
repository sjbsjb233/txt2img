import logging

from sqlalchemy.orm import Session

from .config import settings
from .db import Base, SessionLocal, engine
from .models import User
from .security import hash_password

logger = logging.getLogger(__name__)


def create_all() -> None:
    Base.metadata.create_all(bind=engine)


def bootstrap_admin(db: Session) -> None:
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        logger.warning(
            "ADMIN_USERNAME / ADMIN_PASSWORD not set; skipping admin bootstrap."
        )
        return
    existing = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    if existing is not None:
        return
    admin = User(
        username=settings.ADMIN_USERNAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        is_admin=True,
        tier=99,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    logger.info("Bootstrapped admin user '%s'", settings.ADMIN_USERNAME)


def init() -> None:
    create_all()
    with SessionLocal() as db:
        bootstrap_admin(db)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init()
    print("Database initialised.")
