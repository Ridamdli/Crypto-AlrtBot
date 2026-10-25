"""Initial schema: 19 tables for the JSON -> PostgreSQL migration.

Revision 0001. No data — backfill is scripts/migrate_json_to_pg.py.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exit_decisions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("decision_key", sa.String(64), nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("data_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("decided_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("code_version", sa.String(32), nullable=True),
        sa.Column("config_version", sa.String(64), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("assessments", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("risk_guard", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("rejected", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("events_applied", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("plan_after", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("linked_outcome", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_exit_decisions_decision_key", "exit_decisions", ["decision_key"], unique=True)
    op.create_index("ix_exit_decisions_signal_id", "exit_decisions", ["signal_id"], unique=False)
    op.create_table(
        "exit_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("decision_key", sa.String(64), nullable=True),
        sa.Column("data_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("decided_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_exit_events_signal_id", "exit_events", ["signal_id"], unique=False)
    op.create_table(
        "exit_last_eval",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("data_ts", sa.BigInteger(), nullable=True),
        sa.Column("at_ms", sa.BigInteger(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "exit_plans",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("basis_decision_key", sa.String(64), nullable=True),
        sa.Column("decided_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("plan", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "exit_seen_keys",
        sa.Column("decision_key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "invalidated_signals",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "invo_activity",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("ts", sa.BigInteger(), nullable=True),
        sa.Column("event", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_invo_activity_ts", "invo_activity", ["ts"], unique=False)
    op.create_table(
        "invo_exit_sync",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("state", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "invo_shares",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "invo_sync_audit",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.String(160), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("signal_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("event", sa.String(32), nullable=True),
        sa.Column("req_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("resp_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=True),
        sa.Column("confirmation", sa.String(32), nullable=True),
        sa.Column("final_state", sa.String(32), nullable=True),
        sa.Column("worker", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_invo_sync_audit_correlation_id", "invo_sync_audit", ["correlation_id"], unique=False)
    op.create_table(
        "locks",
        sa.Column("name", sa.String(64), primary_key=True, nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("token", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.Float(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "outcomes",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("signal_timestamp", sa.String(64), nullable=True),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("tp1", sa.Float(), nullable=True),
        sa.Column("tp2", sa.Float(), nullable=True),
        sa.Column("tp3", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("btc_regime", sa.String(32), nullable=True),
        sa.Column("leverage", sa.Integer(), nullable=True),
        sa.Column("margin", sa.Float(), nullable=True),
        sa.Column("position_notional", sa.Float(), nullable=True),
        sa.Column("risk_amount", sa.Float(), nullable=True),
        sa.Column("evaluated_at", sa.String(64), nullable=True),
        sa.Column("future_bars_seen", sa.Integer(), nullable=True),
        sa.Column("final", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("entry_triggered", sa.Boolean(), nullable=True),
        sa.Column("expired", sa.Boolean(), nullable=True),
        sa.Column("first_event", sa.String(16), nullable=True),
        sa.Column("actual_entry_price", sa.Float(), nullable=True),
        sa.Column("tp1_hit", sa.Boolean(), nullable=True),
        sa.Column("tp2_hit", sa.Boolean(), nullable=True),
        sa.Column("tp3_hit", sa.Boolean(), nullable=True),
        sa.Column("sl_hit", sa.Boolean(), nullable=True),
        sa.Column("liquidated", sa.Boolean(), nullable=True),
        sa.Column("liq_price", sa.Float(), nullable=True),
        sa.Column("mfe_r", sa.Float(), nullable=True),
        sa.Column("mae_r", sa.Float(), nullable=True),
        sa.Column("holding_bars", sa.Integer(), nullable=True),
        sa.Column("holding_minutes", sa.Float(), nullable=True),
        sa.Column("realized_r", sa.Float(), nullable=True),
        sa.Column("net_realized_r", sa.Float(), nullable=True),
        sa.Column("fees_cost_r", sa.Float(), nullable=True),
        sa.Column("close_timestamp", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_outcomes_status", "outcomes", ["status"], unique=False)
    op.create_index("ix_outcomes_symbol", "outcomes", ["symbol"], unique=False)
    op.create_table(
        "p3_shadow",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("ts", sa.BigInteger(), nullable=True),
        sa.Column("signal_id", sa.String(128), nullable=True),
        sa.Column("coin", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_p3_shadow_ts", "p3_shadow", ["ts"], unique=False)
    op.create_table(
        "paper_booked",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("signal_timestamp", sa.String(64), nullable=True),
        sa.Column("settled_at", sa.String(64), nullable=True),
        sa.Column("close_timestamp", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("entry_triggered", sa.Boolean(), nullable=True),
        sa.Column("liquidated", sa.Boolean(), nullable=True),
        sa.Column("first_event", sa.String(16), nullable=True),
        sa.Column("leverage", sa.Integer(), nullable=True),
        sa.Column("position_notional", sa.Float(), nullable=True),
        sa.Column("position_pct_capital", sa.Float(), nullable=True),
        sa.Column("net_r", sa.Float(), nullable=True),
        sa.Column("risk_amount", sa.Float(), nullable=True),
        sa.Column("pnl_usd", sa.Float(), nullable=True),
        sa.Column("equity_after", sa.Float(), nullable=True),
        sa.Column("holding_minutes", sa.Float(), nullable=True),
        sa.Column("liq_price", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_paper_booked_symbol", "paper_booked", ["symbol"], unique=False)
    op.create_table(
        "paper_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("starting_equity", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("peak_equity", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "scan_history",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("at", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "scheduler_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("published_signals", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("timestamp", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("configuration_hash", sa.String(128), nullable=True),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("tp", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("margin", sa.Float(), nullable=False),
        sa.Column("position_notional", sa.Float(), nullable=False),
        sa.Column("risk_amount", sa.Float(), nullable=False),
        sa.Column("risk_reward", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("btc_regime", sa.String(32), nullable=True),
        sa.Column("scoring_breakdown", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("conditions", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("invalidation_conditions", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("management_rules", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )
    op.create_index("ix_signals_symbol", "signals", ["symbol"], unique=False)
    op.create_table(
        "sim_reservations",
        sa.Column("signal_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("duplicate", sa.Boolean(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reserved_at", sa.Float(), nullable=True),
        sa.Column("released_at", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("window", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sim_reservations")
    op.drop_index("ix_signals_symbol", table_name="signals")
    op.drop_table("signals")
    op.drop_table("scheduler_state")
    op.drop_table("scan_history")
    op.drop_table("paper_state")
    op.drop_index("ix_paper_booked_symbol", table_name="paper_booked")
    op.drop_table("paper_booked")
    op.drop_index("ix_p3_shadow_ts", table_name="p3_shadow")
    op.drop_table("p3_shadow")
    op.drop_index("ix_outcomes_status", table_name="outcomes")
    op.drop_index("ix_outcomes_symbol", table_name="outcomes")
    op.drop_table("outcomes")
    op.drop_table("locks")
    op.drop_index("ix_invo_sync_audit_correlation_id", table_name="invo_sync_audit")
    op.drop_table("invo_sync_audit")
    op.drop_table("invo_shares")
    op.drop_table("invo_exit_sync")
    op.drop_index("ix_invo_activity_ts", table_name="invo_activity")
    op.drop_table("invo_activity")
    op.drop_table("invalidated_signals")
    op.drop_table("exit_seen_keys")
    op.drop_table("exit_plans")
    op.drop_table("exit_last_eval")
    op.drop_index("ix_exit_events_signal_id", table_name="exit_events")
    op.drop_table("exit_events")
    op.drop_index("ix_exit_decisions_decision_key", table_name="exit_decisions")
    op.drop_index("ix_exit_decisions_signal_id", table_name="exit_decisions")
    op.drop_table("exit_decisions")
