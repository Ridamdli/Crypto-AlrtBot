# Implementation Plan: Multi-Coin Multi-Strategy Engine Upgrade

## Executive Summary

Upgrade the existing Crypto Daily Futures Signal Engine from a single-strategy, 10-coin research prototype to a production-ready, multi-coin (14 coins), multi-strategy (6 families) engine with full research/backtesting infrastructure and live orchestration capabilities.

---

## Phase 1: Foundation & Configuration (Week 1)

### 1.1 Extend Configuration System
**File:** `src/config.py`

**Changes:**
- Add `StrategyConfig` dataclass for strategy-specific parameters
- Add `UniverseConfig` with 14-coin default universe
- Add `StrategyFamilyConfig` for each of 6 strategy families
- Add `DataAvailability` enum for symbol status tracking
- Add `SymbolRegistryConfig` for symbol metadata

```python
@dataclass
class DataAvailability(Enum):
    AVAILABLE = "available"
    DATA_INSUFFICIENT = "data_insufficient"
    DATA_MISSING = "data_missing"
    DISABLED = "disabled"

@dataclass
class SymbolConfig:
    symbol: str
    data_availability: DataAvailability = DataAvailability.AVAILABLE
    enabled: bool = True
    priority_strategies: List[str] = field(default_factory=list)

@dataclass
class StrategyFamilyConfig:
    enabled: bool = True
    default_params: Dict[str, Any] = field(default_factory=dict)
    param_grid: Dict[str, List[Any]] = field(default_factory=dict)

@dataclass
class UniverseConfig:
    min_volume_usdt: float = 10_000_000
    symbols: List[SymbolConfig] = field(default_factory=lambda: [
        SymbolConfig(symbol="BTCUSDT", priority_strategies=["donchian", "momentum", "ema_trend"]),
        SymbolConfig(symbol="ETHUSDT", priority_strategies=["donchian", "momentum", "ema_trend"]),
        SymbolConfig(symbol="SOLUSDT", priority_strategies=["volatility_breakout", "momentum", "volume_breakout"]),
        SymbolConfig(symbol="ZECUSDT", priority_strategies=["donchian", "rsi_mean_reversion", "volatility_breakout"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="XRPUSDT", priority_strategies=["volume_breakout", "donchian", "rsi_bb_mean_reversion"]),
        SymbolConfig(symbol="BNBUSDT", priority_strategies=["ema_trend", "donchian", "momentum"]),
        SymbolConfig(symbol="HYPEUSDT", priority_strategies=["atr_volatility_breakout", "range_breakout", "rsi_reversal"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="DOGEUSDT", priority_strategies=["donchian", "momentum", "rsi_mean_reversion"]),
        SymbolConfig(symbol="UNIUSDT", priority_strategies=["donchian_range_breakout", "momentum", "rsi_bb_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="NEARUSDT", priority_strategies=["donchian", "momentum", "rsi_mean_reversion"], data_availability=DataAvailability.DATA_MISSING),
        SymbolConfig(symbol="ADAUSDT", priority_strategies=["volume_breakout", "rsi_mean_reversion", "ema_trend"]),
        SymbolConfig(symbol="AVAXUSDT", priority_strategies=["volatility_breakout", "ema_trend", "donchian"]),
        SymbolConfig(symbol="LINKUSDT", priority_strategies=["volume_breakout", "momentum", "rsi_bb_mean_reversion"]),
        SymbolConfig(symbol="DOTUSDT", priority_strategies=["volume_breakout", "donchian", "ema_trend"]),
    ])
```

### 1.2 Strategy Configuration System
**New File:** `src/config/strategy_config.py`

```python
@dataclass
class StrategyParam:
    name: str
    type: str  # "int", "float", "bool"
    default: Any
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    description: str = ""

@dataclass
class StrategyConfig:
    family: str  # "donchian", "momentum", "ema_trend", "bollinger_atr", "rsi_mean_reversion", "volume_breakout"
    direction: str  # "LONG", "SHORT", "BOTH"
    timeframe: str  # "15m", "1h", "4h"
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    param_schema: Dict[str, StrategyParam] = field(default_factory=dict)
```

### 1.3 Symbol Registry
**New File:** `src/data/symbol_registry.py`

- Load symbol configurations from config
- Track data availability per symbol
- Provide filtering by availability
- Support enabling/disabling symbols

