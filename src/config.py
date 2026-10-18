"""
config.py – Central configuration for the Signal Engine.
All thresholds are set here; nothing is hardcoded in engine logic.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataAvailability(str, Enum):
    AVAILABLE = "available"
    DATA_INSUFFICIENT = "data_insufficient"
    DATA_MISSING = "data_missing"
    DISABLED = "disabled"


@dataclass
class SymbolConfig:
    symbol: str
    data_availability: DataAvailability = DataAvailability.AVAILABLE
    enabled: bool = True
    priority_strategies: list[str] = field(default_factory=list)


@dataclass
class StrategyFamilyConfig:
    enabled: bool = True
    default_params: dict[str, Any] = field(default_factory=dict)
    param_grid: dict[str, list[Any]] = field(default_factory=dict)


@dataclass
class UniverseConfig:
    min_volume_usdt: float = 10_000_000    # minimum 24h USDT volume
    symbols: list[SymbolConfig] = field(default_factory=lambda: [
        SymbolConfig(symbol="BTCUSDT", priority_strategies=["momentum", "ema_trend", "rsi_mean_reversion"]),
        SymbolConfig(symbol="ETHUSDT", priority_strategies=["donchian", "momentum", "ema_trend"]),
        SymbolConfig(symbol="SOLUSDT", priority_strategies=["bollinger_atr", "momentum", "volume_breakout"]),
        SymbolConfig(symbol="ZECUSDT", priority_strategies=["donchian", "rsi_mean_reversion", "bollinger_atr"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="XRPUSDT", priority_strategies=["volume_breakout", "donchian", "rsi_mean_reversion"]),
        SymbolConfig(symbol="BNBUSDT", priority_strategies=["ema_trend", "donchian", "momentum"]),
        SymbolConfig(symbol="HYPEUSDT", priority_strategies=["bollinger_atr", "volume_breakout", "rsi_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="DOGEUSDT", priority_strategies=["donchian", "momentum", "rsi_mean_reversion"]),
        SymbolConfig(symbol="UNIUSDT", priority_strategies=["donchian", "momentum", "rsi_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="NEARUSDT", priority_strategies=["donchian", "momentum", "rsi_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="ADAUSDT", priority_strategies=["volume_breakout", "rsi_mean_reversion", "ema_trend"]),
        SymbolConfig(symbol="AVAXUSDT", priority_strategies=["bollinger_atr", "ema_trend", "donchian"]),
        SymbolConfig(symbol="LINKUSDT", priority_strategies=["volume_breakout", "momentum", "rsi_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="DOTUSDT", priority_strategies=["volume_breakout", "donchian", "ema_trend"], data_availability=DataAvailability.DATA_MISSING),
    ])


@dataclass
class TimeframeConfig:
    regime: str = "4h"
    trend: str = "1h"
    entry: str = "15m"


@dataclass
class SignalConfig:
    min_confidence: int = 60              # PRD §23 threshold (quality floor; publishing itself is uncapped)


@dataclass
class VolumeConfig:
    rolling_window: int = 20
    min_ratio: float = 1.5               # candidate threshold
    strong_ratio: float = 2.0            # strong candidate bonus


@dataclass
class OIConfig:
    min_change_pct: float = 2.0
    mode: str = "optional"  # "required" | "optional"


@dataclass
class FundingConfig:
    extreme_positive_threshold: float = 0.01   # 1% per 8h – reduce LONG confidence
    extreme_negative_threshold: float = -0.01  # -1% per 8h – reduce SHORT confidence
    confidence_penalty: int = 15               # points to subtract


@dataclass
class TPConfig:
    r_multiple: float = 2.0  # The single TP as R-multiple


@dataclass
class RiskConfig:
    account_size: float = 1000.0
    max_risk_pct: float = 1.0            # 1% of account per trade
    max_leverage: int = 10               # hard cap
    min_rr: float = 2.0                  # minimum R:R to accept a signal
    sl_lookback: int = 20                # structural SL lookback in candles


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
    close_pct: int = 100  # full close at the single TP


@dataclass
class StrategyFamilyConfig:
    enabled: bool = True
    default_params: dict[str, Any] = field(default_factory=dict)
    param_grid: dict[str, list[Any]] = field(default_factory=dict)


@dataclass
class AppConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    timeframes: TimeframeConfig = field(default_factory=TimeframeConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    oi: OIConfig = field(default_factory=OIConfig)
    funding: FundingConfig = field(default_factory=FundingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    tp: TPConfig = field(default_factory=TPConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)

    def validate(self) -> None:
        """Validate configuration consistency."""
        if self.tp.r_multiple < self.risk.min_rr:
            raise ValueError(
                f"Configuration error: TP R-multiple ({self.tp.r_multiple}) < min_rr ({self.risk.min_rr}). "
                f"TP must satisfy TP >= min_rr."
            )

    def compute_hash(self) -> str:
        """Computes a deterministic hash of the active configuration parameters."""
        import hashlib
        import json
        from dataclasses import asdict
        config_dict = asdict(self)
        serialized = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()[:12]

    def get_available_symbols(self) -> list[str]:
        """Get list of symbols with AVAILABLE data (validated historical dataset).

        Research/backtest gate — only these have verified local history.
        """
        return [s.symbol for s in self.universe.symbols if s.enabled and s.data_availability == DataAvailability.AVAILABLE]

    def get_tradable_symbols(self) -> list[str]:
        """Get list of all enabled symbols for LIVE trading.

        Live scans use live Binance data, so coins without validated
        historical rows (DATA_MISSING) can still trade here. Those coins
        carry NO backtest evidence — see research reports before sizing up.
        """
        return [s.symbol for s in self.universe.symbols if s.enabled]

    def get_all_symbols(self) -> list[str]:
        """Get list of all configured symbols."""
        return [s.symbol for s in self.universe.symbols if s.enabled]

    def get_symbol_config(self, symbol: str) -> SymbolConfig:
        """Get configuration for a specific symbol."""
        for s in self.universe.symbols:
            if s.symbol == symbol:
                return s
        raise ValueError(f"Symbol {symbol} not found in universe")


# Singleton – import and use directly
CONFIG = AppConfig()
