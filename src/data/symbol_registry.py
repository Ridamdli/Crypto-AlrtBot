"""
Symbol Registry
Tracks data availability and manages the 14-coin universe.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum
from src.config import AppConfig, DataAvailability, SymbolConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SymbolRegistry:
    """
    Manages the symbol universe with data availability tracking.
    """
    
    def __init__(self, config: Optional[AppConfig] = None):
        from src.config import CONFIG
        self.config = config or CONFIG
        self._symbols: Dict[str, SymbolConfig] = {}
        self._load_from_config()
    
    def _load_from_config(self):
        """Load symbols from configuration."""
        for sym_config in self.config.universe.symbols:
            self._symbols[sym_config.symbol] = sym_config
    
    def get_available_symbols(self) -> List[str]:
        """Get list of symbols with AVAILABLE data."""
        return [s for s in self._symbols if self._symbols[s].data_availability == DataAvailability.AVAILABLE and self._symbols[s].enabled]
    
    def get_all_symbols(self) -> List[str]:
        """Get all enabled symbols."""
        return [s for s in self._symbols if self._symbols[s].enabled]
    
    def get_symbol_config(self, symbol: str) -> SymbolConfig:
        """Get configuration for a specific symbol."""
        if symbol not in self._symbols:
            raise ValueError(f"Symbol {symbol} not found in registry")
        return self._symbols[symbol]
    
    def is_available(self, symbol: str) -> bool:
        """Check if symbol has available data (validated history; research gate)."""
        if symbol not in self._symbols:
            return False
        cfg = self._symbols[symbol]
        return cfg.enabled and cfg.data_availability == DataAvailability.AVAILABLE

    def is_tradable(self, symbol: str) -> bool:
        """Check if symbol is enabled for LIVE trading (live Binance data)."""
        if symbol not in self._symbols:
            return False
        return self._symbols[symbol].enabled

    def get_tradable_symbols(self) -> List[str]:
        """Get all enabled symbols for LIVE trading (all 14 coins)."""
        return [s for s in self._symbols if self._symbols[s].enabled]
    
    def get_data_availability(self, symbol: str) -> DataAvailability:
        """Get data availability status for a symbol."""
        if symbol not in self._symbols:
            return DataAvailability.DATA_MISSING
        return self._symbols[symbol].data_availability
    
    def set_data_availability(self, symbol: str, availability: DataAvailability):
        """Update data availability for a symbol."""
        if symbol in self._symbols:
            self._symbols[symbol].data_availability = availability
            logger.info(f"Updated {symbol} data availability to {availability.value}")
    
    def get_priority_strategies(self, symbol: str) -> List[str]:
        """Get priority strategies for a symbol."""
        if symbol not in self._symbols:
            return []
        return self._symbols[symbol].priority_strategies
    
    def enable_symbol(self, symbol: str):
        """Enable a symbol."""
        if symbol in self._symbols:
            self._symbols[symbol].enabled = True
    
    def disable_symbol(self, symbol: str):
        """Disable a symbol."""
        if symbol in self._symbols:
            self._symbols[symbol].enabled = False
    
    def get_all_symbols_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all symbols."""
        return {
            sym: {
                "enabled": cfg.enabled,
                "data_availability": cfg.data_availability.value,
                "priority_strategies": cfg.priority_strategies,
            }
            for sym, cfg in self._symbols.items()
        }


# Global registry instance
SYMBOL_REGISTRY = None

def get_symbol_registry(config: Optional[AppConfig] = None) -> SymbolRegistry:
    """Get or create the global symbol registry."""
    global SYMBOL_REGISTRY
    if SYMBOL_REGISTRY is None:
        SYMBOL_REGISTRY = SymbolRegistry(config)
    return SYMBOL_REGISTRY