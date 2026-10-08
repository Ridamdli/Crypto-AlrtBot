#!/usr/bin/env python
"""
Controlled Parameter Experiments - One variable at a time on TRAIN partition.
"""
import os
import json
import copy
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

def run_experiment(experiment_name: str, param_values: List, param_setter, base_config: AppConfig, 
                   train_ts: List[int], target_symbols: List[str], client: CachedFuturesClient,
                   tuner: ThresholdTuner, output_dir: str = "reports"):
    """Run a one-variable experiment and save results."""
    results = []
    
    for val in param_values:
        cfg = copy.deepcopy(base_config)
        param_setter(cfg, val)
        cfg.validate()
        
        logger.info(f"{experiment_name}: Testing {val} (config: {cfg.compute_hash()})")
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
    os.makedirs(output_dir, exist_ok=True)
    md_path = os.path.join(output_dir, f"{experiment_name.lower().replace(' ', '_')}.md")
    with open(md_path, "w") as f:
        f.write(f"# {experiment_name} (TRAIN Partition)\n\n")
        f.write("| Param Value | Signals | Signals/Day | Entry Rate | TP1 Rate | TP2 Rate | TP3 Rate | SL Rate | Win Rate | Avg R | Exp R | Profit Factor | Max DD |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in results:
            f.write(f"| {r['param_value']} | {r['signals_total']} | {r['signals_per_day']:.1f} | "
                   f"{r['entry_rate']:.3f} | {r['tp1_hit_rate']:.3f} | {r['tp2_hit_rate']:.3f} | "
                   f"{r['tp3_hit_rate']:.3f} | {r['sl_rate']:.3f} | {r['win_rate']:.3f} | "
                   f"{r['average_r']:.3f} | {r['expected_r']:.3f} | {r['profit_factor']:.3f} | "
                   f"{r['max_drawdown_r']:.1f} |\n")
    
    # Save JSON
    json_path = os.path.join(output_dir, f"{experiment_name.lower().replace(' ', '_')}.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Saved {experiment_name} to {md_path} and {json_path}")
    return results

def main():
    os.makedirs("reports", exist_ok=True)
    client = CachedFuturesClient()
    tuner = ThresholdTuner()

    # Base config
    base_config = AppConfig()
    base_config.oi.mode = "optional"
    base_config.validate()

    # Get dataset
    with client.store.get_connection() as conn:
        res = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'").fetchone()
        if not res or not res[0]:
            logger.error("No data in DB.")
            return
        global_start, global_end = res[0], res[1]
        sym_res = conn.execute("SELECT DISTINCT symbol FROM klines").fetchall()
        all_symbols = [r[0] for r in sym_res]
    
    target_symbols = all_symbols[:10]  # 10 symbols
    scan_end = global_end - (3 * 24 * 60 * 60 * 1000)
    all_timestamps = generate_timestamps(global_start, scan_end)
    
    # Train split only
    n = len(all_timestamps)
    train_end = int(n * 0.6)
    train_ts = all_timestamps[:train_end]
    
    logger.info(f"Train: {len(train_ts)} timestamps, {len(target_symbols)} symbols")

    # ============================================================
    # Experiment A: Volume Threshold
    # ============================================================
    logger.info("=== Experiment A: Volume Threshold ===")
    run_experiment(
        "Volume_Threshold",
        [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        lambda cfg, v: setattr(cfg.volume, 'min_ratio', v),
        base_config, train_ts, target_symbols, client, tuner
    )

    # ============================================================
    # Experiment B: Confidence Threshold
    # ============================================================
    logger.info("=== Experiment B: Confidence Threshold ===")
    run_experiment(
        "Confidence_Threshold",
        [60, 65, 70, 75, 80, 85],
        lambda cfg, v: setattr(cfg.signals, 'min_confidence', v),
        base_config, train_ts, target_symbols, client, tuner
    )

    # ============================================================
    # Experiment C: Structural SL Lookback
    # ============================================================
    logger.info("=== Experiment C: Structural SL Lookback ===")
    # Need to modify SL engine lookback - we'll need to pass it through config
    # For now, let's test by modifying the pipeline directly
    # Actually, let's add a config for SL lookback
    run_experiment(
        "SL_Lookback",
        [20, 30, 40, 50],
        lambda cfg, v: setattr(cfg.risk, 'sl_lookback', v) if hasattr(cfg.risk, 'sl_lookback') else None,
        base_config, train_ts, target_symbols, client, tuner
    )

    # ============================================================
    # Experiment D: TP/RR (with consistent ladders)
    # ============================================================
    logger.info("=== Experiment D: TP/RR Ladder ===")
    tp_ladders = [
        (2.0, 3.0, 4.0, 2.0),
        (2.0, 2.5, 3.5, 2.0),
        (2.0, 3.0, 5.0, 2.0),
        (2.5, 3.5, 4.5, 2.5),
        (3.0, 4.0, 5.0, 3.0),
    ]
    
    tp_results = []
    for tp1, tp2, tp3, min_rr in tp_ladders:
        cfg = copy.deepcopy(base_config)
        cfg.tp.r_multiples = (tp1, tp2, tp3)
        cfg.risk.min_rr_tp1 = min_rr
        cfg.validate()
        
        label = f"TP({tp1},{tp2},{tp3})_minRR{min_rr}"
        logger.info(f"TP/RR: Testing {label}")
        evals = generate_evaluations_for_config(cfg, train_ts, target_symbols, client)
        metrics = tuner.calculate_metrics(evals, num_days=len(train_ts)/96.0)
        
        result = {
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "min_rr": min_rr,
            "config_hash": cfg.compute_hash(),
            **metrics.__dict__
        }
        tp_results.append(result)
    
    # Save TP/RR results
    md_path = os.path.join("reports", "tp_rr_experiment.md")
    with open(md_path, "w") as f:
        f.write("# TP/RR Ladder Experiment (TRAIN Partition)\n\n")
        f.write("| TP1 | TP2 | TP3 | min_rr_tp1 | Signals | Signals/Day | Entry Rate | TP1 Rate | SL Rate | Win Rate | Avg R | Exp R | Profit Factor | Max DD |\n")
        f.write("| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in tp_results:
            f.write(f"| {r['tp1']} | {r['tp2']} | {r['tp3']} | {r['min_rr']} | "
                   f"{r['signals_total']} | {r['signals_per_day']:.1f} | {r['entry_rate']:.3f} | "
                   f"{r['tp1_hit_rate']:.3f} | {r['sl_rate']:.3f} | {r['win_rate']:.3f} | "
                   f"{r['average_r']:.3f} | {r['expected_r']:.3f} | {r['profit_factor']:.3f} | "
                   f"{r['max_drawdown_r']:.1f} |\n")
    
    json_path = os.path.join("reports", "tp_rr_experiment.json")
    with open(json_path, "w") as f:
        json.dump(tp_results, f, indent=2)
    
    logger.info("All experiments complete!")

if __name__ == "__main__":
    main()