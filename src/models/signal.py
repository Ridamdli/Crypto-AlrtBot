from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class SignalModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Identity
    signal_id: str
    timestamp: str
    symbol: str
    side: str
    strategy_id: str = "momentum_volume_v1"
    strategy_version: str = "1.0.0"
    configuration_hash: str | None = None

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
    # W-DD Option A: per-component points and the inputs the scorer saw, so a
    # published score is explainable from the record alone. None means the
    # signal predates the contract — audits must reconstruct and label that
    # Estimate, never read it as zero. Audits join outcomes by signal_id:
    # pariston_outcomes.json is an evidence export, not written by this tree.
    scoring_breakdown: dict[str, Any] | None = None

    # Human-readable output
    conditions: list[str]
    invalidation_conditions: list[str]
    management_rules: list[str]

    # Optional enrichment
    btc_regime: str | None = None
    volume_ratio: float | None = None
    oi_change_pct: float | None = None
    funding_rate: float | None = None

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
