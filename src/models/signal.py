from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class SignalModel(BaseModel):
    signal_id: str
    timestamp: str
    symbol: str
    side: str
    entry: float
    tp1: float
    tp2: float
    tp3: float
    stop_loss: float
    leverage: int
    margin: float
    position_notional: float
    risk_amount: float
    risk_reward_tp1: float
    risk_reward_tp2: float
    risk_reward_tp3: float
    confidence: int
    conditions: List[str]
    invalidation_conditions: List[str]
    management_rules: List[str]
    
    # Allow extra fields for internal logic, but require the above
    class Config:
        extra = "allow"
