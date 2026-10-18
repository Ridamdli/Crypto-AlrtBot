from src.utils.logger import get_logger

logger = get_logger(__name__)

class InvalidationEngine:
    def generate_rules(self, side: str, entry: float, sl: float, timeframe: str = "15m") -> dict[str, list[str]]:
        """
        Generates explicit validation, invalidation, and management rules
        per PRD §25 and §27.
        """

        validation_conditions = [
            f"Price reaches entry zone ({entry:.6g})",
        ]

        if side == "LONG":
            invalidation_conditions = [
                f"{timeframe} candle closes below {sl:.6g} (Stop Loss)",
                "BTC loses 4H bullish structure",
                "Entry not triggered within 6 candles",
            ]
        else:
            invalidation_conditions = [
                f"{timeframe} candle closes above {sl:.6g} (Stop Loss)",
                "BTC loses 4H bearish structure",
                "Entry not triggered within 6 candles",
            ]

        management_rules = [
            "TP → close 100% of position",
        ]

        return {
            "conditions": validation_conditions,
            "invalidation_conditions": invalidation_conditions,
            "management_rules": management_rules,
        }
