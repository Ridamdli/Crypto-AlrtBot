from pydantic import BaseModel, model_validator
from pydantic import ConfigDict
from typing import List, Optional

class SignalModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Identity
    signal_id: str
    timestamp: str
    symbol: str
    side: str
    strategy_id: str = "momentum_volume_v1"
    strategy_version: str = "1.0.0"

    # Trade parameters
    entry: float
    tp1: float
    tp2: float
    tp3: float
    stop_loss: float

    # Risk / sizing
    leverage: int
    margin: float
    position_notional: float
    risk_amount: float
    risk_reward_tp1: float
    risk_reward_tp2: float
    risk_reward_tp3: float

    # Scoring
    confidence: int

    # Human-readable output
    conditions: List[str]
    invalidation_conditions: List[str]
    management_rules: List[str]

    # Optional enrichment
    btc_regime: Optional[str] = None
    volume_ratio: Optional[float] = None
    oi_change_pct: Optional[float] = None
    funding_rate: Optional[float] = None

    @model_validator(mode="after")
    def validate_geometry(self):
        """Ensure SL and TPs are on the correct side of entry."""
        if self.side == "LONG":
            assert self.stop_loss < self.entry, "LONG SL must be below entry"
            assert self.tp1 > self.entry, "LONG TP1 must be above entry"
        else:
            assert self.stop_loss > self.entry, "SHORT SL must be above entry"
            assert self.tp1 < self.entry, "SHORT TP1 must be below entry"
        assert self.risk_reward_tp1 >= 0.5, f"R:R too low: {self.risk_reward_tp1}"
        assert 0 <= self.confidence <= 100, "Confidence must be 0-100"
        return self
