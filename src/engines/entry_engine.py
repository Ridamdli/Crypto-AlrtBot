from typing import Dict, Any, List
from src.utils.logger import get_logger

logger = get_logger(__name__)

class EntryEngine:
    def __init__(self, pullback_pct: float = 0.5):
        self.pullback_pct = pullback_pct / 100.0 # e.g., 0.5%

    def calculate_entry(self, model: str, current_price: float, recent_klines: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
        """
        Calculate entry price and activation conditions based on model.
        models: "market", "pullback"
        """
        if model == "market":
            return {
                "entry_price": current_price,
                "entry_type": "market",
                "activation_condition": "immediate",
            }
        
        elif model == "pullback":
            # Simple pullback: try to get an entry a certain % better than current price
            if side == "LONG":
                entry_price = current_price * (1 - self.pullback_pct)
                activation = f"Price drops to {entry_price:.4f}"
            else:
                entry_price = current_price * (1 + self.pullback_pct)
                activation = f"Price rises to {entry_price:.4f}"
                
            return {
                "entry_price": entry_price,
                "entry_type": "limit",
                "activation_condition": activation,
            }
            
        else:
            logger.error(f"Unknown entry model: {model}")
            return {"entry_price": current_price, "entry_type": "market", "activation_condition": "unknown"}
