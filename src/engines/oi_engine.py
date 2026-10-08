from typing import List, Dict, Any, Literal
from src.utils.logger import get_logger

logger = get_logger(__name__)

OIStatus = Literal["CONFIRMED", "REJECTED", "UNKNOWN"]

class OIEngine:
    def __init__(
        self,
        min_oi_change_pct: float = 2.0,
        oi_mode: str = "optional",  # "required" | "optional"
    ):
        self.min_oi_change_pct = min_oi_change_pct
        self.oi_mode = oi_mode  # "required" or "optional"

    def analyze_oi(self, oi_hist: List[Dict[str, Any]], price_trend: str) -> Dict[str, Any]:
        """
        Analyze open interest history to detect percentage change and divergence.
        Returns tri-state status: CONFIRMED, REJECTED, UNKNOWN
        """
        # Check if we have sufficient OI data
        if len(oi_hist) < 2:
            return {
                "oi_change_pct": 0.0,
                "signal": "neutral",
                "divergence": False,
                "status": "UNKNOWN",
                "reason": "insufficient_oi_data",
            }

        current_oi = float(oi_hist[-1].get("sumOpenInterestValue", 0))
        previous_oi = float(oi_hist[0].get("sumOpenInterestValue", 0))

        if previous_oi == 0:
            change_pct = 0.0
        else:
            change_pct = ((current_oi - previous_oi) / previous_oi) * 100.0

        signal = "neutral"
        divergence = False
        status: OIStatus = "UNKNOWN"
        reason = "neutral"

        if change_pct >= self.min_oi_change_pct:
            if price_trend == "up":
                signal = "bullish"
                status = "CONFIRMED"
                reason = "oi_increasing_with_price"
            elif price_trend == "down":
                signal = "bearish"
                status = "CONFIRMED"
                reason = "oi_increasing_against_price"
        elif change_pct <= -self.min_oi_change_pct:
            # OI decreasing
            if price_trend == "up":
                divergence = True
                signal = "bearish_divergence"
                status = "REJECTED"
                reason = "oi_decreasing_with_price_up"
            elif price_trend == "down":
                divergence = True
                signal = "bullish_divergence"
                status = "REJECTED"
                reason = "oi_decreasing_with_price_down"
        else:
            # Change is below threshold
            status = "UNKNOWN"
            reason = "oi_change_below_threshold"

        return {
            "oi_change_pct": change_pct,
            "signal": signal,
            "divergence": divergence,
            "status": status,
            "reason": reason,
        }

    def should_reject_candidate(self, oi_result: Dict[str, Any]) -> bool:
        """
        Determine if candidate should be rejected based on OI mode.
        """
        if self.oi_mode == "required":
            return oi_result.get("status") != "CONFIRMED"
        return False  # optional mode: never reject, just no bonus
