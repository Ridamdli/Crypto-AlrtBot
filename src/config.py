"""
config.py – Central configuration for the Signal Engine.
All thresholds are set here; nothing is hardcoded in engine logic.
"""
from dataclasses import dataclass, field
from typing import List

@dataclass
class UniverseConfig:
    min_volume_usdt: float = 10_000_000    # minimum 24h USDT volume

@dataclass
class TimeframeConfig:
    regime: str = "4h"
    trend: str = "1h"
    entry: str = "15m"

@dataclass
class SignalConfig:
    max_daily: int = 3
    min_confidence: int = 60              # PRD §23 threshold

@dataclass
class VolumeConfig:
    rolling_window: int = 20
    min_ratio: float = 1.5               # candidate threshold
    strong_ratio: float = 2.0            # strong candidate bonus

@dataclass
class OIConfig:
    min_change_pct: float = 2.0

@dataclass
class FundingConfig:
    extreme_positive_threshold: float = 0.01   # 1% per 8h – reduce LONG confidence
    extreme_negative_threshold: float = -0.01  # -1% per 8h – reduce SHORT confidence
    confidence_penalty: int = 15               # points to subtract

@dataclass
class RiskConfig:
    account_size: float = 1000.0
    max_risk_pct: float = 1.0            # 1% of account per trade
    max_leverage: int = 10               # hard cap
    min_rr_tp1: float = 1.5             # minimum R:R to accept a signal

@dataclass
class ScoringConfig:
    # Weights must sum to 100
    regime_weight: int = 20
    structure_weight: int = 15
    momentum_weight: int = 15
    volume_weight: int = 15
    oi_weight: int = 15
    funding_weight: int = 5
    liquidity_weight: int = 5
    rr_weight: int = 10

@dataclass
class ManagementConfig:
    tp1_close_pct: int = 50
    tp2_close_pct: int = 25
    tp3_close_pct: int = 25

@dataclass
class AppConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    timeframes: TimeframeConfig = field(default_factory=TimeframeConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    oi: OIConfig = field(default_factory=OIConfig)
    funding: FundingConfig = field(default_factory=FundingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)

# Singleton – import and use directly
CONFIG = AppConfig()
