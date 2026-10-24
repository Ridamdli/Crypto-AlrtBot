"""Declarative base and shared column types for the Postgres schema.

JSONType is JSON on SQLite (in-memory test backend) and JSONB on PostgreSQL,
so one model definition serves both.
"""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

JSONType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass
