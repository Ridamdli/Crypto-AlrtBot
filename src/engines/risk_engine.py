from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)

class RiskEngine:
    def __init__(self, account_size: float = 1000.0, max_risk_pct: float = 1.0, max_leverage: int = 20):
        self.account_size = account_size
        self.max_risk_pct = max_risk_pct / 100.0
        self.max_leverage = max_leverage

    def calculate_risk_parameters(self, entry: float, sl: float, tp_dict: Dict[str, float]) -> Dict[str, Any]:
        """
        Calculate R:R, position size, leverage, and margin.
        """
        risk_per_trade = self.account_size * self.max_risk_pct
        risk_distance = abs(entry - sl)
        
        if risk_distance == 0:
            return {"error": "Invalid risk distance"}

        # Position size in contracts/coins
        position_size = risk_per_trade / risk_distance
        
        # Position notional value in USDT
        position_notional = position_size * entry
        
        # Calculate Required Leverage based on account constraints
        # We assume we want to use at most 10% of our account as margin for one trade
        target_margin = self.account_size * 0.10
        required_leverage = position_notional / target_margin
        
        # Round up leverage to nearest whole number, cap at max_leverage
        leverage = min(int(required_leverage) + 1, self.max_leverage)
        
        # Recalculate actual margin used based on chosen leverage
        actual_margin = position_notional / leverage

        # Risk to Reward ratios
        rr_tps = {}
        for tp_name, tp_val in tp_dict.items():
            reward = abs(tp_val - entry)
            rr = reward / risk_distance
            rr_tps[f"rr_{tp_name}"] = round(rr, 2)

        return {
            "risk_amount": round(risk_per_trade, 2),
            "position_notional": round(position_notional, 2),
            "position_size": round(position_size, 4),
            "leverage": leverage,
            "margin": round(actual_margin, 2),
            "risk_reward_ratios": rr_tps
        }