---

## Phase 2: Strategy Framework (Week 2)

### 2.1 Base Strategy Interface
**New File:** `src/strategies/base.py`

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"

class Timeframe(Enum):
    TF_15M = "15m"
    TF_1H = "1h"
    TF_4H = "4h"

@dataclass
class SignalCandidate:
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    confidence: int
    strategy_id: str
    strategy_version: str
    timeframe: str
    parameters: Dict[str, Any]
    conditions: List[str]
    invalidation_conditions: List[str]
    management_rules: List[str]
    metadata: Dict[str, Any]

class BaseStrategy(ABC):
    """Base class for all strategy families."""
    
    # Class attributes to be overridden
    family: str = "base"
    supported_directions: List[str] = ["LONG", "SHORT"]
    supported_timeframes: List[str] = ["15m", "1h", "4h"]
    default_params: Dict[str, Any] = {}
    param_schema: Dict[str, Dict] = {}
    
    def __init__(self, config: Dict[str, Any], symbol: str):
        self.config = config
        self.symbol = symbol
        self.params = {**self.default_params, **config.get("params", {})}
    
    @abstractmethod
    def generate_candidates(
        self, 
        klines_15m: List[Dict], 
        klines_1h: List[Dict], 
        klines_4h: List[Dict],
        btc_regime: str,
        oi_data: Dict,
        funding_rate: float,
        current_price: float
    ) -> List[SignalCandidate]:
        """Generate signal candidates from market data."""
        pass
    
    def validate_params(self) -> bool:
        """Validate strategy parameters."""
        return True
    
    def get_param_grid(self) -> Dict[str, List]:
        """Return parameter grid for optimization."""
        return {}
```

### 2.2 Strategy Registry
**New File:** `src/strategies/registry.py`

```python
class StrategyRegistry:
    """Registry of all available strategy families and their instances."""
    
    def __init__(self):
        self._families: Dict[str, Type[BaseStrategy]] = {}
        self._instances: Dict[str, BaseStrategy] = {}
    
    def register_family(self, family_name: str, strategy_class: Type[BaseStrategy]):
        self._families[family_name] = strategy_class
    
    def create_instance(self, family: str, config: Dict, symbol: str) -> BaseStrategy:
        if family not in self._families:
            raise ValueError(f"Unknown strategy family: {family}")
        return self._families[family](config, symbol)
    
    def get_family(self, family: str) -> Optional[Type[BaseStrategy]]:
        return self._families.get(family)
    
    def list_families(self) -> List[str]:
        return list(self._families.keys())
```

---

## Phase 3: Strategy Family Implementations (Week 2-3)

### 3.1 Donchian Breakout Strategy
**New File:** `src/strategies/donchian.py`

```python
class DonchianBreakoutStrategy(BaseStrategy):
    family = "donchian"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "lookback": 20,
        "breakout_buffer": 0.001,  # 0.1% buffer
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "volume_confirmation": True,
        "volume_ratio_min": 1.5,
        "regime_filter": True,
    }
    
    param_schema = {
        "lookback": {"type": "int", "min": 10, "max": 50},
        "breakout_buffer": {"type": "float", "min": 0, "max": 0.01},
        "atr_filter": {"type": "bool"},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0},
        "volume_confirmation": {"type": "bool"},
        "volume_ratio_min": {"type": "float", "min": 1.0, "max": 3.0},
        "regime_filter": {"type": "bool"},
    }
    
    def generate_candidates(self, klines_15m, klines_1h, klines_4h, btc_regime, oi_data, funding_rate, current_price):
        # Implementation: Donchian channel breakout with ATR filter
        # Use previous-period channel values (no current candle)
        pass
```

### 3.2 Momentum Filter Strategy
**New File:** `src/strategies/momentum.py`

```python
class MomentumFilterStrategy(BaseStrategy):
    family = "momentum"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "lookback": 20,
        "threshold_pct": 0.02,  # 2%
        "confirmation_bars": 1,
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.0,
        "volume_filter": True,
        "volume_ratio_min": 1.5,
        "trend_filter": True,
    }
    
    param_schema = {
        "lookback": {"type": "int", "min": 5, "max": 50},
        "threshold_pct": {"type": "float", "min": 0.005, "max": 0.10},
        "confirmation_bars": {"type": "int", "min": 1, "max": 5},
        "atr_filter": {"type": "bool"},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0},
        "volume_filter": {"type": "bool"},
        "volume_ratio_min": {"type": "float", "min": 1.0, "max": 3.0},
        "trend_filter": {"type": "bool"},
    }
    
    def generate_candidates(self, ...):
        # Implementation: Price expansion + volume + trend confirmation
        pass
