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

State: data_store/paper_portfolio.json
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_STARTING_EQUITY = 1000.0
DEFAULT_RISK_PER_TRADE = 10.0


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
        os.makedirs(self.storage_dir, exist_ok=True)

    # ── ledger ──────────────────────────────────────────────────────────
    def load(self) -> Dict[str, Any]:
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

    def save(self, ledger: Dict[str, Any]) -> None:
        try:
            with open(self.ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
        except OSError as e:
            logger.error(f"[Paper] Failed to save portfolio ledger: {e}")

    # ── settlement ──────────────────────────────────────────────────────
    def settle(self, outcomes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Book all newly-final outcomes. Idempotent. Returns run summary."""
        ledger = self.load()
        booked = ledger.get("booked", {})
        newly_booked = 0
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
            booked[sid] = {
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
            newly_booked += 1
        ledger["booked"] = booked
        self.save(ledger)
        summary = {
            "equity": ledger["equity"],
            "newly_booked": newly_booked,
            "total_booked": len(booked),
        }
        if newly_booked:
            logger.info(f"[Paper] {summary}")
        return summary

    def summary(self) -> Dict[str, Any]:
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
