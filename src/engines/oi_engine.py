from typing import List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)

class OIEngine:
    def __init__(self, min_oi_change_pct: float = 2.0):
        self.min_oi_change_pct = min_oi_change_pct

    def analyze_oi(self, oi_hist: List[Dict[str, Any]], price_trend: str) -> Dict[str, Any]:
        """
        Analyze open interest history to detect percentage change and divergence.
        oi_hist: output from /futures/data/openInterestHist
        price_trend: "up" or "down"
        """
        if len(oi_hist) < 2:
            return {"oi_change_pct": 0.0, "signal": "neutral", "divergence": False}

        current_oi = float(oi_hist[-1].get("sumOpenInterestValue", 0))
        previous_oi = float(oi_hist[0].get("sumOpenInterestValue", 0)) # Compare with start of period

        if previous_oi == 0:
            change_pct = 0.0
        else:
            change_pct = ((current_oi - previous_oi) / previous_oi) * 100.0

        signal = "neutral"
        divergence = False

        if change_pct >= self.min_oi_change_pct:
            if price_trend == "up":
                signal = "bullish" # Price up + OI up = Long build up
            elif price_trend == "down":
                signal = "bearish" # Price down + OI up = Short build up
        elif change_pct <= -self.min_oi_change_pct:
            # OI decreasing
            if price_trend == "up":
                divergence = True # Price up + OI down = Short covering (weak bullish or reversal warning)
            elif price_trend == "down":
                divergence = True # Price down + OI down = Long liquidation (weak bearish or reversal warning)

        return {
            "oi_change_pct": change_pct,
            "signal": signal,
            "divergence": divergence
        }
