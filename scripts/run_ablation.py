#!/usr/bin/env python
"""
Ablation Experiment Framework - Tests incremental value of each filter component.
"""
import os
import json
import copy
import argparse
from datetime import datetime
from typing import List, Dict, Any

from src.config import AppConfig
from src.data.cached_client import CachedFuturesClient
from src.pipeline import SignalPipeline
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.backtest.tuner import ThresholdTuner
from src.utils.logger import get_logger

logger = get_logger(__name__)

def generate_timestamps(start_ts: int, end_ts: int, interval_ms: int = 900000) -> List[int]:
    ts = start_ts
    remainder = ts % interval_ms
    if remainder != 0:
        ts += (interval_ms - remainder)
    timestamps = []
    while ts <= end_ts:
        timestamps.append(ts)
        ts += interval_ms
    return timestamps

def fetch_future_klines_for_evaluation(client: CachedFuturesClient, symbol: str, start_ts: int, limit: int = 100) -> List[Dict]:
    with client.store.get_connection() as conn:
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

def generate_evaluations_for_config(
    config: AppConfig, 
    timestamps: List[int], 
    target_symbols: List[str],
    client: CachedFuturesClient
) -> List[Dict[str, Any]]:
    import logging
    for mod in ["src.pipeline", "src.engines.volume_engine", "src.engines.oi_engine", 
                "src.engines.funding_engine", "src.engines.btc_regime", "src.engines.scoring_engine",
                "src.engines.tp_engine",
                "src.data.timeframe_manager", "src.data.ohlcv_fetcher", "src.data.futures_data"]:
        logging.getLogger(mod).setLevel(logging.ERROR)
    
    from src.data.timeframe_manager import TimeframeManager
    
    def fast_fetch_multi(self, symbol: str, limit: int = 100, end_time=None):
        results = {}
        for tf in self.timeframes:
            results[tf] = self.fetcher.fetch_standardized_klines(symbol, tf, limit, end_time=end_time)
        return results
        
    TimeframeManager.fetch_multi_timeframe = fast_fetch_multi
    
    pipeline = SignalPipeline(config=config, client=client)
    evaluator = SignalOutcomeEvaluator()
    
    all_evals = []
    for ts in timestamps:
        signals = pipeline.run_pipeline(as_of_time=ts, symbols=target_symbols)
        for sig in signals:
            future_klines = fetch_future_klines_for_evaluation(client, sig["symbol"], ts, limit=100)
            ev = evaluator.evaluate_signal(sig, future_klines)
            ev["signal"] = sig
            all_evals.append(ev)
    return all_evals

# Ablation variant definitions
# Each variant specifies which filters are ENABLED
ABLATION_VARIANTS = {
    "A0_minimal": {
        "description": "Minimal core: only entry/SL/TP + risk + min_confidence",
        "btc_regime": False,
        "volume": False,
        "oi": False,
        "funding": False,
    },
    "A1_btc_regime": {
        "description": "A0 + BTC regime filter",
        "btc_regime": True,
        "volume": False,
        "oi": False,
        "funding": False,
    },
    "A2_volume": {
        "description": "A0 + Volume filter",
        "btc_regime": False,
        "volume": True,
        "oi": False,
        "funding": False,
    },
    "A3_oi": {
        "description": "A0 + OI filter",
        "btc_regime": False,
        "volume": False,
        "oi": True,
        "funding": False,
    },
    "A4_funding": {
        "description": "A0 + Funding filter",
        "btc_regime": False,
        "volume": False,
        "oi": False,
        "funding": True,
    },
    "A5_btc_volume": {
        "description": "A0 + BTC regime + Volume",
        "btc_regime": True,
        "volume": True,
        "oi": False,
        "funding": False,
    },
    "A6_btc_oi": {
        "description": "A0 + BTC regime + OI",
        "btc_regime": True,
        "volume": False,
        "oi": True,
        "funding": False,
    },
    "A7_volume_oi": {
        "description": "A0 + Volume + OI",
        "btc_regime": False,
        "volume": True,
        "oi": True,
        "funding": False,
    },
    "A8_btc_volume_oi": {
        "description": "A0 + BTC regime + Volume + OI",
        "btc_regime": True,
        "volume": True,
        "oi": True,
        "funding": False,
    },
    "A9_btc_volume_funding": {
        "description": "A0 + BTC regime + Volume + Funding",
        "btc_regime": True,
        "volume": True,
        "oi": False,
        "funding": True,
    },
    "A10_full": {
        "description": "Full current filter stack",
        "btc_regime": True,
        "volume": True,
        "oi": True,
        "funding": True,
    },
}