```

### 3.3 EMA Trend / Pullback Strategy
**New File:** `src/strategies/ema_trend.py`

```python
class EMATrendStrategy(BaseStrategy):
    family = "ema_trend"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "fast_period": 20,
        "slow_period": 50,
        "pullback_pct": 0.5,
        "confirmation_bars": 1,
        "atr_stop": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    }
    
    param_schema = {
        "fast_period": {"type": "int", "min": 5, "max": 50},
        "slow_period": {"type": "int", "min": 20, "max": 200},
        "pullback_pct": {"type": "float", "min": 0.1, "max": 2.0},
        "confirmation_bars": {"type": "int", "min": 1, "max": 5},
        "atr_stop": {"type": "bool"},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0},
    }
    
    def generate_candidates(self, ...):
        # Implementation: EMA trend + pullback to fast EMA
        # Test combinations: 20/50, 20/100, 50/100
        pass
```

### 3.4 Bollinger/ATR Volatility Breakout
**New File:** `src/strategies/bollinger_atr.py`

```python
class BollingerATRStrategy(BaseStrategy):
    family = "bollinger_atr"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "bb_period": 20,
        "bb_std": 2.0,
        "bandwidth_lookback": 20,
        "bandwidth_threshold": 0.1,
        "atr_period": 14,
        "atr_multiplier": 2.0,
        "volume_threshold": 1.5,
    }
    
    param_schema = {
        "bb_period": {"type": "int", "min": 10, "max": 50},
        "bb_std": {"type": "float", "min": 1.0, "max": 3.0},
        "bandwidth_lookback": {"type": "int", "min": 10, "max": 50},
        "bandwidth_threshold": {"type": "float", "min": 0.01, "max": 0.5},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 1.0, "max": 4.0},
        "volume_threshold": {"type": "float", "min": 1.0, "max": 3.0},
    }
    
    def generate_candidates(self, ...):
        # Implementation: BB compression + breakout + ATR stop
        pass
```

### 3.5 RSI Mean Reversion
**New File:** `src/strategies/rsi_mean_reversion.py`

```python
class RSIMeanReversionStrategy(BaseStrategy):
    family = "rsi_mean_reversion"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "rsi_period": 14,
        "oversold_threshold": 30,
        "overbought_threshold": 70,
        "confirmation_threshold": 5,  # bars to confirm
        "trend_filter": True,
        "atr_stop": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "max_holding_bars": 50,
    }
    
    param_schema = {
        "rsi_period": {"type": "int", "min": 7, "max": 30},
        "oversold_threshold": {"type": "int", "min": 10, "max": 40},
        "overbought_threshold": {"type": "int", "min": 60, "max": 90},
        "confirmation_threshold": {"type": "int", "min": 1, "max": 10},
        "trend_filter": {"type": "bool"},
        "atr_stop": {"type": "bool"},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0},
        "max_holding_bars": {"type": "int", "min": 10, "max": 200},
    }
    
    def generate_candidates(self, ...):
        # Implementation: RSI oversold/overbought + reversal confirmation + regime filter
        pass
```

### 3.6 Volume-Confirmed Breakout
**New File:** `src/strategies/volume_breakout.py`

```python
class VolumeBreakoutStrategy(BaseStrategy):
    family = "volume_breakout"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "structural_lookback": 20,
        "volume_lookback": 20,
        "volume_multiplier": 2.0,
        "breakout_buffer": 0.001,
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    }
    
    param_schema = {
        "structural_lookback": {"type": "int", "min": 10, "max": 50},
        "volume_lookback": {"type": "int", "min": 10, "max": 50},
        "volume_multiplier": {"type": "float", "min": 1.0, "max": 5.0},
        "breakout_buffer": {"type": "float", "min": 0, "max": 0.01},
        "atr_filter": {"type": "bool"},
        "atr_period": {"type": "int", "min": 7, "max": 30},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0},
    }
    
    def generate_candidates(self, ...):
        # Implementation: Structural breakout + volume confirmation
        pass
