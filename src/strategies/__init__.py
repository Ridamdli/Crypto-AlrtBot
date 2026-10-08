"""
Strategy Package
Exports all strategy families and the base interface.
"""
from typing import Any, Dict

from .base import BaseStrategy, Direction, Timeframe, SignalCandidate, StrategyRegistry
from .donchian import DonchianBreakoutStrategy
from .momentum import MomentumFilterStrategy
from .ema_trend import EMATrendStrategy
from .bollinger_atr import BollingerATRStrategy
from .rsi_mean_reversion import RSIMeanReversionStrategy
from .volume_breakout import VolumeBreakoutStrategy

# Canonical strategy registry - single source of truth
STRATEGY_REGISTRY = StrategyRegistry()

# Register all strategy families with the canonical registry
STRATEGY_REGISTRY.register_family("donchian", DonchianBreakoutStrategy)
STRATEGY_REGISTRY.register_family("momentum", MomentumFilterStrategy)
STRATEGY_REGISTRY.register_family("ema_trend", EMATrendStrategy)
STRATEGY_REGISTRY.register_family("bollinger_atr", BollingerATRStrategy)
STRATEGY_REGISTRY.register_family("rsi_mean_reversion", RSIMeanReversionStrategy)
STRATEGY_REGISTRY.register_family("volume_breakout", VolumeBreakoutStrategy)

# Map strategy families to their default timeframes
STRATEGY_TIMEFRAMES = {
    "donchian": "15m",
    "momentum": "15m",
    "ema_trend": "15m",
    "bollinger_atr": "15m",
    "rsi_mean_reversion": "15m",
    "volume_breakout": "15m",
}

# Map coins to their priority strategies (for research prioritization)
COIN_PRIORITY_STRATEGIES = {
    "BTCUSDT": ["momentum", "ema_trend", "rsi_mean_reversion"],
    "ETHUSDT": ["donchian", "momentum", "ema_trend"],
    "SOLUSDT": ["bollinger_atr", "momentum", "volume_breakout"],
    "ZECUSDT": ["donchian", "rsi_mean_reversion", "bollinger_atr"],
    "XRPUSDT": ["volume_breakout", "donchian", "rsi_mean_reversion"],
    "BNBUSDT": ["ema_trend", "donchian", "momentum"],
    "HYPEUSDT": ["bollinger_atr", "volume_breakout", "rsi_mean_reversion"],
    "DOGEUSDT": ["donchian", "momentum", "rsi_mean_reversion"],
    "UNIUSDT": ["donchian", "momentum", "rsi_mean_reversion"],
    "NEARUSDT": ["donchian", "momentum", "rsi_mean_reversion"],
    "ADAUSDT": ["volume_breakout", "rsi_mean_reversion", "ema_trend"],
    "AVAXUSDT": ["bollinger_atr", "ema_trend", "donchian"],
    "LINKUSDT": ["volume_breakout", "momentum", "rsi_mean_reversion"],
    "DOTUSDT": ["volume_breakout", "donchian", "ema_trend"],
}

def create_strategy(family: str, config: Dict[str, Any], symbol: str) -> BaseStrategy:
    """Factory function to create a strategy instance."""
    if family not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy family: {family}")
    return STRATEGY_REGISTRY.create(family, config, symbol)


def register_all_strategies(registry: StrategyRegistry) -> None:
    """Register all strategy families with the given registry."""
    for name in STRATEGY_REGISTRY.list_families():
        cls = STRATEGY_REGISTRY.get(name)
        registry.register_family(name, cls)


__all__ = [
    "BaseStrategy",
    "Direction", 
    "Timeframe",
    "SignalCandidate",
    "StrategyRegistry",
    "DonchianBreakoutStrategy",
    "MomentumFilterStrategy",
    "EMATrendStrategy",
    "BollingerATRStrategy",
    "RSIMeanReversionStrategy",
    "VolumeBreakoutStrategy",
    "STRATEGY_REGISTRY",
    "STRATEGY_TIMEFRAMES",
    "COIN_PRIORITY_STRATEGIES",
    "create_strategy",
    "register_all_strategies",
]