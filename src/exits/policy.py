"""ExitPolicyEngine — deterministic exit-management primitives.

Every function here is pure (same inputs -> same outputs), works only on
COMPLETED candles, and never requires an LLM. Workers compose these
primitives; they add no hidden thresholds of their own.

Candle convention (matches src/data/ohlcv_fetcher.py):
  {"timestamp": <bar open ms>, "close_time": <bar close ms>,
   "open/high/low/close/volume": floats}
A candle is COMPLETED when close_time <= now_ms. The forming candle is
always excluded from decisions.
"""
from typing import Any

from src.exits.config import ExitConfig


def split_completed(
    klines: list[dict[str, Any]], now_ms: int, timeframe_ms: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split klines into (completed, not-yet-completed).

    Falls back to timestamp+timeframe when close_time is absent, so the
    function also works on minimal synthetic bars used in tests.
    Out-of-order rows are sorted by timestamp first (never trust feed order).
    """
    ordered = sorted(klines, key=lambda k: float(k.get("timestamp", 0)))
    done, forming = [], []
    for k in ordered:
        try:
            close_time = float(k.get("close_time", float(k.get("timestamp", 0)) + timeframe_ms))
        except (TypeError, ValueError):
            continue
        (done if close_time <= now_ms else forming).append(k)
    return done, forming


def last_close_ts(completed: list[dict[str, Any]], timeframe_ms: int) -> int:
    """Close timestamp of the latest completed candle, 0 when empty."""
    if not completed:
        return 0
    k = completed[-1]
    try:
        return int(float(k.get("close_time", float(k.get("timestamp", 0)) + timeframe_ms)))
    except (TypeError, ValueError):
        return 0


def check_freshness(
    completed: list[dict[str, Any]],
    now_ms: int,
    cfg: ExitConfig,
) -> tuple[bool, str, int]:
    """Returns (fresh, reason, data_ts). Stale or thin data fails loudly —
    callers must abstain, never silently proceed."""
    data_ts = last_close_ts(completed, cfg.timeframe_ms)
    if not completed:
        return False, "no completed candles", 0
    if len(completed) < cfg.min_bars:
        return False, f"thin history ({len(completed)} < {cfg.min_bars} bars)", data_ts
    age_s = (now_ms - data_ts) / 1000.0
    if age_s < 0:
        return False, "data from the future (clock skew or misordered feed)", data_ts
    if age_s > cfg.max_data_age_s:
        return False, f"stale data (last close {age_s:.0f}s ago > {cfg.max_data_age_s}s)", data_ts
    return True, "ok", data_ts


def _f(k: dict[str, Any], field: str) -> float:
    return float(k[field])


def atr_wilder(completed: list[dict[str, Any]], period: int = 14) -> float | None:
    """Wilder ATR over completed candles. None when history too short."""
    if len(completed) < period + 1:
        return None
    trs = []
    prev_close = _f(completed[0], "close")
    for k in completed[1:]:
        high, low, close = _f(k, "high"), _f(k, "low"), _f(k, "close")
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def swing_levels(
    completed: list[dict[str, Any]], lookback: int
) -> tuple[float | None, float | None]:
    """(highest high, lowest low) over the last `lookback` completed bars."""
    window = completed[-lookback:] if len(completed) >= lookback else completed
    if not window:
        return None, None
    try:
        return max(_f(k, "high") for k in window), min(_f(k, "low") for k in window)
    except (TypeError, ValueError, KeyError):
        return None, None


def closes_against(completed: list[dict[str, Any]], side: str, n: int) -> int:
    """Count of the last n completed closes that moved against the side."""
    window = completed[-n:] if len(completed) >= n else completed
    count = 0
    prev = None
    for k in window:
        try:
            c = _f(k, "close")
        except (TypeError, ValueError, KeyError):
            prev = None
            continue
        if prev is not None:
            if side.upper() == "LONG" and c < prev or side.upper() == "SHORT" and c > prev:
                count += 1
        prev = c
    return count


def opposite_break(
    completed: list[dict[str, Any]], side: str, lookback: int = 20
) -> tuple[bool, dict[str, Any]]:
    """Completed-close break against the trade direction (Donchian-style).

    Uses prior-period extremes (excludes the evaluated candle itself) so the
    signal cannot peek at its own bar — same no-look-ahead convention as
    DonchianBreakoutStrategy._calculate_donchian_channels.
    """
    if len(completed) < lookback + 1:
        return False, {"reason": "insufficient history for opposite-break check"}
    ref = completed[:-1]
    window = ref[-lookback:]
    try:
        last_close = _f(completed[-1], "close")
        if side.upper() == "LONG":
            level = min(_f(k, "low") for k in window)
            broken = last_close < level
        else:
            level = max(_f(k, "high") for k in window)
            broken = last_close > level
    except (TypeError, ValueError, KeyError):
        return False, {"reason": "non-numeric candle fields"}
    return broken, {"level": level, "last_close": last_close, "lookback": lookback}


def stop_breached(last_close: float, side: str, stop_loss: float) -> bool:
    """A completed close beyond the stop = structural invalidation."""
    if side.upper() == "LONG":
        return last_close < stop_loss
    return last_close > stop_loss


def fee_cost_r(entry: float, risk_distance: float, cfg: ExitConfig) -> float:
    """Cost drag in R multiples, mirroring SignalOutcomeEvaluator exactly:
    two round-trip legs of (taker + slippage) on entry price / risk distance."""
    if risk_distance <= 0:
        return float("inf")
    total_pct = cfg.fee_legs * (cfg.taker_fee_pct + cfg.slippage_pct)
    return round(entry * total_pct / risk_distance, 3)


def gross_rr(entry: float, sl: float, tp: float) -> float:
    """Reward-to-risk of one target. Signed: negative means adverse."""
    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        return 0.0
    # Direction-aware: reward is movement from entry toward tp along the trade.
    return abs(tp - entry) / risk_distance


def remaining_net_rr(
    ref_price: float, side: str, entry: float, sl: float, tp: float, cfg: ExitConfig
) -> float:
    """Net R still available from ref_price to tp, minus full round-trip costs.

    Costs are charged in full (conservative: the exit still has to pay them).
    Returns negative values when the target is already behind ref_price.
    """
    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        return 0.0
    if side.upper() == "LONG":
        remaining = tp - ref_price
    else:
        remaining = ref_price - tp
    gross = remaining / risk_distance
    return round(gross - fee_cost_r(entry, risk_distance, cfg), 3)


def tp_distances_atr(
    ref_price: float, side: str, tps: list[float], atr_value: float | None
) -> list[float | None]:
    """Remaining distance to each TP in ATR units (direction-aware sign)."""
    out = []
    for tp in tps:
        if atr_value is None or atr_value <= 0:
            out.append(None)
            continue
        raw = (tp - ref_price) if side.upper() == "LONG" else (ref_price - tp)
        out.append(round(raw / atr_value, 3))
    return out


def near_level(ref_price: float, level: float, atr_value: float | None, band_atr: float) -> bool:
    """Is ref_price within band_atr ATRs of level? Needs a valid ATR."""
    if atr_value is None or atr_value <= 0:
        return False
    return abs(ref_price - level) <= band_atr * atr_value
