import os
import json
from typing import Dict, Any, List, Optional
from enum import Enum

from src.models.signal import SignalModel
from src.utils.logger import get_logger

logger = get_logger(__name__)

class AmbiguousResolution(str, Enum):
    """
    Resolution policy for ambiguous candles where both TP and SL are touched
    within the same bar's [Low, High] range.
    """
    CONSERVATIVE = "conservative"  # Worst-case: SL hit first (PRD Risk-First principle)
    OPTIMISTIC = "optimistic"      # Best-case: TP hit first
    OPEN_PROXIMITY = "open_proximity"  # Target closer to candle Open was touched first

class SignalOutcomeEvaluator:
    """
    Historical Signal Outcome Evaluator per PRD §31.
    Evaluates what happened to a signal after publication using subsequent candles.
    """

    def __init__(
        self,
        max_entry_bars: int = 6,
        max_holding_bars: int = 100,
        ambiguous_resolution: AmbiguousResolution = AmbiguousResolution.CONSERVATIVE,
        taker_fee_pct: float = 0.0004,     # 0.04% Binance futures taker fee
        slippage_pct: float = 0.0002,      # 0.02% estimated slippage
        storage_dir: str = "data_store/evaluations",
    ):
        self.max_entry_bars = max_entry_bars
        self.max_holding_bars = max_holding_bars
        self.ambiguous_resolution = ambiguous_resolution
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct
        self.storage_dir = storage_dir
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir, exist_ok=True)

    def evaluate_signal(
        self,
        signal: Dict[str, Any],
        subsequent_klines: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Evaluates a signal across subsequent OHLCV candles (typically 15m).
        """
        from src.papertrade.liquidation import estimate_liq_price

        side = signal["side"]
        entry_target = float(signal["entry"])
        sl = float(signal["stop_loss"])
        tp1 = float(signal["tp1"])
        tp2 = float(signal["tp2"])
        tp3 = float(signal["tp3"])
        risk_distance = abs(entry_target - sl)

        # ── Optional liquidation modeling (isolated margin) ───────────────
        # Only active when the signal carries leverage + margin info (live
        # pipeline signals do; research-only dicts don't → behavior unchanged).
        liq_price = None
        liq_loss_r = None
        try:
            _lev = signal.get("leverage")
            _margin = signal.get("margin")
            _notional = signal.get("position_notional")
            if _lev is not None and (_margin is not None or _notional is not None):
                liq_price = estimate_liq_price(entry_target, side, float(_lev))
                _notional = float(_notional) if _notional is not None else float(_margin) * float(_lev)
                _qty = _notional / entry_target if entry_target else 0.0
                _risk_usd = risk_distance * _qty
                if liq_price is not None and _risk_usd > 0:
                    liq_loss_r = round(-float(_margin if _margin is not None else _notional / float(_lev)) / _risk_usd, 3)
        except (TypeError, ValueError, ZeroDivisionError):
            liq_price, liq_loss_r = None, None

        # ── Step 1: Check Entry Activation ──────────────────────────────────
        entry_triggered = False
        actual_entry_price = None
        entry_timestamp = None
        entry_bar_idx = None

        check_limit = min(len(subsequent_klines), self.max_entry_bars)
        for i in range(check_limit):
            k = subsequent_klines[i]
            k_low = float(k["low"])
            k_high = float(k["high"])

            if side == "LONG":
                # Entry is a pullback/limit order: triggered if price reaches or dips below entry
                if k_low <= entry_target:
                    entry_triggered = True
                    actual_entry_price = entry_target
                    entry_timestamp = k.get("timestamp")
                    entry_bar_idx = i
                    break
            else:  # SHORT
                if k_high >= entry_target:
                    entry_triggered = True
                    actual_entry_price = entry_target
                    entry_timestamp = k.get("timestamp")
                    entry_bar_idx = i
                    break

        if not entry_triggered:
            return {
                "signal_id": signal.get("signal_id"),
                "symbol": signal.get("symbol"),
                "side": side,
                "entry_triggered": False,
                "expired": True,
                "first_event": "EXPIRED",
                "actual_entry_price": None,
                "entry_timestamp": None,
                "candles_to_entry": check_limit,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "sl_hit": False,
                "liquidated": False,
                "liq_price": liq_price,
                "mfe": 0.0,
                "mae": 0.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "holding_bars": 0,
                "realized_r": 0.0,
                "net_realized_r": 0.0,
                "fees_cost_r": 0.0,
                "status": "EXPIRED",
            }

        # ── Step 2: Track Active Trade Progression ──────────────────────────
        active_klines = subsequent_klines[entry_bar_idx:]
        active_klines = active_klines[: self.max_holding_bars]

        tp1_hit = False
        tp2_hit = False
        tp3_hit = False
        sl_hit = False
        liquidated = False
        first_event = None

        mfe = 0.0
        mae = 0.0
        active_sl = sl  # Can move to break-even after TP1
        holding_bars = 0

        # Position tranche tracking: 50% TP1, 25% TP2, 25% TP3
        remaining_position = 1.0
        realized_r = 0.0

        for bar_idx, k in enumerate(active_klines):
            holding_bars = bar_idx + 1
            k_open = float(k["open"])
            k_high = float(k["high"])
            k_low = float(k["low"])

            # Excursion calculations
            if side == "LONG":
                bar_favorable = max(0.0, k_high - actual_entry_price)
                bar_adverse = max(0.0, actual_entry_price - k_low)
            else:
                bar_favorable = max(0.0, actual_entry_price - k_low)
                bar_adverse = max(0.0, k_high - actual_entry_price)

            if bar_favorable > mfe:
                mfe = bar_favorable
            if bar_adverse > mae:
                mae = bar_adverse

            # Check liquidation FIRST (liq engine closes before TP/SL prints)
            if liq_price is not None and liq_loss_r is not None:
                liq_touched = (k_low <= liq_price) if side == "LONG" else (k_high >= liq_price)
            else:
                liq_touched = False

            if liq_touched:
                liquidated = True
                if first_event is None:
                    first_event = "LIQ"
                # Isolated margin wiped: lose the full posted margin
                realized_r = liq_loss_r
                remaining_position = 0.0
                break

            # Check TP / SL hits on this bar
            next_target = tp3 if tp2_hit else (tp2 if tp1_hit else tp1)

            if side == "LONG":
                target_touched = k_high >= next_target
                sl_touched = k_low <= active_sl
            else:
                target_touched = k_low <= next_target
                sl_touched = k_high >= active_sl

            # Handle Ambiguous Candle
            if target_touched and sl_touched:
                target_first = self._resolve_ambiguous(
                    k_open, next_target, active_sl, side
                )
            else:
                target_first = target_touched

            # Process Events
            if target_first and target_touched:
                if not tp1_hit:
                    tp1_hit = True
                    if first_event is None:
                        first_event = "TP1"
                    # Realize 50% of position at TP1 R:R
                    rr_tp1 = abs(tp1 - actual_entry_price) / risk_distance
                    realized_r += 0.50 * rr_tp1
                    remaining_position -= 0.50
                    # Position management rule: Move SL to break-even (entry)
                    active_sl = actual_entry_price
                elif not tp2_hit:
                    tp2_hit = True
                    if first_event is None:
                        first_event = "TP2"
                    rr_tp2 = abs(tp2 - actual_entry_price) / risk_distance
                    realized_r += 0.25 * rr_tp2
                    remaining_position -= 0.25
                    # Trailing stop rule: Move SL to TP1
                    active_sl = tp1
                elif not tp3_hit:
                    tp3_hit = True
                    if first_event is None:
                        first_event = "TP3"
                    rr_tp3 = abs(tp3 - actual_entry_price) / risk_distance
                    realized_r += 0.25 * rr_tp3
                    remaining_position -= 0.25
                    break  # Complete exit

            elif sl_touched:
                sl_hit = True
                if first_event is None:
                    first_event = "SL"

                if remaining_position == 1.0:
                    # Stopped out full position
                    realized_r = -1.0
                else:
                    # Stopped out at trailing stop / break-even on remaining position
                    if active_sl == actual_entry_price:
                        # Break-even stop
                        realized_r += 0.0
                    else:
                        trailing_rr = (
                            (active_sl - actual_entry_price) / risk_distance
                            if side == "LONG"
                            else (actual_entry_price - active_sl) / risk_distance
                        )
                        realized_r += remaining_position * trailing_rr
                remaining_position = 0.0
                break

        # Calculate MFE and MAE in R multiples
        mfe_r = round(mfe / risk_distance, 2) if risk_distance > 0 else 0.0
        mae_r = round(mae / risk_distance, 2) if risk_distance > 0 else 0.0

        # Fees & Slippage deduction
        # 2 round-trip trades (entry + exits)
        total_costs_pct = (2 * self.taker_fee_pct) + (2 * self.slippage_pct)
        # Convert cost to R-multiple terms: cost_in_price / risk_distance
        cost_in_price = actual_entry_price * total_costs_pct
        fees_cost_r = round(cost_in_price / risk_distance, 3) if risk_distance > 0 else 0.0
        net_realized_r = round(realized_r - fees_cost_r, 3)

        status = "STOPPED_OUT" if sl_hit and remaining_position == 0 and not tp1_hit else (
            "TP3_HIT" if tp3_hit else (
                "TP2_HIT" if tp2_hit else (
                    "TP1_HIT" if tp1_hit else "ACTIVE"
                )
            )
        )
        if liquidated:
            status = "LIQUIDATED"

        result = {
            "signal_id": signal.get("signal_id"),
            "symbol": signal.get("symbol"),
            "side": side,
            "entry_triggered": True,
            "expired": False,
            "first_event": first_event or "TIMEOUT",
            "actual_entry_price": actual_entry_price,
            "entry_timestamp": entry_timestamp,
            "candles_to_entry": entry_bar_idx,
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
            "tp3_hit": tp3_hit,
            "sl_hit": sl_hit,
            "liquidated": liquidated,
            "liq_price": liq_price,
            "mfe": round(mfe, 6),
            "mae": round(mae, 6),
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "holding_bars": holding_bars,
            "realized_r": round(realized_r, 3),
            "net_realized_r": net_realized_r,
            "fees_cost_r": fees_cost_r,
            "status": status,
        }
        return result

    def _resolve_ambiguous(
        self,
        candle_open: float,
        target_price: float,
        sl_price: float,
        side: str,
    ) -> bool:
        """
        Determines whether target or SL was touched first on an ambiguous candle.
        Returns True if target hit first, False if SL hit first.
        """
        if self.ambiguous_resolution == AmbiguousResolution.CONSERVATIVE:
            return False  # SL hit first (risk-first)
        elif self.ambiguous_resolution == AmbiguousResolution.OPTIMISTIC:
            return True   # Target hit first
        elif self.ambiguous_resolution == AmbiguousResolution.OPEN_PROXIMITY:
            dist_to_target = abs(candle_open - target_price)
            dist_to_sl = abs(candle_open - sl_price)
            return dist_to_target < dist_to_sl
        return False

    def evaluate_batch(
        self,
        signals: List[Dict[str, Any]],
        market_klines: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Evaluates a batch of signals against their respective subsequent market klines."""
        evaluations = []
        for sig in signals:
            symbol = sig.get("symbol")
            klines = market_klines.get(symbol, [])
            ev = self.evaluate_signal(sig, klines)
            evaluations.append(ev)
        return evaluations

    def save_evaluations(
        self, evaluations: List[Dict[str, Any]], filename: str = "evaluations.json"
    ) -> str:
        """Persists evaluated outcomes to storage directory."""
        filepath = os.path.join(self.storage_dir, filename)
        with open(filepath, "w") as f:
            json.dump(evaluations, f, indent=2)
        logger.info(f"[SignalOutcomeEvaluator] Saved {len(evaluations)} outcomes to {filepath}")
        return filepath
