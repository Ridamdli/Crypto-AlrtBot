"""
Live outcome tracker.

Watches every persisted signal against subsequent live 15m candles using the
same SignalOutcomeEvaluator as research (same fees/slippage assumptions), and
maintains a results ledger for future analysis:

    data_store/outcomes.json   {signal_id: outcome_record}

Signals are re-evaluated each cycle until final:
  final = status in (TP3_HIT, STOPPED_OUT)
       or EXPIRED with enough future bars to judge entry fairly
       or elapsed 15m bars >= max_entry_bars + max_holding_bars

Young signals with truncated future data are NEVER finalized.
"""
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.backtest.evaluator import SignalOutcomeEvaluator
from src.data.binance_client import BinanceFuturesClient
from src.data.ohlcv_fetcher import OHLCVFetcher
from src.utils.logger import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = ("TP3_HIT", "STOPPED_OUT", "LIQUIDATED")
BAR_MS = 15 * 60 * 1000


def format_duration(ms: int) -> str:
    """Human-readable duration, e.g. 7380000 -> '2h 3m'. Negative/clamped to 0."""
    total_min = max(0, int(ms // 60000))
    days, rem = divmod(total_min, 1440)
    hours, minutes = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def holding_info(entry_ts_ms, holding_bars: int, is_final: bool, now_ms: int):
    """Entry-to-close timer. Closed trades use bar count; open trades use live elapsed time."""
    if not entry_ts_ms or not holding_bars:
        return {"holding_minutes": None, "holding_time": None, "close_timestamp": None}
    if is_final:
        close_ms = int(entry_ts_ms) + int(holding_bars) * BAR_MS
        minutes = int(holding_bars) * 15
        return {
            "holding_minutes": minutes,
            "holding_time": format_duration(minutes * 60000),
            "close_timestamp": datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    elapsed_ms = max(0, now_ms - int(entry_ts_ms))
    return {
        "holding_minutes": round(elapsed_ms / 60000, 1),
        "holding_time": format_duration(elapsed_ms) + " (open)",
        "close_timestamp": None,
    }


class OutcomeTracker:
    def __init__(
        self,
        client: Optional[BinanceFuturesClient] = None,
        evaluator: Optional[SignalOutcomeEvaluator] = None,
        storage_dir: str = "data_store",
        ledger_name: str = "outcomes.json",
        future_limit: int = 200,
    ):
        self.client = client or BinanceFuturesClient()
        self.fetcher = OHLCVFetcher(self.client)
        self.evaluator = evaluator or SignalOutcomeEvaluator()
        self.storage_dir = storage_dir
        self.ledger_path = os.path.join(storage_dir, ledger_name)
        self.future_limit = future_limit
        os.makedirs(self.storage_dir, exist_ok=True)

    # ── ledger ──────────────────────────────────────────────────────────
    def load_ledger(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save_ledger(self, ledger: Dict[str, Dict[str, Any]]) -> None:
        try:
            with open(self.ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
        except OSError as e:
            logger.error(f"[Tracker] Failed to save outcomes ledger: {e}")

    def load_all_signals(self) -> List[Dict[str, Any]]:
        signals = []
        for path in sorted(glob.glob(os.path.join(self.storage_dir, "signals_*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            for s in data if isinstance(data, list) else []:
                if isinstance(s, dict) and s.get("signal_id"):
                    signals.append(s)
        return signals

    # ── evaluation ──────────────────────────────────────────────────────
    @staticmethod
    def _signal_ts_ms(signal: Dict[str, Any]) -> Optional[int]:
        ts = signal.get("timestamp")
        if isinstance(ts, (int, float)):
            return int(ts) if ts > 100_000_000_000 else int(ts * 1000)
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return int(dt.timestamp() * 1000)
            except ValueError:
                return None
        return None

    def _future_klines(self, symbol: str, signal_ts_ms: int) -> List[Dict[str, Any]]:
        klines = self.fetcher.fetch_standardized_klines(symbol, "15m", limit=self.future_limit)
        return [k for k in klines if k.get("timestamp", 0) > signal_ts_ms]

    def _is_final(self, result: Dict[str, Any], future_bars: int, elapsed_bars: int) -> bool:
        status = result.get("status")
        if status in TERMINAL_STATUSES:
            return True
        max_entry = self.evaluator.max_entry_bars
        max_hold = self.evaluator.max_holding_bars
        if status == "EXPIRED" and future_bars >= max_entry:
            return True
        if elapsed_bars >= max_entry + max_hold:
            return True
        return False

    def track_signal(self, signal: Dict[str, Any], ledger: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Evaluate one signal; returns updated record, or None if skipped."""
        sid = signal.get("signal_id")
        symbol = signal.get("symbol")
        if not sid or not symbol:
            return None
        existing = ledger.get(sid, {})
        if existing.get("final"):
            return existing

        signal_ts = self._signal_ts_ms(signal)
        if signal_ts is None:
            return None

        try:
            future = self._future_klines(symbol, signal_ts)
        except Exception as e:
            logger.warning(f"[Tracker] future klines failed for {sid}: {e}")
            return existing or None
        if not future:
            return existing or None

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        elapsed_bars = max(0, (now_ms - signal_ts) // BAR_MS)

        try:
            result = self.evaluator.evaluate_signal(signal, future)
        except Exception as e:
            logger.warning(f"[Tracker] evaluation failed for {sid}: {e}")
            return existing or None

        record = {
            "signal_id": sid,
            "symbol": symbol,
            "side": signal.get("side"),
            "strategy_id": signal.get("strategy_id"),
            "signal_timestamp": signal.get("timestamp"),
            "entry": signal.get("entry"),
            "stop_loss": signal.get("stop_loss"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "confidence": signal.get("confidence"),
            "btc_regime": signal.get("btc_regime"),
            "leverage": signal.get("leverage"),
            "margin": signal.get("margin"),
            "position_notional": signal.get("position_notional"),
            "risk_amount": signal.get("risk_amount"),
            "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "future_bars_seen": len(future),
            **{k: result.get(k) for k in (
                "entry_triggered", "expired", "first_event", "actual_entry_price",
                "tp1_hit", "tp2_hit", "tp3_hit", "sl_hit", "liquidated", "liq_price",
                "mfe_r", "mae_r", "holding_bars", "realized_r", "net_realized_r",
                "fees_cost_r", "status",
            )},
        }
        record["final"] = self._is_final(result, len(future), elapsed_bars)
        record.update(holding_info(
            result.get("entry_timestamp"),
            result.get("holding_bars") or 0,
            record["final"],
            now_ms,
        ))
        ledger[sid] = record
        return record

    def track_all(self) -> Dict[str, Any]:
        """Evaluate all open signals; returns a run summary."""
        ledger = self.load_ledger()
        signals = self.load_all_signals()
        evaluated, finalized, skipped = 0, 0, 0
        for sig in signals:
            sid = sig.get("signal_id", "?")
            if ledger.get(sid, {}).get("final"):
                skipped += 1
                continue
            rec = self.track_signal(sig, ledger)
            if rec is None:
                skipped += 1
            else:
                evaluated += 1
                if rec.get("final"):
                    finalized += 1
        self.save_ledger(ledger)
        summary = {
            "signals_total": len(signals),
            "evaluated_this_run": evaluated,
            "finalized_this_run": finalized,
            "skipped_final_or_nodata": skipped,
            "ledger_size": len(ledger),
        }
        logger.info(f"[Tracker] {summary}")
        return summary
