"""Database layer: PostgreSQL when DATABASE_URL is set, JSON files otherwise.

`is_pg_enabled()` is the single switch every store branches on. When it is
False the existing JSON/JSONL paths run unchanged, which is what keeps the
test suite and a no-database deploy working.
"""
from src.db.engine import (
    database_url,
    get_engine,
    get_session_factory,
    is_pg_enabled,
    reset_engine,
)

__all__ = [
    "database_url",
    "get_engine",
    "get_session_factory",
    "is_pg_enabled",
    "reset_engine",
]
