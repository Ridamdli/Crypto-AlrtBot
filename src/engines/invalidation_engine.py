from typing import Dict, Any, List
from src.utils.logger import get_logger

logger = get_logger(__name__)

class InvalidationEngine:
    def __init__(self):
        pass

    def generate_rules(self, side: str, entry: float, sl: float, timeframe: str = "15m") -> Dict[str, List[str]]:
        """
        Generates explicit validation, invalidation, and management rules.
        """
        validation_conditions = [
            f"Price must reach entry zone ({entry:.4f}) before hitting any TP targets."
        ]
        
        invalidation_conditions = [
            f"A {timeframe} candle closes beyond Stop Loss ({sl:.4f}).",
            "BTC regime shifts abruptly against trade direction."
        ]
        
        management_rules = [
            "Move Stop Loss to break-even after TP1 is hit.",
            "Trailing stop activation after TP2."
        ]
        
        return {
            "conditions": validation_conditions,
            "invalidation_conditions": invalidation_conditions,
            "management_rules": management_rules
        }
