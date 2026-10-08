#!/usr/bin/env python
"""
Confidence Threshold Experiment - One variable at a time on TRAIN partition.
"""
import os
import json
import copy
import argparse
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

def main():
    parser = argparse.ArgumentParser(description="Confidence Threshold Experiment (TRAIN)")
    parser.add_argument("--max-symbols", type=int, default=10, help="Max symbols")
    parser.add_argument("--values", nargs="+", type=int, default=[60, 65, 70, 75, 80, 85], help="Confidence threshold values")
    parser.add_argument("--output-dir", default="reports", help="Output directory")
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
    
    target_symbols = all_symbols[:args.max_symbols]
    scan_end = global_end - (3 * 24 * 60 * 60 * 1000)
    all_timestamps = generate_timestamps(global_start, scan_end)
    
    n = len(all_timestamps)
    train_end = int(n * 0.6)
    train_ts = all_timestamps[:train_end]
    
    logger.info(f"Train: {len(train_ts)} timestamps, {len(target_symbols)} symbols")
    logger.info(f"Testing confidence thresholds: {args.values}")

    results = []
    for val in args.values:
        cfg = copy.deepcopy(base_config)
        cfg.signals.min_confidence = val
        cfg.validate()
        
        logger.info(f"Testing signals.min_confidence={val} (config: {cfg.compute_hash()})")
        evals = generate_evaluations_for_config(cfg, train_ts, target_symbols, client)
        metrics = tuner.calculate_metrics(evals, num_days=len(train_ts)/96.0)
        
        result = {
            "param_value": val,
            "config_hash": cfg.compute_hash(),
            **metrics.__dict__
        }
        results.append(result)
        logger.info(f"  signals={metrics.signals_total}, signals/day={metrics.signals_per_day:.1f}, "
                   f"entry_rate={metrics.entry_rate:.3f}, tp1_rate={metrics.tp1_hit_rate:.3f}, "
                   f"sl_rate={metrics.sl_rate:.3f}, win_rate={metrics.win_rate:.3f}, "
                   f"avg_r={metrics.average_r:.3f}, exp_r={metrics.expected_r:.3f}, "
                   f"pf={metrics.profit_factor:.3f}, max_dd={metrics.max_drawdown_r:.1f}")
    
    # Save markdown report
    md_path = os.path.join(args.output_dir, "calibration_confidence.md")
    with open(md_path, "w") as f:
        f.write("# Confidence Threshold Experiment (TRAIN Partition)\n\n")
        f.write("| Param Value | Signals | Signals/Day | Entry Rate | TP1 Rate | TP2 Rate | TP3 Rate | SL Rate | Win Rate | Avg R | Exp R | Profit Factor | Max DD |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in results:
            f.write(f"| {r['param_value']} | {r['signals_total']} | {r['signals_per_day']:.1f} | "
                   f"{r['entry_rate']:.3f} | {r['tp1_hit_rate']:.3f} | {r['tp2_hit_rate']:.3f} | "
                   f"{r['tp3_hit_rate']:.3f} | {r['sl_rate']:.3f} | {r['win_rate']:.3f} | "
                   f"{r['average_r']:.3f} | {r['expected_r']:.3f} | {r['profit_factor']:.3f} | "
                   f"{r['max_drawdown_r']:.1f} |\n")
    
    # Save JSON
    json_path = os.path.join(args.output_dir, "calibration_confidence.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    
    # Stability analysis
    from scripts.analyze_stability import analyze_stability
    stability = analyze_stability(results, "param_value", "expected_r")
    stability_path = os.path.join(args.output_dir, "calibration_confidence_stability.json")
    with open(stability_path, "w") as f:
        json.dump(stability, f, indent=2)
    
    logger.info(f"Saved to {md_path}, {json_path}, {stability_path}")

if __name__ == "__main__":
    main()