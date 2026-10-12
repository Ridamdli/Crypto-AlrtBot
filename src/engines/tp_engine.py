from typing import Dict
from src.utils.logger import get_logger

logger = get_logger(__name__)

class TakeProfitEngine:
    def __init__(self, r_multiple: float = 2.0):
        self.r_multiple = r_multiple

    def calculate_tp(self, entry_price: float, sl_price: float, side: str) -> Dict[str, float]:
        """
        Calculate the single TP based on risk (R).
        """
        if side == "LONG":
            risk = entry_price - sl_price
        else:
            risk = sl_price - entry_price

        if risk <= 0:
            logger.error("Invalid risk calculated. SL might be on wrong side of entry.")
            return {"tp": 0.0}

        if side == "LONG":
            return {"tp": entry_price + (risk * self.r_multiple)}
        return {"tp": entry_price - (risk * self.r_multiple)}
