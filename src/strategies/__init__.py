"""
Strategy Package
Exports all strategy families and the base interface.
"""
from typing import Any, Dict

from .base import BaseStrategy, Direction, SignalCandidate, StrategyRegistry, Timeframe
from .bollinger_atr import BollingerATRStrategy
from .donchian import DonchianBreakoutStrategy
from .momentum import MomentumFilterStrategy
from .volume_breakout import VolumeBreakoutStrategy

# Canonical strategy registry - single source of truth
STRATEGY_REGISTRY = StrategyRegistry()

# Register all strategy families with the canonical registry
STRATEGY_REGISTRY.register_family("donchian", DonchianBreakoutStrategy)
STRATEGY_REGISTRY.register_family("momentum", MomentumFilterStrategy)
STRATEGY_REGISTRY.register_family("bollinger_atr", BollingerATRStrategy)
STRATEGY_REGISTRY.register_family("volume_breakout", VolumeBreakoutStrategy)

# Map strategy families to their default timeframes
STRATEGY_TIMEFRAMES = {
    "donchian": "15m",
    "momentum": "15m",
    "bollinger_atr": "15m",
    "volume_breakout": "15m",
}

# Map coins to their priority strategies (for research prioritization)
COIN_PRIORITY_STRATEGIES = {
    "BTCUSDT": ["momentum"],
    "ETHUSDT": ["donchian", "momentum"],
    "SOLUSDT": ["bollinger_atr", "momentum", "volume_breakout"],
    "ZECUSDT": ["donchian", "bollinger_atr"],
    "XRPUSDT": ["volume_breakout", "donchian"],
    "BNBUSDT": ["donchian", "momentum"],
    "HYPEUSDT": ["bollinger_atr", "volume_breakout"],
    "DOGEUSDT": ["donchian", "momentum"],
    "UNIUSDT": ["donchian", "momentum"],
    "NEARUSDT": ["donchian", "momentum"],
    "ADAUSDT": ["volume_breakout"],
    "AVAXUSDT": ["bollinger_atr", "donchian"],
    "LINKUSDT": ["volume_breakout", "momentum"],
    "DOTUSDT": ["volume_breakout", "donchian"],
}

def create_strategy(family: str, config: dict[str, Any], symbol: str) -> BaseStrategy:
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
    "COIN_PRIORITY_STRATEGIES",
    "STRATEGY_REGISTRY",
    "STRATEGY_TIMEFRAMES",
    "BaseStrategy",
    "BollingerATRStrategy",
    "Direction",
    "DonchianBreakoutStrategy",
    "MomentumFilterStrategy",
    "SignalCandidate",
    "StrategyRegistry",
    "Timeframe",
    "VolumeBreakoutStrategy",
    "create_strategy",
    "register_all_strategies",
]
