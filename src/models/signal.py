from pydantic import BaseModel, model_validator
from pydantic import ConfigDict
from typing import Any, List, Optional

class SignalModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Identity
    signal_id: str
    timestamp: str
    symbol: str
    side: str
    strategy_id: str = "momentum_volume_v1"
    strategy_version: str = "1.0.0"
    configuration_hash: Optional[str] = None

    # Trade parameters
    entry: float
    tp: float
    stop_loss: float

    # Risk / sizing
    leverage: int
    margin: float
    position_notional: float
    risk_amount: float
    risk_reward: float

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

    @model_validator(mode="before")
    @classmethod
    def _legacy_ladder(cls, data: Any) -> Any:
        """Backward compatibility: old persisted rows carry tp1/tp2/tp3 and
        risk_reward_tp1 — map the TP1 leg onto the single TP."""
        if isinstance(data, dict):
            data = dict(data)
            if data.get("tp") is None and data.get("tp1") is not None:
                data["tp"] = data["tp1"]
            if data.get("risk_reward") is None and data.get("risk_reward_tp1") is not None:
                data["risk_reward"] = data["risk_reward_tp1"]
        return data

    @model_validator(mode="after")
    def validate_geometry(self):
        """Ensure SL and TP are on the correct side of entry."""
        if self.side == "LONG":
            assert self.stop_loss < self.entry, "LONG SL must be below entry"
            assert self.tp > self.entry, "LONG TP must be above entry"
        else:
            assert self.stop_loss > self.entry, "SHORT SL must be above entry"
            assert self.tp < self.entry, "SHORT TP must be below entry"
        assert self.risk_reward >= 0.5, f"R:R too low: {self.risk_reward}"
        assert 0 <= self.confidence <= 100, "Confidence must be 0-100"
        return self
