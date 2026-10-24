"""Sync SQLAlchemy engine factory tuned for Neon (scale-to-zero Postgres).

One module-level engine per process. `get_engine()` is lazy because importing
this module must not open a connection — most of the test suite and every
JSON-fallback run never touches a database.

Cold start: Neon suspends an idle branch and the first connection pays a
resume penalty, so engine creation probes with `SELECT 1` and retries
connection errors only (3 attempts, 0.5/1/2 s). A constraint or SQL error is
never retried.
"""
import os
import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.utils.logger import get_logger

logger = get_logger(__name__)

COLD_START_ATTEMPTS = 3
COLD_START_BACKOFF_S = (0.5, 1.0, 2.0)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def database_url() -> str | None:
    """Effective database URL, or None when the JSON path must be used.

    A bare `postgresql://` URL (what Neon hands out) would select psycopg2,
    which is not installed, so the scheme is pinned to the psycopg3 driver.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return None
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def is_pg_enabled() -> bool:
    """True when DATABASE_URL is set; every store branches on this."""
    return database_url() is not None


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # In-memory SQLite is the test backend: one shared connection keeps
        # the schema visible to every session in the process.
        return {"connect_args": {"check_same_thread": False},
                "poolclass": StaticPool}
    connect_args = {"connect_timeout": 10, "application_name": "crypto-alrtbot"}
    if "-pooler" in url:
        # PgBouncer transaction pooling cannot hold session-level prepared
        # statements, so psycopg must not create them.
        connect_args["prepare_threshold"] = None
    return {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 10,
        "connect_args": connect_args,
    }


def _probe(engine: Engine) -> None:
    """Force one connection, retrying only cold-start connection failures."""
    for attempt in range(1, COLD_START_ATTEMPTS + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            if attempt == COLD_START_ATTEMPTS:
                raise
            delay = COLD_START_BACKOFF_S[min(attempt - 1, len(COLD_START_BACKOFF_S) - 1)]
            logger.warning(
                f"[DB] cold-start attempt {attempt}/{COLD_START_ATTEMPTS} failed "
                f"({exc.__class__.__name__}); retrying in {delay}s"
            )
            time.sleep(delay)


def get_engine() -> Engine:
    """Process-wide engine. Raises if DATABASE_URL is unset — callers must
    check `is_pg_enabled()` first, because that is the JSON fallback switch."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                url = database_url()
                if url is None:
                    raise RuntimeError(
                        "DATABASE_URL is not set; use the JSON path "
                        "(is_pg_enabled() is False)"
                    )
                engine = create_engine(url, **_engine_kwargs(url))
                _probe(engine)
                _engine = engine
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Session factory bound to the process engine."""
    global _session_factory
    if _session_factory is None:
        with _lock:
            if _session_factory is None:
                _session_factory = sessionmaker(
                    bind=get_engine(), expire_on_commit=False, future=True
                )
    return _session_factory


def reset_engine() -> None:
    """Drop the cached engine and factory (tests, or a URL change)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