def build_ablation_config(base_config: AppConfig, variant: Dict) -> AppConfig:
    """Build config for a specific ablation variant."""
    cfg = copy.deepcopy(base_config)
    
    # Disable filters by setting thresholds to extreme values that pass everything
    if not variant["btc_regime"]:
        # Disable BTC regime by allowing all directions regardless of regime
        # We can't easily disable it in pipeline, so we set a flag
        pass  # Handled in pipeline via monkey-patch or config
    
    if not variant["volume"]:
        cfg.volume.min_ratio = 0.0  # Pass all volume
    
    if not variant["oi"]:
        cfg.oi.mode = "disabled"  # Special mode to skip OI
        cfg.oi.min_change_pct = 0.0
    
    if not variant["funding"]:
        cfg.funding.extreme_positive_threshold = 100.0  # Never trigger
        cfg.funding.extreme_negative_threshold = -100.0
        cfg.funding.confidence_penalty = 0
    
    # We need to pass variant info to pipeline - use a custom attribute
    cfg._ablation_variant = variant
    
    return cfg

def run_ablation_experiment(
    variant_name: str,
    variant_config: Dict,
    base_config: AppConfig,
    train_ts: List[int],
    target_symbols: List[str],
    client: CachedFuturesClient,
    tuner: ThresholdTuner,
    output_dir: str = "reports"
) -> Dict[str, Any]:
    """Run a single ablation variant and return metrics."""
    logger.info(f"Running ablation: {variant_name} - {variant_config['description']}")
    
    cfg = build_ablation_config(base_config, variant_config)
    cfg.validate()
    
    evals = generate_evaluations_for_config(cfg, train_ts, target_symbols, client)
    metrics = tuner.calculate_metrics(evals, num_days=len(train_ts)/96.0)
    
    result = {
        "variant": variant_name,
        "description": variant_config["description"],
        "config_hash": cfg.compute_hash(),
        "filters_enabled": {
            "btc_regime": variant_config["btc_regime"],
            "volume": variant_config["volume"],
            "oi": variant_config["oi"],
            "funding": variant_config["funding"],
        },
        **metrics.__dict__
    }
    
    logger.info(f"  signals={metrics.signals_total}, signals/day={metrics.signals_per_day:.1f}, "
               f"entry_rate={metrics.entry_rate:.3f}, tp1_rate={metrics.tp1_hit_rate:.3f}, "
               f"sl_rate={metrics.sl_rate:.3f}, win_rate={metrics.win_rate:.3f}, "
               f"avg_r={metrics.average_r:.3f}, exp_r={metrics.expected_r:.3f}, "
               f"pf={metrics.profit_factor:.3f}, max_dd={metrics.max_drawdown_r:.1f}")
    
    return result

def main():
    parser = argparse.ArgumentParser(description="Run Ablation Experiments")
    parser.add_argument("--output-dir", default="reports", help="Output directory")
    parser.add_argument("--variant", help="Run specific variant only")
    parser.add_argument("--list", action="store_true", help="List available variants")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    client = CachedFuturesClient()
    tuner = ThresholdTuner()

    base_config = AppConfig()
    base_config.oi.mode = "optional"
    base_config.validate()

    with client.store.get_connection() as conn:
        res = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'").fetchone()
        if not res or not res[0]:
            logger.error("No data in DB.")
            return
        global_start, global_end = res[0], res[1]
        sym_res = conn.execute("SELECT DISTINCT symbol FROM klines").fetchall()
        all_symbols = [r[0] for r in sym_res]
    
    target_symbols = all_symbols[:10]
    scan_end = global_end - (3 * 24 * 60 * 60 * 1000)
    all_timestamps = generate_timestamps(global_start, scan_end)
    
    n = len(all_timestamps)
    train_end = int(n * 0.6)
    train_ts = all_timestamps[:train_end]
    
    logger.info(f"Train: {len(train_ts)} timestamps, {len(target_symbols)} symbols")

    if args.list:
        print("Available ablation variants:")
        for name, config in ABLATION_VARIANTS.items():
            filters = [k for k, v in config.items() if k != "description" and v]
            print(f"  {name}: {config['description']} (filters: {', '.join(filters) if filters else 'none'})")
        return

    variants_to_run = {args.variant: ABLATION_VARIANTS[args.variant]} if args.variant else ABLATION_VARIANTS
    
    all_results = []
    for name, config in variants_to_run.items():
        result = run_ablation_experiment(
            name, config, base_config, train_ts, target_symbols, client, tuner, args.output_dir
        )
        all_results.append(result)
    
    # Save results
    md_path = os.path.join(args.output_dir, "ablation.md")
    with open(md_path, "w") as f:
        f.write("# Ablation Study (TRAIN Partition)\n\n")
        f.write("| Variant | Description | Signals | Signals/Day | Entry Rate | TP1 Rate | SL Rate | Win Rate | Avg R | Exp R | Profit Factor | Max DD |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in all_results:
            f.write(f"| {r['variant']} | {r['description']} | {r['signals_total']} | "
                   f"{r['signals_per_day']:.1f} | {r['entry_rate']:.3f} | {r['tp1_hit_rate']:.3f} | "
                   f"{r['sl_rate']:.3f} | {r['win_rate']:.3f} | {r['average_r']:.3f} | "
                   f"{r['expected_r']:.3f} | {r['profit_factor']:.3f} | {r['max_drawdown_r']:.1f} |\n")
    
    json_path = os.path.join(args.output_dir, "ablation.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    
    logger.info(f"Saved ablation results to {md_path} and {json_path}")

if __name__ == "__main__":
    main()