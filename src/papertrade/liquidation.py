"""
Isolated-margin liquidation estimator (Binance USDT-M style).

Approximation used across formatter display, outcome evaluation, and paper
accounting — identical math everywhere so the three never disagree.

Isolated LONG liquidates (ignoring extra margin) when:
    liq = entry * (1 - 1/leverage + mmr)
Isolated SHORT liquidates when:
    liq = entry * (1 + 1/leverage - mmr)

mmr = maintenance-margin rate (Binance tier-1 notional bracket: 0.4%).
"""
from typing import Optional

MMR_DEFAULT = 0.004


def estimate_liq_price(entry: float, side: str, leverage: float, mmr: float = MMR_DEFAULT) -> Optional[float]:
    """Estimated liquidation price, or None if inputs are unusable."""
    try:
        entry = float(entry)
        leverage = float(leverage)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or leverage <= 0:
        return None
    if side == "LONG":
        return entry * (1.0 - 1.0 / leverage + mmr)
    if side == "SHORT":
        return entry * (1.0 + 1.0 / leverage - mmr)
    return None


def liq_distance_pct(entry: float, liq_price: float, side: str) -> Optional[float]:
    """Adverse distance to liquidation, in percent of entry."""
    try:
        if side == "LONG":
            return (float(entry) - float(liq_price)) / float(entry) * 100.0
        if side == "SHORT":
            return (float(liq_price) - float(entry)) / float(entry) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None
