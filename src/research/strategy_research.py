"""
Strategy Research Runner
Orchestrates strategy research across all coins, strategies, and parameter combinations.
Implements walk-forward validation and parameter robustness analysis.
"""
import os
import json
import copy
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from src.config import AppConfig
from src.data.cached_client import CachedFuturesClient
from src.pipeline import SignalPipeline
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.backtest.tuner import ThresholdTuner, DatasetSplit
from src.strategies import STRATEGY_REGISTRY, STRATEGY_TIMEFRAMES, COIN_PRIORITY_STRATEGIES, create_strategy
from src.data.symbol_registry import get_symbol_registry
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StrategyResearchRunner:
    """
    Runs strategy research across all coins, strategies, and parameter combinations.
    Implements walk-forward validation and parameter robustness analysis.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        # Register all strategy families
        for name, cls in STRATEGY_REGISTRY._families.items():
            STRATEGY_REGISTRY.register_family(name, cls)
    
    def _get_strategies_for_symbol(self, symbol: str) -> List[str]:
        """Get strategy families to test for a symbol."""
        from src.config import CONFIG
        symbol_cfg = CONFIG.get_symbol_config(symbol)
        return symbol_cfg.priority_strategies or list(STRATEGY_REGISTRY._families.keys())
    
    def _get_klines_for_timeframe(self, client: CachedFuturesClient, symbol: str, timeframe: str, end_ts: int, limit: int = 200) -> List[Dict]:
        """Fetch klines for a specific timeframe."""
        return client.store.get_connection().execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines WHERE symbol=? AND interval=? AND timestamp <= ? ORDER BY timestamp DESC LIMIT ?",
            (symbol, timeframe, end_ts, limit)
        )
    
    def run_parameter_sweep(
        self,
        strategy_family: str,
        symbol: str,
        param_grid: Dict[str, List[Any]],
        train_timestamps: List[int],
        val_timestamps: List[int],
        client: CachedFuturesClient,
    ) -> List[Dict]:
        """Run parameter sweep for a single strategy/symbol on TRAIN/VALIDATION."""
        from src.backtest.tuner import ThresholdTuner
        from src.strategies import create_strategy
        
        tuner = ThresholdTuner()
        
        def eval_provider(cfg: Any):
            # Create strategy instance
            strategy = create_strategy(strategy_family, {"params": cfg}, symbol)
            
            # Generate evaluations for this config on train timestamps
            evals = []
            for ts in train_timestamps:
                # Fetch klines
                klines_15m = self._get_klines_for_timeframe(client, symbol, "15m", ts, 200)
                klines_1h = self._get_klines_for_timeframe(client, symbol, "1h", ts, 200)
                klines_4h = self._get_klines_for_timeframe(client, symbol, "4h", ts, 200)
                
                if len(klines_15m) < 25:
                    continue
                
                current_price = float(klines_15m[-1]["close"])
                
                # Generate candidates
                candidates = strategy.generate_candidates(
                    klines_15m, klines_1h, klines_4h,
                    "neutral", {}, 0.0, current_price
                )
                
                # Evaluate
                for cand in candidates:
                    future_klines = self._get_klines_for_timeframe(client, symbol, "15m", ts, 100)
                    evaluator = SignalOutcomeEvaluator()
                    ev = evaluator.evaluate_signal(cand.__dict__, future_klines)
                    ev["signal"] = cand.__dict__
                    evals.append(ev)
            
            return evals
        
        base_config = {}
        results = tuner.parameter_sweep(
            base_config=base_config,
            param_grid=param_grid,
            evaluations_provider=eval_provider,
            num_days=len(train_timestamps) / 96.0,
            sort_by="expected_r"
        )
        return results

    def run_walk_forward(
        self,
        strategy_family: str,
        symbol: str,
        config: Dict[str, Any],
        timestamps: List[int],
        n_windows: int = 3,
        train_ratio: float = 0.6,
        client: Any = None,
    ) -> List[Dict]:
        """Run walk-forward evaluation for a strategy configuration."""
        from src.backtest.tuner import ThresholdTuner
        
        tuner = ThresholdTuner()
        # Generate all evaluations for this config
        all_evals = []
        # ... implementation for walk-forward
        return tuner.walk_forward_evaluate(all_evals, n_windows, train_ratio)


class StrategyResearchRunner:
    """
    Main orchestrator for strategy research across all coins and strategies.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = CachedFuturesClient()
        self.symbol_registry = get_symbol_registry(config)
        self.pipeline = SignalPipeline(config=config, client=self.client)
        self.evaluator = SignalOutcomeEvaluator()
        self.tuner = ThresholdTuner()
    
    def _generate_timestamps(self, start_ts: int, end_ts: int) -> List[int]:
        """Generate 15-minute timestamps between start and end."""
        ts = start_ts
        remainder = ts % 900000
        if remainder != 0:
            ts += (900000 - remainder)
        timestamps = []
        while ts <= end_ts:
            timestamps.append(ts)
            ts += 900000
        return timestamps
    
    def _fetch_future_klines(self, symbol: str, start_ts: int, limit: int = 100) -> List[Dict]:
        """Fetch future 15m klines for evaluation."""
        with self.client.store.get_connection() as conn:
            query = """
                SELECT timestamp, open, high, low, close, volume 
                FROM klines 
                WHERE symbol=? AND interval='15m' AND timestamp > ?
                ORDER BY timestamp ASC
                LIMIT ?
            """
            rows = conn.execute(query, (symbol, start_ts, limit)).fetchall()
        formatted = []
        for r in rows:
            formatted.append({
                "timestamp": r["timestamp"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"]
            })
        return formatted
    
    def _generate_evaluations_for_config(
        self,
        config: AppConfig,
        timestamps: List[int],
        target_symbols: List[str],
    ) -> List[Dict[str, Any]]:
        """Generate evaluations for a specific config on given timestamps."""
        import logging
        for mod in ["src.pipeline", "src.engines.volume_engine", "src.engines.oi_engine", 
                    "src.engines.funding_engine", "src.engines.btc_regime", "src.engines.scoring_engine",
                    "src.engines.tp_engine", "src.data.timeframe_manager", "src.data.ohlcv_fetcher", "src.data.futures_data"]:
            logging.getLogger(mod).setLevel(logging.ERROR)
        
        from src.data.timeframe_manager import TimeframeManager
        
        def fast_fetch_multi(self, symbol: str, limit: int = 100, end_time=None):
            results = {}
            for tf in self.timeframes:
                results[tf] = self.fetcher.fetch_standardized_klines(symbol, tf, limit, end_time=end_time)
            return results
        
        TimeframeManager.fetch_multi_timeframe = fast_fetch_multi
        
        pipeline = SignalPipeline(config=config, client=self.client)
        evaluator = SignalOutcomeEvaluator()
        
        all_evals = []
        for ts in timestamps:
            signals = pipeline.run_pipeline(as_of_time=ts, symbols=target_symbols)
            for sig in signals:
                future_klines = self._fetch_future_klines(sig["symbol"], ts, limit=100)
                ev = evaluator.evaluate_signal(sig, future_klines)
                ev["signal"] = sig
                all_evals.append(ev)
        
        return all_evals
    
    def run_research(
        self,
        symbols: Optional[List[str]] = None,
        strategy_families: Optional[List[str]] = None,
        param_grids: Optional[Dict[str, Dict[str, List]]] = None,
        walk_forward_windows: int = 3,
        train_ratio: float = 0.6,
        output_dir: str = "reports",
    ) -> Dict[str, Any]:
        """
        Run complete strategy research pipeline.
        
        Args:
            symbols: List of symbols to research (default: all available)
            strategy_families: List of strategy families to test (default: all)
            param_grids: Custom parameter grids per strategy family
            walk_forward_windows: Number of walk-forward windows
            train_ratio: Train/validation split ratio
            output_dir: Directory for output reports
            
        Returns:
            Dictionary with all results, rankings, and reports
        """
        os.makedirs(output_dir, exist_ok=True)
        
        # Get available symbols
        available_symbols = self.symbol_registry.get_available_symbols()
        target_symbols = symbols or available_symbols
        
        # Filter by symbol_registry availability
        target_symbols = [s for s in target_symbols if self.symbol_registry.is_available(s)]
        
        # Get strategy families to test
        if strategy_families:
            test_families = [f for f in strategy_families if f in STRATEGY_REGISTRY._families]
        else:
            test_families = list(STRATEGY_REGISTRY._families.keys())
        
        # Get timestamps from database
        with self.client.store.get_connection() as conn:
            res = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'").fetchone()
            if not res or not res[0]:
                logger.error("No data in DB")
                return {}
            global_start, global_end = res[0], res[1]
        
        scan_end = global_end - (3 * 24 * 60 * 60 * 1000)  # 3 days buffer for evaluation
        all_timestamps = self._generate_timestamps(global_start, scan_end)
        
        # Chronological split
        n = len(all_timestamps)
        train_end = int(n * 0.6)
        val_end = int(n * 0.8)
        
        train_ts = all_timestamps[:train_end]
        val_ts = all_timestamps[train_end:val_end]
        test_ts = all_timestamps[val_end:]
        
        logger.info(f"Dataset: {len(target_symbols)} symbols, {len(all_timestamps)} timestamps")
        logger.info(f"Train: {len(train_ts)}, Val: {len(val_ts)}, Test: {len(test_ts)}")
        
        all_results = {}
        
        # Run research for each strategy family
        for family in test_families:
            logger.info(f"=== Researching {family} ===")
            
            # Get default param grid for this family
            param_grid = {}
            if param_grids and family in param_grids:
                param_grid = param_grids[family]
            else:
                # Use default param grids from strategy families
                pass
            
            # For now, run baseline with default params
            family_results = {}
            for symbol in target_symbols:
                # Get priority strategies for this symbol
                symbol_cfg = self.config.get_symbol_config(symbol)
                priority_strategies = symbol_cfg.priority_strategies
                
                # Only test this family if it's in priority list or no priority specified
                if priority_strategies and family not in priority_strategies:
                    continue
                
                # Run parameter sweep on TRAIN
                # ... (detailed implementation would go here)
                pass
            
            all_results[family] = family_results
        
        # Save all results
        output_path = os.path.join(output_dir, "strategy_research_results.json")
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        
        logger.info(f"Results saved to {output_path}")
        return all_results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Strategy Research")
    parser.add_argument("--symbol", type=str, help="Single symbol to research")
    parser.add_argument("--strategy", type=str, help="Specific strategy family")
    parser.add_argument("--all", action="store_true", help="Run all coins/strategies")
    parser.add_argument("--output-dir", default="reports", help="Output directory")
    args = parser.parse_args()

    config = AppConfig()
    runner = StrategyResearchRunner(config)
    
    if args.all:
        runner.run_research(output_dir=args.output_dir)
    elif args.symbol and args.strategy:
        runner.run_research(symbols=[args.symbol], strategy_families=[args.strategy], output_dir=args.output_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()