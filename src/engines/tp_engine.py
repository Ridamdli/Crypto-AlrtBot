from typing import Dict
from src.utils.logger import get_logger

logger = get_logger(__name__)

class TakeProfitEngine:
    def __init__(self, r_multiples: tuple = (1.0, 2.0, 3.0)):
        self.r_multiples = r_multiples

    def calculate_tp(self, entry_price: float, sl_price: float, side: str) -> Dict[str, float]:
        """
        Calculate TP1, TP2, TP3 based on risk (R).
        """
        if side == "LONG":
            risk = entry_price - sl_price
        else:
            risk = sl_price - entry_price
            
        if risk <= 0:
            logger.error("Invalid risk calculated. SL might be on wrong side of entry.")
            return {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0}

        tps = {}
        for i, mult in enumerate(self.r_multiples):
            if side == "LONG":
                tps[f"tp{i+1}"] = entry_price + (risk * mult)
            else:
                tps[f"tp{i+1}"] = entry_price - (risk * mult)

        return tps
