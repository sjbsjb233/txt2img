"""SQLAlchemy async engine and session factory.

The backend uses SQLite with the ``aiosqlite`` driver. We keep a single
process-wide engine + sessionmaker pair behind module-level globals so that
domain services can call ``get_session()`` from anywhere without juggling
DI plumbing. (The design doc explicitly chooses single-worker uvicorn for v1
exactly so this kind of in-process state is safe — see §1.4.)

Connection-time pragmas — ``journal_mode=WAL``, ``synchronous=NORMAL``,
``foreign_keys=ON``, ``busy_timeout=5000`` — are applied to every new
connection via the SQLAlchemy ``connect`` event, which is the only reliable
way to set them: each ``aiosqlite`` connection is its own SQLite handle and
PRAGMAs do not propagate.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

logger = logging.getLogger("txt2img.db")

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    """Apply per-connection SQLite pragmas.

    Runs synchronously on the underlying DB-API (aiosqlite) connection at
    pool checkout time. We open a short-lived cursor instead of using
    ``connection.execute`` because aiosqlite's sync proxy is the only API
    available inside this hook.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _ensure_sqlite_parent_dir(db_url: str) -> None:
    """For ``sqlite+aiosqlite:////absolute/path/foo.db`` URLs, mkdir -p the parent."""
    prefix = "sqlite+aiosqlite:///"
    if not db_url.startswith(prefix):
        return
    path = db_url[len(prefix) :]
    if path.startswith("/"):
        absolute_path = path
    else:
        # Three-slash form: relative path. Nothing to ensure beyond cwd.
        return
    parent = os.path.dirname(absolute_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def init_engine(db_url: Optional[str] = None) -> AsyncEngine:
    """Create the process-wide engine and session factory.

    Idempotent: calling twice with the same URL returns the existing engine.
    Calling with a different URL closes the old engine first — used by tests
    that swap the DB between cases.
    """
    global _engine, _session_factory

    settings = get_settings()
    url = db_url or settings.DB_URL

    if _engine is not None:
        # Already initialised. Tests that need a different URL must call
        # close_engine() first.
        return _engine

    _ensure_sqlite_parent_dir(url)

    # ``check_same_thread`` is a SQLite-only kwarg; aiosqlite already
    # serialises through its own thread, so we keep the default. We disable
    # SQLAlchemy's NullPool default and use the standard pool because we
    # benefit from connection reuse (and the pragmas only need to be set
    # once per pooled connection).
    _engine = create_async_engine(
        url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )

    # event.listens_for needs the *sync* Engine; AsyncEngine wraps one.
    sync_engine: Engine = _engine.sync_engine
    event.listen(sync_engine, "connect", _set_sqlite_pragmas)

    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    logger.info("db engine initialised url=%s", _redact_url(url))
    return _engine


async def close_engine() -> None:
    """Dispose the engine on shutdown. Safe to call when never inited."""
    global _engine, _session_factory
    if _engine is None:
        return
    await _engine.dispose()
    _engine = None
    _session_factory = None
    logger.info("db engine closed")


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("db engine not initialised; call init_engine() first")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("db engine not initialised; call init_engine() first")
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Open an ``AsyncSession`` scoped to a single unit of work.

    Commits on clean exit, rolls back on exception, always closes.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _redact_url(url: str) -> str:
    """Hide credentials in DB URLs before logging. SQLite URLs have none, but
    if we ever switch to Postgres we won't accidentally print the password."""
    at = url.rfind("@")
    if at == -1:
        return url
    scheme_end = url.find("://")
    if scheme_end == -1:
        return url
    return url[: scheme_end + 3] + "***@" + url[at + 1 :]
