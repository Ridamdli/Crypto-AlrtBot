"""SQLAlchemy models for the Postgres migration (Step 2.2).

Every table carries the verbatim original row in `payload`/JSONB alongside
promoted columns used for queries, plus the provenance columns from
ProvenanceMixin (`source_file`, `ingested_at`, `payload_sha256`, `window`).
Promoted columns are derived from `payload`; if they ever disagree, `payload`
is authoritative — that is what makes the migration lossless.

Column types are dialect-adaptive: JSONType is JSONB on PostgreSQL and JSON
on SQLite, BigIntPK is BIGSERIAL on PostgreSQL and INTEGER PRIMARY KEY on
SQLite (which does not autoincrement BIGINT primary keys).
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, JSONType

# sqlite only autoincrements INTEGER primary keys, so the bigserial columns
# fall back to INTEGER there. Test backend only; Postgres gets BIGSERIAL.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class ProvenanceMixin:
    """Migration provenance. Runtime-written rows leave these NULL."""

    source_file: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    # 'W1' = migrated Window 1 archive. NULL = written by the running service.
    window: Mapped[str | None] = mapped_column(String(16))


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------
class Signal(Base, ProvenanceMixin):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    timestamp: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    strategy_id: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    configuration_hash: Mapped[str | None] = mapped_column(String(128))
    entry: Mapped[float] = mapped_column(Float)
    tp: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer)
    margin: Mapped[float] = mapped_column(Float)
    position_notional: Mapped[float] = mapped_column(Float)
    risk_amount: Mapped[float] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float)
    confidence: Mapped[int] = mapped_column(Integer)
    btc_regime: Mapped[str | None] = mapped_column(String(32))
    scoring_breakdown: Mapped[dict | None] = mapped_column(JSONType)
    conditions: Mapped[dict | None] = mapped_column(JSONType)
    invalidation_conditions: Mapped[dict | None] = mapped_column(JSONType)
    management_rules: Mapped[dict | None] = mapped_column(JSONType)
    payload: Mapped[dict] = mapped_column(JSONType)


# --------------------------------------------------------------------------
# exit ledger (one JSON file in the file backend)
# --------------------------------------------------------------------------
class ExitDecision(Base, ProvenanceMixin):
    __tablename__ = "exit_decisions"

    # Surrogate key, not decision_key: decisions_for()/the 2,000 cap need
    # insertion order, and decided_at_ms ties on monitor-only rows.
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    decision_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    signal_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(32))
    data_ts_ms: Mapped[int | None] = mapped_column(BigInteger)
    decided_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    code_version: Mapped[str | None] = mapped_column(String(32))
    config_version: Mapped[str | None] = mapped_column(String(64))
    rationale: Mapped[str | None] = mapped_column(Text)
    assessments: Mapped[list | None] = mapped_column(JSONType)
    risk_guard: Mapped[dict | None] = mapped_column(JSONType)
    rejected: Mapped[list | None] = mapped_column(JSONType)
    events_applied: Mapped[list | None] = mapped_column(JSONType)
    plan_after: Mapped[dict | None] = mapped_column(JSONType)
    linked_outcome: Mapped[dict | None] = mapped_column(JSONType)
    payload: Mapped[dict] = mapped_column(JSONType)


class ExitPlan(Base, ProvenanceMixin):
    __tablename__ = "exit_plans"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    basis_decision_key: Mapped[str | None] = mapped_column(String(64))
    decided_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[dict] = mapped_column(JSONType)


class ExitEvent(Base, ProvenanceMixin):
    __tablename__ = "exit_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str | None] = mapped_column(String(32))
    decision_key: Mapped[str | None] = mapped_column(String(64))
    data_ts_ms: Mapped[int | None] = mapped_column(BigInteger)
    decided_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONType)


class InvalidatedSignal(Base, ProvenanceMixin):
    __tablename__ = "invalidated_signals"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class ExitLastEval(Base, ProvenanceMixin):
    __tablename__ = "exit_last_eval"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data_ts: Mapped[int | None] = mapped_column(BigInteger)
    at_ms: Mapped[int | None] = mapped_column(BigInteger)


class ExitSeenKey(Base, ProvenanceMixin):
    """Idempotency keys, unbounded — the file backend's cap is not reproduced."""

    __tablename__ = "exit_seen_keys"

    decision_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------
