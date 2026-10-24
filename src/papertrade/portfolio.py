"""
Paper-trading portfolio (live test without real money or exchange keys).

How it works:
- Every published signal is a virtual position, sized by the signal's own
  risk_amount (default $10 = 1% of the $1,000 starting bank).
- Fills/outcomes come from the SAME SignalOutcomeEvaluator as research,
  with the same taker fee (0.04%) and slippage (0.02%) assumptions.
- A trade settles into equity only when its outcome is FINAL
  (TP3_HIT / STOPPED_OUT / fairly-expired / bars exhausted).
- P&L per trade = net_realized_r x risk_amount.

Simplifications (v1, documented honestly):
- Unlimited simultaneous positions (no margin calls).
- Fixed risk per trade (no compounding of position size).
- Limit-entry fills per evaluator rules (entry must touch within 6 bars).

State: data_store/paper_portfolio.json, or — when DATABASE_URL is set — the
paper_state (single row) + paper_booked tables. paper_state carries a
`version`; settle() uses it as an optimistic lock (conditional UPDATE, retry
once, then raise) because settlement is read-modify-write on shared equity.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.db import get_session_factory, is_pg_enabled
from src.db import models as db
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_STARTING_EQUITY = 1000.0
DEFAULT_RISK_PER_TRADE = 10.0


class _StaleState(Exception):
    """paper_state moved under us between read and conditional UPDATE."""


class PaperPortfolio:
    def __init__(
        self,
        storage_dir: str = "data_store",
        ledger_name: str = "paper_portfolio.json",
        starting_equity: float = DEFAULT_STARTING_EQUITY,
        risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    ):
        self.storage_dir = storage_dir
        self.ledger_path = os.path.join(storage_dir, ledger_name)
        self.starting_equity = starting_equity
        self.default_risk = risk_per_trade
        self._pg = is_pg_enabled()
        # No disk on a serverless deploy: only the JSON backend needs a dir.
        if not self._pg:
            os.makedirs(self.storage_dir, exist_ok=True)

    # ── ledger ──────────────────────────────────────────────────────────
    def load(self) -> dict[str, Any]:
        if self._pg:
            try:
                with self._pg_session() as s:
                    state = s.get(db.PaperState, 1)
                    booked = self._pg_booked(s)
            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"[Paper] Failed to load portfolio ledger: {e}")
                state, booked = None, {}
            return {
                "starting_equity": (state.starting_equity if state
                                    else self.starting_equity),
                "equity": (state.equity if state else self.starting_equity),
                "peak_equity": (state.peak_equity if state else self.starting_equity),
                "booked": booked,
            }
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "booked" in data:
                    return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {
            "starting_equity": self.starting_equity,
            "equity": self.starting_equity,
            "peak_equity": self.starting_equity,
            "booked": {},  # signal_id -> settled trade record
        }

    def save(self, ledger: dict[str, Any]) -> None:
        if self._pg:
            # Explicit snapshot overwrite: no version precondition here. Only
            # settle() (the concurrent path) uses the optimistic lock.
            try:
                with self._pg_session() as s, s.begin():
                    vals = {"starting_equity": ledger.get("starting_equity",
                                                          self.starting_equity),
                            "equity": ledger.get("equity", self.starting_equity),
                            "peak_equity": ledger.get("peak_equity",
                                                      self.starting_equity)}
                    if s.get(db.PaperState, 1) is None:
                        s.add(db.PaperState(id=1, version=0, **vals))
                    else:
                        s.execute(update(db.PaperState)
                                  .where(db.PaperState.id == 1)
                                  .values(version=db.PaperState.version + 1, **vals))
                    for sid, rec in (ledger.get("booked") or {}).items():
                        s.merge(db.PaperBooked(signal_id=sid,
                                               payload=rec,
                                               **_booked_columns(rec)))
            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"[Paper] Failed to save portfolio ledger: {e}")
            return
        try:
            from src.utils.atomic import atomic_write_json
            atomic_write_json(self.ledger_path, ledger)
        except OSError as e:
            logger.error(f"[Paper] Failed to save portfolio ledger: {e}")

    # ── postgres helpers ────────────────────────────────────────────────
    def _pg_session(self):
        return get_session_factory()()

    def _pg_booked(self, s) -> dict[str, dict[str, Any]]:
        return {sid: dict(p) for sid, p in s.execute(
            select(db.PaperBooked.signal_id, db.PaperBooked.payload))}

    def _pg_write_state(self, s, ledger: dict[str, Any], old_version: int) -> bool:
        """Conditional UPDATE. False means another writer moved the version."""
        res = s.execute(
            update(db.PaperState)
            .where(db.PaperState.id == 1, db.PaperState.version == old_version)
            .values(starting_equity=ledger["starting_equity"],
                    equity=ledger["equity"],
                    peak_equity=ledger["peak_equity"],
                    version=old_version + 1)
        )
        return res.rowcount == 1

    # ── settlement ──────────────────────────────────────────────────────
    def settle(self, outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Book all newly-final outcomes. Idempotent. Returns run summary."""
        if self._pg:
            return self._settle_pg(outcomes)
        ledger = self.load()
        newly_booked, _rows = self._book(ledger, outcomes)
        self.save(ledger)
        summary = {
            "equity": ledger["equity"],
            "newly_booked": newly_booked,
            "total_booked": len(ledger.get("booked", {})),
        }
        if newly_booked:
            logger.info(f"[Paper] {summary}")
        return summary

    def _book(self, ledger: dict[str, Any],
              outcomes: dict[str, dict[str, Any]]) -> tuple[int, dict[str, dict[str, Any]]]:
        """Apply newly-final outcomes to `ledger` in place.

        Shared by both backends so the money math cannot diverge. Returns
        (newly_booked, {signal_id: record}) for the rows that were added.
        """
        booked = ledger.get("booked", {})
        newly_booked = 0
        rows: dict[str, dict[str, Any]] = {}
        for sid, oc in outcomes.items():
            if sid in booked or not oc.get("final"):
                continue
            risk = oc.get("risk_amount") or self.default_risk
            try:
                risk = float(risk)
            except (TypeError, ValueError):
                risk = self.default_risk
            net_r = oc.get("net_realized_r") or 0.0
            try:
                net_r = float(net_r)
            except (TypeError, ValueError):
                net_r = 0.0
            pnl = round(net_r * risk, 2)
            ledger["equity"] = round(ledger.get("equity", self.starting_equity) + pnl, 2)
            ledger["peak_equity"] = max(ledger.get("peak_equity", ledger["equity"]), ledger["equity"])
            _notional = oc.get("position_notional")
            try:
                _notional = float(_notional) if _notional is not None else None
            except (TypeError, ValueError):
                _notional = None
            rec = {
                "signal_id": sid,
                "symbol": oc.get("symbol"),
                "side": oc.get("side"),
                "strategy_id": oc.get("strategy_id"),
                "signal_timestamp": oc.get("signal_timestamp"),
                "settled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "entry_triggered": oc.get("entry_triggered"),
                "first_event": oc.get("first_event"),
                "status": oc.get("status"),
                "holding_minutes": oc.get("holding_minutes"),
                "holding_time": oc.get("holding_time"),
                "close_timestamp": oc.get("close_timestamp"),
                "liquidated": bool(oc.get("liquidated")),
                "liq_price": oc.get("liq_price"),
                "leverage": oc.get("leverage"),
                "position_notional": _notional,
                "position_pct_capital": round(_notional / ledger.get("starting_equity", self.starting_equity) * 100, 2) if _notional else None,
                "net_r": net_r,
                "risk_amount": risk,
                "pnl_usd": pnl,
                "equity_after": ledger["equity"],
            }
            booked[sid] = rec
            rows[sid] = rec
            newly_booked += 1
        ledger["booked"] = booked
        return newly_booked, rows

    def _settle_pg(self, outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Optimistic-locked settlement: read version, compute, conditional
        UPDATE; retry once if the version moved, then raise."""
        for attempt in (1, 2):
            try:
                with self._pg_session() as s, s.begin():
                    state = s.get(db.PaperState, 1)
                    if state is None:
                        s.add(db.PaperState(id=1, version=0,
                                            starting_equity=self.starting_equity,
                                            equity=self.starting_equity,
                                            peak_equity=self.starting_equity))
                        s.flush()
                        state = s.get(db.PaperState, 1)
                    old_version = int(state.version or 0)
                    ledger = {"starting_equity": state.starting_equity,
                              "equity": state.equity,
                              "peak_equity": state.peak_equity,
                              "booked": self._pg_booked(s)}
                    newly_booked, rows = self._book(ledger, outcomes)
                    if not self._pg_write_state(s, ledger, old_version):
                        raise _StaleState()
                    for sid, rec in rows.items():
                        s.add(db.PaperBooked(signal_id=sid,
                                             payload=rec,
                                             **_booked_columns(rec)))
            except _StaleState:
                logger.warning(f"[Paper] paper_state version conflict "
                               f"(attempt {attempt}/2)")
                continue
            except SQLAlchemyError as e:
                logger.error(f"[Paper] Failed to settle portfolio: {e}")
                raise
            summary = {
                "equity": ledger["equity"],
                "newly_booked": newly_booked,
                "total_booked": len(ledger.get("booked", {})),
            }
            if newly_booked:
                logger.info(f"[Paper] {summary}")
            return summary
        raise RuntimeError("paper_state version conflict after retry")

    def summary(self) -> dict[str, Any]:
        """Portfolio stats for the dashboard."""
        ledger = self.load()
        trades = list(ledger.get("booked", {}).values())
        entered = [t for t in trades if t.get("entry_triggered")]
        wins = [t for t in entered if (t.get("net_r") or 0) > 0]
        liquidations = [t for t in entered if t.get("liquidated")]
        equity = ledger.get("equity", self.starting_equity)
        peak = ledger.get("peak_equity", equity) or equity
        equity_curve, cum = [], 0.0
        start = ledger.get("starting_equity", self.starting_equity)
        running_peak, max_dd = start, 0.0
        for t in sorted(trades, key=lambda x: str(x.get("signal_timestamp") or "")):
            cum = round(cum + (t.get("pnl_usd") or 0), 2)
            eq = round(start + cum, 2)
            running_peak = max(running_peak, eq)
            max_dd = max(max_dd, round(running_peak - eq, 2))
            equity_curve.append({"t": t.get("signal_timestamp"), "cum_pnl": cum, "id": t.get("signal_id")})
        return {
            "starting_equity": start,
            "equity": equity,
            "total_pnl": round(equity - start, 2),
            "return_pct": round((equity / start - 1) * 100, 2) if start else 0,
            "peak_equity": peak,
            "max_drawdown_usd": max_dd,
            "trades_settled": len(trades),
            "trades_entered": len(entered),
            "wins": len(wins),
            "liquidations": len(liquidations),
            "win_rate": round(len(wins) / len(entered), 3) if entered else 0,
            "equity_curve": equity_curve[-60:],
            "recent_trades": sorted(trades, key=lambda x: str(x.get("settled_at") or ""), reverse=True)[:50],
        }


def _booked_columns(rec: dict[str, Any]) -> dict[str, Any]:
    """Promoted paper_booked columns for one settled record."""
    return {
        "symbol": rec.get("symbol"),
        "side": rec.get("side"),
        "strategy_id": rec.get("strategy_id"),
        "signal_timestamp": rec.get("signal_timestamp"),
        "settled_at": rec.get("settled_at"),
        "close_timestamp": rec.get("close_timestamp"),
        "status": rec.get("status"),
        "entry_triggered": rec.get("entry_triggered"),
        "liquidated": rec.get("liquidated"),
        "first_event": rec.get("first_event"),
        "leverage": rec.get("leverage"),
        "position_notional": rec.get("position_notional"),
        "position_pct_capital": rec.get("position_pct_capital"),
        "net_r": rec.get("net_r"),
        "risk_amount": rec.get("risk_amount"),
        "pnl_usd": rec.get("pnl_usd"),
        "equity_after": rec.get("equity_after"),
        "holding_minutes": rec.get("holding_minutes"),
        "liq_price": rec.get("liq_price"),
    }
