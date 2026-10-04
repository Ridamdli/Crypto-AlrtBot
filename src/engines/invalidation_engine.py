from typing import List, Dict, Any
from src.utils.logger import get_logger
from src.config import CONFIG

logger = get_logger(__name__)

class InvalidationEngine:
    def generate_rules(self, side: str, entry: float, sl: float, timeframe: str = "15m") -> Dict[str, List[str]]:
        """
        Generates explicit validation, invalidation, and management rules
        per PRD §25 and §27.
        """
        cfg = CONFIG.management

        validation_conditions = [
            f"Price reaches entry zone ({entry:.6g})",
        ]

        if side == "LONG":
            invalidation_conditions = [
                f"{timeframe} candle closes below {sl:.6g} (Stop Loss)",
                "BTC loses 4H bullish structure",
                f"Entry not triggered within 6 candles",
            ]
        else:
            invalidation_conditions = [
                f"{timeframe} candle closes above {sl:.6g} (Stop Loss)",
                "BTC loses 4H bearish structure",
                f"Entry not triggered within 6 candles",
            ]

        management_rules = [
            f"TP1 → close {cfg.tp1_close_pct}% of position",
            f"TP2 → close {cfg.tp2_close_pct}% of position",
            f"TP3 → close {cfg.tp3_close_pct}% of position",
            "After TP1: move SL to entry (break-even)",
            "After TP2: trailing stop activation",
        ]

        return {
            "conditions": validation_conditions,
            "invalidation_conditions": invalidation_conditions,
            "management_rules": management_rules,
        }