class Outcome(Base, ProvenanceMixin):
    __tablename__ = "outcomes"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(8))
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    signal_timestamp: Mapped[str | None] = mapped_column(String(64))
    entry: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float | None] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    tp3: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[int | None] = mapped_column(Integer)
    btc_regime: Mapped[str | None] = mapped_column(String(32))
    leverage: Mapped[int | None] = mapped_column(Integer)
    margin: Mapped[float | None] = mapped_column(Float)
    position_notional: Mapped[float | None] = mapped_column(Float)
    risk_amount: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[str | None] = mapped_column(String(64))
    future_bars_seen: Mapped[int | None] = mapped_column(Integer)
    final: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    entry_triggered: Mapped[bool | None] = mapped_column(Boolean)
    expired: Mapped[bool | None] = mapped_column(Boolean)
    first_event: Mapped[str | None] = mapped_column(String(16))
    actual_entry_price: Mapped[float | None] = mapped_column(Float)
    tp1_hit: Mapped[bool | None] = mapped_column(Boolean)
    tp2_hit: Mapped[bool | None] = mapped_column(Boolean)
    tp3_hit: Mapped[bool | None] = mapped_column(Boolean)
    sl_hit: Mapped[bool | None] = mapped_column(Boolean)
    liquidated: Mapped[bool | None] = mapped_column(Boolean)
    liq_price: Mapped[float | None] = mapped_column(Float)
    mfe_r: Mapped[float | None] = mapped_column(Float)
    mae_r: Mapped[float | None] = mapped_column(Float)
    holding_bars: Mapped[int | None] = mapped_column(Integer)
    holding_minutes: Mapped[int | None] = mapped_column(Integer)
    realized_r: Mapped[float | None] = mapped_column(Float)
    net_realized_r: Mapped[float | None] = mapped_column(Float)
    fees_cost_r: Mapped[float | None] = mapped_column(Float)
    close_timestamp: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType)


# --------------------------------------------------------------------------
# paper trading
# --------------------------------------------------------------------------
class PaperBooked(Base, ProvenanceMixin):
    __tablename__ = "paper_booked"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    side: Mapped[str | None] = mapped_column(String(8))
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    signal_timestamp: Mapped[str | None] = mapped_column(String(64))
    settled_at: Mapped[str | None] = mapped_column(String(64))
    close_timestamp: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    entry_triggered: Mapped[bool | None] = mapped_column(Boolean)
    liquidated: Mapped[bool | None] = mapped_column(Boolean)
    first_event: Mapped[str | None] = mapped_column(String(16))
    leverage: Mapped[int | None] = mapped_column(Integer)
    position_notional: Mapped[float | None] = mapped_column(Float)
    position_pct_capital: Mapped[float | None] = mapped_column(Float)
    net_r: Mapped[float | None] = mapped_column(Float)
    risk_amount: Mapped[float | None] = mapped_column(Float)
    pnl_usd: Mapped[float | None] = mapped_column(Float)
    equity_after: Mapped[float | None] = mapped_column(Float)
    holding_minutes: Mapped[int | None] = mapped_column(Integer)
    liq_price: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONType)


class PaperState(Base, ProvenanceMixin):
    """Single row (id=1). `version` is the optimistic lock for settle()."""

    __tablename__ = "paper_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    starting_equity: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    peak_equity: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# --------------------------------------------------------------------------
# scheduler / scan history
# --------------------------------------------------------------------------
class SchedulerState(Base, ProvenanceMixin):
    __tablename__ = "scheduler_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cooldown_hours: Mapped[float | None] = mapped_column(Float)
    published_signals: Mapped[dict] = mapped_column(JSONType)


class ScanHistory(Base, ProvenanceMixin):
    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType)


# --------------------------------------------------------------------------
# Invo
# --------------------------------------------------------------------------
class SimReservation(Base, ProvenanceMixin):
    __tablename__ = "sim_reservations"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(16))
    duplicate: Mapped[bool | None] = mapped_column(Boolean)
    evidence: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    reserved_at: Mapped[float | None] = mapped_column(Float)
    released_at: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONType)


class InvoShare(Base, ProvenanceMixin):
    __tablename__ = "invo_shares"

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONType)


class InvoExitSync(Base, ProvenanceMixin):
    __tablename__ = "invo_exit_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state: Mapped[dict] = mapped_column(JSONType)


class InvoSyncAudit(Base, ProvenanceMixin):
    __tablename__ = "invo_sync_audit"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    seq: Mapped[int | None] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(160))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    signal_id: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str | None] = mapped_column(String(32))
    event: Mapped[str | None] = mapped_column(String(32))
    req_ts_ms: Mapped[int | None] = mapped_column(BigInteger)
    resp_ts_ms: Mapped[int | None] = mapped_column(BigInteger)
    plan_version: Mapped[int | None] = mapped_column(Integer)
    confirmation: Mapped[str | None] = mapped_column(String(32))
    final_state: Mapped[str | None] = mapped_column(String(32))
    worker: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType)


class InvoActivity(Base, ProvenanceMixin):
    __tablename__ = "invo_activity"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[int | None] = mapped_column(BigInteger, index=True)
    event: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType)


class P3Shadow(Base, ProvenanceMixin):
    __tablename__ = "p3_shadow"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[int | None] = mapped_column(BigInteger, index=True)
    signal_id: Mapped[str | None] = mapped_column(String(128))
    coin: Mapped[str | None] = mapped_column(String(32))
    side: Mapped[str | None] = mapped_column(String(8))
    verdict: Mapped[str | None] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONType)


# --------------------------------------------------------------------------
# locks
# --------------------------------------------------------------------------
class Lock(Base, ProvenanceMixin):
    """Postgres-side advisory bookkeeping; the PG lock itself is pg_try_advisory_lock."""

    __tablename__ = "locks"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(128))
    token: Mapped[str | None] = mapped_column(String(64))
    acquired_at: Mapped[float | None] = mapped_column(Float)
