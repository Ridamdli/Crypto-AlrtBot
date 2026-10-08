"""
Strategy Orchestrator
Orchestrates multiple strategies per coin, handles conflicts,
and manages signal lifecycle for live trading.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from src.config import AppConfig
from src.strategies import STRATEGY_REGISTRY, create_strategy
from src.data.symbol_registry import get_symbol_registry
from src.models.lifecycle import SignalLifecycleTracker, SignalState
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ConflictConfig:
    """Configuration for conflict resolution."""
    # Threshold for considering a signal "strong"
    strong_signal_threshold: float = 0.7
    # Minimum score delta to override conflict
    min_score_delta: int = 15
    # Allow conflicting signals if one is much stronger
    allow_override: bool = True
    # Cooldown hours for duplicate prevention
    cooldown_hours: float = 6.0


@dataclass
class AggregatedSignal:
    """Result of signal aggregation for a symbol."""
    symbol: str
    side: str  # "LONG", "SHORT", "CONFLICT"
    signals: List[Dict[str, Any]]
    consensus_score: float
    conflict: bool
    conflict_details: Optional[str] = None


class ConflictResolver:
    """Resolves conflicts between multiple strategy signals for the same symbol."""
    
    def __init__(self, config: Optional[ConflictConfig] = None):
        self.config = config or ConflictConfig()
    
    def resolve(self, signals: List[Dict[str, Any]]) -> AggregatedSignal:
        """
        Resolve conflicting signals for a single symbol.
        
        Conflict resolution logic:
        - If all signals agree on side → consensus
        - If 2+ LONG and 1 SHORT → LONG with conflict metadata
        - If 1 LONG + 1 SHORT → no alert unless score delta > threshold
        - If 3+ LONG → strong consensus
        """
        if not signals:
            return AggregatedSignal(
                symbol="",
                side="NONE",
                signals=[],
                consensus_score=0.0,
                conflict=False,
                conflict_details="No signals"
            )
        
        symbol = signals[0].get("symbol", "UNKNOWN")
        long_signals = [s for s in signals if s.get("side") == "LONG"]
        short_signals = [s for s in signals if s.get("side") == "SHORT"]
        
        long_count = len(long_signals)
        short_count = len(short_signals)
        
        # Calculate average confidence for each side
        long_conf = sum(s.get("confidence", 0) for s in long_signals) / max(1, long_count)
        short_conf = sum(s.get("confidence", 0) for s in short_signals) / max(1, short_count)
        
        # Determine consensus
        if long_count > 0 and short_count == 0:
            # Pure long consensus
            return AggregatedSignal(
                symbol=symbol,
                side="LONG",
                signals=signals,
                consensus_score=long_conf,
                conflict=False,
                conflict_details=None
            )
        elif short_count > 0 and long_count == 0:
            # Pure short consensus
            return AggregatedSignal(
                symbol=symbol,
                side="SHORT",
                signals=signals,
                consensus_score=short_conf,
                conflict=False,
                conflict_details=None
            )
        elif long_count > 0 and short_count > 0:
            # Conflict between sides
            conflict_details = f"LONG: {long_count} (avg conf: {long_conf:.1f}), SHORT: {short_count} (avg conf: {short_conf:.1f})"
            
            # Check if one side significantly stronger
            conf_delta = abs(long_conf - short_conf)
            if conf_delta >= self.config.min_score_delta and self.config.allow_override:
                # Stronger side wins
                if long_conf > short_conf:
                    return AggregatedSignal(
                        symbol=symbol,
                        side="LONG",
                        signals=signals,
                        consensus_score=long_conf,
                        conflict=True,
                        conflict_details=f"LONG overrides SHORT ({conflict_details})"
                    )
                else:
                    return AggregatedSignal(
                        symbol=symbol,
                        side="SHORT",
                        signals=signals,
                        consensus_score=short_conf,
                        conflict=True,
                        conflict_details=f"SHORT overrides LONG ({conflict_details})"
                    )
            else:
                # No clear winner - no signal
                return AggregatedSignal(
                    symbol=symbol,
                    side="CONFLICT",
                    signals=signals,
                    consensus_score=max(long_conf, short_conf),
                    conflict=True,
                    conflict_details=f"Conflict unresolved: {conflict_details}"
                )
        else:
            return AggregatedSignal(
                symbol=symbol,
                side="NONE",
                signals=[],
                consensus_score=0.0,
                conflict=False,
                conflict_details="No valid signals"
            )


class SignalDeduplicator:
    """Prevents duplicate signals within cooldown period."""
    
    def __init__(self, state_file: str = "data_store/scheduler_state.json", cooldown_hours: float = 6.0):
        self.state_file = state_file
        self.cooldown_hours = cooldown_hours
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        if not os.path.exists(state_file):
            with open(state_file, "w") as f:
                json.dump({"published_signals": {}}, f)
    
    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(self.state_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"published_signals": {}}
    
    def _save_state(self, state: Dict[str, Any]):
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save deduplicator state: {e}")
    
    def is_duplicate(self, symbol: str, side: str) -> bool:
        """Check if signal is duplicate within cooldown."""
        state = self._load_state()
        key = f"{symbol}_{side}"
        published_map = state.get("published_signals", {})
        if key in published_map:
            last_ts_str = published_map[key]
            last_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            elapsed_hours = (now_dt - last_dt).total_seconds() / 3600.0
            if elapsed_hours < self.cooldown_hours:
                logger.info(f"Suppressing duplicate {symbol} {side} (elapsed: {elapsed_hours:.1f}h)")
                return True
        return False
    
    def mark_published(self, symbol: str, side: str, timestamp: str):
        """Record that a signal was published."""
        state = self._load_state()
        key = f"{symbol}_{side}"
        state.setdefault("published_signals", {})[key] = timestamp
        self._save_state(state)


class StrategyOrchestrator:
    """
    Orchestrates multiple strategies per coin, handles conflicts,
    and manages signal lifecycle for live trading.
    """
    
    def __init__(
        self,
        config: AppConfig,
        registry: Any,
        conflict_config: Optional[ConflictConfig] = None,
    ):
        self.config = config
        self.registry = registry
        self.conflict_resolver = ConflictResolver(conflict_config)
        self.deduplicator = SignalDeduplicator()
        self.symbol_registry = get_symbol_registry(config)
        self._strategy_cache: Dict[str, Any] = {}
        self.signal_history: Dict[str, List[Dict]] = {}
        
        # Load top-3 registry if exists
        self.top3_registry: Dict[str, List[str]] = {}
        self._load_top3_registry()
    
    def _load_top3_registry(self, registry_path: str = "reports/top3_by_coin.json"):
        """Load top-3 strategy registry from file."""
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r") as f:
                    self.top3_registry = json.load(f)
                logger.info(f"Loaded top-3 registry for {len(self.top3_registry)} symbols")
            except Exception as e:
                logger.warning(f"Failed to load top-3 registry: {e}")
    
    def _get_strategy_instance(self, symbol: str, family: str) -> Any:
        """Get or create strategy instance for a symbol."""
        cache_key = f"{symbol}:{family}"
        if cache_key not in self._strategy_cache:
            # Load config from top3 registry or use defaults
            config = {}
            if symbol in self.top3_registry:
                for strat_id in self.top3_registry[symbol]:
                    if strat_id.startswith(family):
                        # Extract params from registry
                        pass
            self._strategy_cache[f"{symbol}:{family}"] = create_strategy(family, {"params": {}}, symbol)
        return self._strategy_cache[f"{symbol}:{family}"]
    
    def get_approved_strategies(self, symbol: str) -> List[str]:
        """Get approved strategies for a symbol from top3 registry."""
        if symbol in self.top3_registry:
            return self.top3_registry[symbol]
        # Fallback to priority strategies from config
        from src.config import CONFIG
        symbol_cfg = CONFIG.get_symbol_config(symbol)
        return symbol_cfg.priority_strategies
    
    def get_approved_strategies_for_all(self) -> Dict[str, List[str]]:
        """Get approved strategies for all symbols."""
        result = {}
        for symbol in self.symbol_registry.get_tradable_symbols():
            result[symbol] = self.get_approved_strategies(symbol)
        return result
    
    def process_symbol(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        global_regime: str,
        funding_rate: float,
        oi_data: Dict,
    ) -> List[Dict[str, Any]]:
        """
        Run all approved strategies for a symbol and return signals.
        
        Args:
            symbol: Trading symbol
            market_data: Dict with klines_15m, klines_1h, klines_4h, current_price
            global_regime: BTC regime
            funding_rate: Current funding rate
            oi_data: OI analysis result
            
        Returns:
            List of signal dicts
        """
        if not self.symbol_registry.is_tradable(symbol):
            return []
        
        approved_families = self.get_approved_strategies(symbol)
        if not approved_families:
            return []
        
        all_signals = []
        
        for family in approved_families:
            if family not in STRATEGY_REGISTRY:
                continue
                
            try:
                strategy = create_strategy(family, {"params": {}}, symbol)
                
                # Get market data for this symbol
                klines_15m = market_data.get("klines_15m", [])
                klines_1h = market_data.get("klines_1h", [])
                klines_4h = market_data.get("klines_4h", [])
                current_price = market_data.get("current_price", 0)
                
                if len(klines_15m) < 25:
                    continue
                
                # Generate candidates
                oi_data = market_data.get("oi_data", {})
                funding_rate = market_data.get("funding_rate", 0.0)
                btc_regime = market_data.get("btc_regime", "neutral")
                current_price = market_data.get("current_price", 0)
                
                strategy_candidates = strategy.generate_candidates(
                    klines_15m=market_data.get("klines_15m", []),
                    klines_1h=market_data.get("klines_1h", []),
                    klines_4h=market_data.get("klines_4h", []),
                    btc_regime=market_data.get("btc_regime", "neutral"),
                    oi_data=market_data.get("oi_data", {}),
                    funding_rate=market_data.get("funding_rate", 0.0),
                    current_price=market_data.get("current_price", 0),
                )
                
                for cand in strategy_candidates:
                    signal_dict = {
                        "symbol": cand.symbol,
                        "side": cand.side,
                        "entry": cand.entry_price,
                        "tp1": cand.tp1,
                        "tp2": cand.tp2,
                        "tp3": cand.tp3,
                        "stop_loss": cand.stop_loss,
                        "strategy_id": cand.strategy_id,
                        "strategy_version": cand.strategy_version,
                        "confidence": cand.confidence,
                        "strategy_family": cand.strategy_id.split("_")[0],
                        "strategy_metadata": cand.metadata,
                        "conditions": cand.conditions,
                        "invalidation_conditions": cand.invalidation_conditions,
                        "management_rules": cand.management_rules,
                    }
                    all_signals.append(signal_dict)
                    
            except Exception as e:
                logger.error(f"Error in strategy {family} for {symbol}: {e}")
                continue
        
        return all_signals
    
    def generate_signals(
        self,
        market_data: Dict[str, Dict[str, Any]],
        global_regime: str,
        funding_rates: Dict[str, float],
        oi_data_map: Dict[str, Dict],
    ) -> List[Dict[str, Any]]:
        """
        Generate and aggregate signals for all available symbols.
        
        Args:
            market_data: Dict of symbol -> {klines_15m, klines_1h, klines_4h, current_price, ...}
            global_regime: BTC regime
            funding_rates: Dict of symbol -> funding_rate
            oi_data_map: Dict of symbol -> OI analysis
            
        Returns:
            List of final aggregated signals ready for Telegram
        """
        all_signals_by_symbol: Dict[str, List[Dict]] = {}
        
        # Generate signals per symbol
        for symbol in self.symbol_registry.get_tradable_symbols():
            market_info = market_data.get(symbol, {})
            if not market_info:
                continue
                
            signals = self.process_symbol(
                symbol=symbol,
                market_data=market_info,
                global_regime=market_data.get("btc_regime", "neutral"),
                funding_rate=funding_rates.get(symbol, 0.0),
                oi_data=oi_data_map.get(symbol, {}),
            )
            
            if signals:
                all_signals_by_symbol[symbol] = signals
        
        # Resolve conflicts and deduplicate
        final_signals = []
        
        for symbol, signals in all_signals_by_symbol.items():
            # Resolve conflicts
            aggregated = self.conflict_resolver.resolve(signals)
            
            if aggregated.side in ["LONG", "SHORT"]:
                # Check deduplication
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if not self.deduplicator.is_duplicate(symbol, aggregated.side):
                    self.deduplicator.mark_published(symbol, aggregated.side, ts)
                    
                    # Pick the highest confidence signal as the primary
                    primary = max(aggregated.signals, key=lambda s: s.get("confidence", 0))
                    primary["conflict_resolved"] = aggregated.conflict
                    primary["conflict_details"] = aggregated.conflict_details
                    primary["consensus_score"] = aggregated.consensus_score
                    final_signals.append(primary)
                    
                    self.deduplicator.mark_published(symbol, aggregated.side, ts)
        
        return final_signals
    
    def get_approved_strategies_for_all(self) -> Dict[str, List[str]]:
        """Get approved strategies for all available symbols."""
        result = {}
        for symbol in self.symbol_registry.get_tradable_symbols():
            result[symbol] = self.get_approved_strategies(symbol)
        return result