```

### 3.7 Strategy Registry Integration
**New File:** `src/strategies/__init__.py`

```python
from .base import BaseStrategy, Direction, Timeframe
from .donchian import DonchianBreakoutStrategy
from .momentum import MomentumFilterStrategy
from .ema_trend import EMATrendStrategy
from .bollinger_atr import BollingerATRStrategy
from .rsi_mean_reversion import RSIMeanReversionStrategy
from .volume_breakout import VolumeBreakoutStrategy

STRATEGY_REGISTRY = {
    "donchian": DonchianBreakoutStrategy,
    "momentum": MomentumFilterStrategy,
    "ema_trend": EMATrendStrategy,
    "bollinger_atr": BollingerATRStrategy,
    "rsi_mean_reversion": RSIMeanReversionStrategy,
    "volume_breakout": VolumeBreakoutStrategy,
}

def create_strategy(family: str, config: Dict, symbol: str) -> BaseStrategy:
    if family not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy family: {family}")
    return STRATEGY_REGISTRY[family](config, symbol)

__all__ = [
    "BaseStrategy", "Direction", "Timeframe", "SignalCandidate",
    "DonchianBreakoutStrategy", "MomentumFilterStrategy", "EMATrendStrategy",
    "BollingerATRStrategy", "RSIMeanReversionStrategy", "VolumeBreakoutStrategy",
    "STRATEGY_REGISTRY", "create_strategy",
]
```

---

## Phase 4: Research & Backtesting Infrastructure (Week 3-4)

### 4.1 Strategy Research Runner
**New File:** `src/research/strategy_research.py`

```python
class StrategyResearchRunner:
    """
    Runs strategy research across all coins, strategies, and parameter combinations.
    Implements walk-forward validation and parameter robustness analysis.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.registry = StrategyRegistry()
        self._register_families()
    
    def _register_families(self):
        from src.strategies import STRATEGY_REGISTRY
        for name, cls in STRATEGY_REGISTRY.items():
            self.registry.register_family(name, cls)
    
    def run_research(
        self,
        symbols: Optional[List[str]] = None,
        strategy_families: Optional[List[str]] = None,
        param_grids: Optional[Dict] = None,
        walk_forward_windows: int = 3,
        train_ratio: float = 0.6,
    ) -> Dict[str, Any]:
        """
        Run complete strategy research pipeline.
        
        Returns:
            Dict with results, rankings, top3_by_coin, walk_forward results
        """
        pass
    
    def run_parameter_sweep(
        self,
        strategy_family: str,
        symbol: str,
        param_grid: Dict[str, List],
        train_timestamps: List[int],
        val_timestamps: List[int],
    ) -> List[Dict]:
        """Run parameter sweep for a single strategy/symbol on TRAIN/VALIDATION."""
        pass
    
    def run_walk_forward(
        self,
        strategy_family: str,
        symbol: str,
        config: Dict,
        timestamps: List[int],
        n_windows: int = 3,
        train_ratio: float = 0.6,
    ) -> List[Dict]:
        """Run walk-forward evaluation for a strategy configuration."""
        pass
    
    def calculate_stability(self, results: List[Dict]) -> Dict:
        """Analyze parameter stability - plateaus vs peaks."""
        pass
    
    def rank_candidates(self, results: List[Dict]) -> List[Dict]:
        """Rank candidates using composite score with diversity penalty."""
        pass
    
    def select_top3(self, ranked: List[Dict]) -> List[Dict]:
        """Select top 3 with diversity penalty."""
        pass
```

### 4.2 Walk-Forward Validation
**Extend:** `src/backtest/tuner.py`

- Add strategy-aware walk-forward evaluation
- Support multiple evaluation windows
- Track stability metrics per window

### 4.3 Parameter Robustness Analysis
**New File:** `src/research/stability.py`

```python
def analyze_parameter_stability(results: List[Dict], param_name: str, metric: str = "expected_r") -> Dict:
    """Identify plateaus vs isolated peaks in parameter space."""
    pass

def calculate_robustness_score(results: List[Dict]) -> float:
    """Composite robustness score."""
    pass
```

---

## Phase 5: Live Orchestration (Week 5)

### 5.1 Strategy Orchestrator
**New File:** `src/orchestrator.py`

```python
class StrategyOrchestrator:
    """
    Orchestrates multiple strategies per coin, handles conflicts,
    and manages signal lifecycle.
    """
    
    def __init__(self, config: AppConfig, registry: StrategyRegistry):
        self.config = config
        self.registry = registry
        self.active_strategies: Dict[str, List[BaseStrategy]] = {}
        self.signal_history: Dict[str, List[Dict]] = {}
    
    def load_top3_registry(self, registry_path: str):
        """Load top3_by_coin.json into active strategies."""
        pass
    
    def process_symbol(self, symbol: str, market_data: Dict) -> List[Dict]:
        """Run all active strategies for a symbol, return signals."""
        pass
    
    def resolve_conflicts(self, signals: List[Dict]) -> List[Dict]:
        """
        Conflict resolution policy:
        - 2 LONG + 1 SHORT → LONG with conflict metadata
        - 1 LONG + 1 SHORT → no alert unless score delta > threshold
        - 3 LONG → strong consensus
        """
        pass
    
    def deduplicate_signals(self, signals: List[Dict]) -> List[Dict]:
        """Deduplicate by symbol+side within cooldown."""
        pass
    
    def update_lifecycle(self, signal: Dict, event: str):
        """Update signal lifecycle state."""
        pass
```

### 5.2 Conflict Resolution
**Extend:** `src/orchestrator.py`

```python
class ConflictResolver:
    """Configurable conflict resolution for multi-strategy signals."""
    
    def __init__(self, config: Dict):
        self.threshold = config.get("conflict_threshold", 0.1)
        self.min_score_delta = config.get("min_score_delta", 10)
    
    def resolve(self, signals: List[Dict]) -> List[Dict]:
        """Apply conflict resolution policy."""
        pass
```

### 5.3 Orchestrator Integration
**Extend:** `src/scheduler.py`

- Add `StrategyOrchestrator` to `BotScheduler`
- Replace single pipeline call with orchestrator
- Maintain duplicate prevention per symbol+side
- Add signal lifecycle tracking

---

## Phase 6: Telegram & Reporting (Week 5)

### 6.1 Telegram Format Update
**Extend:** `src/utils/telegram_formatter.py`

```python
def format_signal(signal: SignalModel, rank: int = 1) -> str:
    """Format signal with strategy info."""
    # Include: strategy_id, strategy_version, timeframe
    pass

def format_conflict_signal(signals: List[SignalModel], rank: int) -> str:
    """Format conflict signals with metadata."""
    pass
```

### 6.2 Reporting System
**New File:** `src/reporting/report_generator.py`

```python
class ReportGenerator:
    def generate_top3_report(self, top3_by_coin: Dict) -> str:
        """Generate markdown table for top 3 per coin."""
        pass
    
    def generate_robustness_report(self, stability_results: Dict) -> str:
        pass
    
    def generate_walk_forward_report(self, wf_results: Dict) -> str:
        pass
    
    def generate_top3_json(self, top3_by_coin: Dict) -> Dict:
        """Generate top3_by_coin.json for live registry."""
        pass
```

### 6.3 Top3 Registry
**New File:** `src/data/top3_registry.py`

```python
class Top3Registry:
    """Loads and serves top3_by_coin.json for live orchestration."""
    
    def __init__(self, registry_path: str):
        self.registry_path = registry_path
        self._data = None
    
    def load(self) -> Dict:
        pass
    
    def get_strategies_for_symbol(self, symbol: str) -> List[Dict]:
        pass
    
    def is_strategy_active(self, symbol: str, strategy_id: str) -> bool:
        pass
```

---

## Phase 7: Testing & Validation (Week 6)

### 7.1 Strategy Tests
**New File:** `tests/test_strategies.py`

```python
# Tests for each strategy family:
# - Donchian channel calculation
# - Momentum threshold detection
# - EMA crossover/pullback logic
# - Bollinger band compression/breakout
# - RSI oversold/overbought + reversal
# - Volume breakout confirmation
# - Long/Short signal generation
# - No-lookahead verification
# - Parameter validation
# - Rolling indicator calculations (EMA, RSI, BB, ATR)
```

### 7.2 Integration Tests
**Extend:** `tests/test_backtest.py`

- Strategy registry tests
- Strategy parameter validation
- Walk-forward splitting
- Parameter robustness analysis
- Top-3 diversity selection

### 7.3 Smoke Test
**New File:** `scripts/smoke_test.py`

```python
# Small real-data test:
# - Load 1 coin, 1 strategy, 1 month data
# - Run pipeline
# - Verify signal generation
# - Verify no look-ahead
```

---

## Phase 8: Documentation & CLI (Week 6)

### 8.1 Research CLI
**New File:** `scripts/run_strategy_research.py`

```python
def main():
    parser = argparse.ArgumentParser(description="Strategy Research Runner")
    parser.add_argument("--symbol", type=str, help="Single symbol to research")
    parser.add_argument("--strategy", type=str, help="Specific strategy family")
    parser.add_argument("--all", action="store_true", help="Run all coins/strategies")
    parser.add_argument("--top3", action="store_true", help="Generate top3 report")
    parser.add_argument("--report", action="store_true", help="Generate all reports")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols")
    parser.add_argument("--strategies", nargs="+", help="Strategy families")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward")
    parser.add_argument("--output-dir", default="reports", help="Output directory")
```

### 8.2 README Updates
**Extend:** `README.md`

- Document 14 coins
- Document 6 strategy families
- Research workflow
- Validation methodology
- Top-3 selection process
- How to run research
- How to inspect reports
- Research → Paper → Live flow

### 8.3 Architecture Documentation
**New File:** `docs/ARCHITECTURE.md`

- Component diagram
- Data flow
- Strategy lifecycle
- Configuration reference

---

## Phase 9: Data Preparation (Week 1-6, ongoing)

### 9.1 Data Validation
**Extend:** `scripts/validate_data.py`

- Validate all 14 coins for 15m/1h/4h
- Check timestamps, duplicates, missing candles, OHLC validity
- Mark ZECUSDT, HYPEUSDT, UNIUSDT, NEARUSDT as DATA_MISSING
- Update symbol registry

### 9.2 Data Download
**Extend:** `scripts/download_history.py`

- Support downloading for all 14 symbols
- Add DATA_MISSING flag for unavailable symbols
- Resume capability for interrupted downloads

---

## Implementation Order Summary

| Phase | Components | Est. Effort |
|-------|------------|-------------|
| 1 | Config, Symbol Registry, Strategy Config | 2-3 days |
| 2 | Base Strategy, Registry, 6 Families | 4-5 days |
| 3 | Strategy Implementations (6 families) | 5-6 days |
| 4 | Research Runner, Walk-Forward, Stability | 4-5 days |
| 5 | Orchestrator, Conflict Resolution, Top3 Registry | 3-4 days |
| 6 | Telegram Format, Reports, Top3 Registry | 2-3 days |
| 7 | Tests (unit + integration + smoke) | 3-4 days |
| 8 | Documentation, CLI, Architecture Docs | 2-3 days |
| 9 | Data Validation & Download | 2-3 days |
| **Total** | **~30-35 days** | |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Strategy implementations may have bugs | Comprehensive unit tests per family + smoke test |
| Walk-forward may be slow | Parallel processing per coin; configurable windows |
| Parameter sweeps explode combinatorially | Prune with early stopping; focus on priority params |
| Live orchestrator conflicts | Deterministic resolution with config; extensive tests |
| Data missing for 4 coins | Explicit DATA_MISSING state; skip gracefully |
| Look-ahead bugs | Deterministic replay tests + no-lookahead regression tests |

---

## Success Criteria

1. ✅ All 122 existing tests pass
2. ✅ 6 strategy families implemented with tests
3. ✅ 14-coin universe configurable with data availability tracking
4. ✅ Strategy research runner produces top3_by_coin.json
5. ✅ Walk-forward validation implemented
6. ✅ Parameter stability analysis (plateaus vs peaks)
7. ✅ Top-3 selection with diversity penalty
8. ✅ Minimum validation gates (no forced top-3)
9. ✅ Live orchestrator with conflict resolution
10. ✅ Top3 registry loads for live trading
11. ✅ Telegram format includes strategy metadata
12. ✅ All tests pass (existing + new)
13. ✅ No synthetic data, no fabricated results
13. ✅ Documentation complete
14. ✅ CLI for research workflow