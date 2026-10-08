#!/usr/bin/env python
"""
Run corrected baseline with fixed TP/RR config and OI semantics.
"""
import os
import json
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any

from src.config import AppConfig, CONFIG
from src.data.cached_client import CachedFuturesClient
from src.pipeline import SignalPipeline
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.backtest.tuner import ThresholdTuner, DatasetSplit
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
    logger.info(f"Running pipeline over {len(timestamps)} timestamps for config {config.compute_hash()}...")
    
    for ts in timestamps:
        signals = pipeline.run_pipeline(as_of_time=ts, symbols=target_symbols)
        for sig in signals:
            future_klines = fetch_future_klines_for_evaluation(client, sig["symbol"], ts, limit=100)
            ev = evaluator.evaluate_signal(sig, future_klines)
            ev["signal"] = sig
            all_evals.append(ev)
    return all_evals

def save_baseline_report(evals_train, evals_val, evals_test, train_ts, total_ts, target_symbols, tuner, config_name, output_dir="reports"):
    total_days = len(total_ts) / 96.0
    
    os.makedirs(output_dir, exist_ok=True)
    md_path = os.path.join(output_dir, f"{config_name}_baseline.md")
    json_path = os.path.join(output_dir, f"{config_name}_baseline.json")
    
    with open(md_path, "w") as f:
        f.write(f"# {config_name} Baseline Report\n\n")
        f.write(f"**Period:** {total_days:.1f} days\n")
        f.write(f"**Symbols:** {len(target_symbols)}\n\n")
        
        f.write("### Metrics (TRAIN vs VAL vs TEST)\n")
        f.write("| Metric | TRAIN | VAL | TEST |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")
        
        b_t = tuner.calculate_metrics(evals_train, num_days=len(train_ts)/96.0).__dict__
        b_v = tuner.calculate_metrics(evals_val, num_days=len(evals_val)/96.0).__dict__
        b_te = tuner.calculate_metrics(evals_test, num_days=len(evals_test)/96.0).__dict__
        for k in b_t.keys():
            if isinstance(b_t[k], float):
                f.write(f"| **{k}** | {b_t[k]:.3f} | {b_v[k]:.3f} | {b_te[k]:.3f} |\n")
            else:
                f.write(f"| **{k}** | {b_t[k]} | {b_v[k]} | {b_te[k]} |\n")
        
        all_evals = evals_train + evals_val + evals_test
        
        # Signal Frequency Analysis
        f.write("\n## Phase 6: Signal Frequency Analysis\n")
        day_counts = {}
        for ts in total_ts:
            d = ts // 86400000
            day_counts[d] = 0
        for ev in all_evals:
            try:
                sig_ts_str = ev["signal"]["timestamp"]
                sig_ts = datetime.fromisoformat(sig_ts_str.replace("Z", "+00:00")).timestamp() * 1000
            except:
                sig_ts = ev.get("entry_timestamp", 0)
            d = int(sig_ts // 86400000)
            if d in day_counts:
                day_counts[d] += 1
        counts = list(day_counts.values())
        total_days_count = len(counts)
        if total_days_count > 0:
            c0 = sum(1 for c in counts if c == 0)
            c1 = sum(1 for c in counts if c == 1)
            c2 = sum(1 for c in counts if c == 2)
            c3 = sum(1 for c in counts if c == 3)
            c_more = sum(1 for c in counts if c > 3)
            f.write(f"- 0 signals/day: {c0/total_days_count*100:.1f}%\n")
            f.write(f"- 1 signal/day: {c1/total_days_count*100:.1f}%\n")
            f.write(f"- 2 signals/day: {c2/total_days_count*100:.1f}%\n")
            f.write(f"- 3 signals/day: {c3/total_days_count*100:.1f}%\n")
            f.write(f"- >3 signals/day: {c_more/total_days_count*100:.1f}%\n")
            f.write(f"\nAverage: {len(all_evals)/total_days_count:.2f} signals/day\n")
        
        # Quality Stratification
        f.write("\n## Phase 7: Quality Stratification (All Data)\n")
        def write_strat(name, key_fn):
            f.write(f"### By {name}\n")
            f.write("| Bucket | Count | Win Rate | Exp R | Max DD |\n")
            f.write("| :--- | :--- | :--- | :--- | :--- |\n")
            buckets = {}
            for ev in all_evals:
                k = key_fn(ev)
                if k not in buckets: buckets[k] = []
                buckets[k].append(ev)
            for k, ev_list in sorted(buckets.items()):
                m = tuner.calculate_metrics(ev_list, 1.0)
                f.write(f"| {k} | {len(ev_list)} | {m.win_rate:.2f} | {m.expected_r:.2f} | {m.max_drawdown_r:.2f} |\n")
        
        write_strat("Symbol", lambda e: e["signal"].get("symbol", "unknown"))
        write_strat("Side", lambda e: e["signal"].get("side", "unknown"))
        write_strat("BTC Regime", lambda e: e["signal"].get("btc_regime", "unknown"))
        
        def conf_bucket(e):
            c = e["signal"].get("confidence", 0)
            if c < 60: return "<60"
            if c < 70: return "60-69"
            if c < 80: return "70-79"
            if c < 90: return "80-89"
            return "90-100"
        write_strat("Confidence", conf_bucket)
        
        def rr_bucket(e):
            rr = e["signal"].get("risk_reward_tp1", 1.0)
            if rr < 1.5: return "<1.5"
            if rr < 2.0: return "1.5-2.0"
            if rr < 2.5: return "2.0-2.5"
            if rr < 3.0: return "2.5-3.0"
            return "3.0+"
        write_strat("R:R to TP1", rr_bucket)
        
        # Best/Worst
        f.write("\n## Top 10 Best & Worst Trades\n")
        sorted_evals = sorted(all_evals, key=lambda x: x.get("net_realized_r", 0))
        f.write("\n### Worst 10\n")
        for ev in sorted_evals[:10]:
            f.write(f"#### {ev['signal'].get('symbol')} {ev['signal'].get('side')} : {ev.get('net_realized_r', 0):.2f}R (Hold: {ev.get('holding_bars')})\n")
            f.write("```json\n")
            f.write(json.dumps(ev['signal'], indent=2) + "\n")
            f.write("```\n")
        f.write("\n### Best 10\n")
        for ev in reversed(sorted_evals[-10:]):
            f.write(f"#### {ev['signal'].get('symbol')} {ev['signal'].get('side')} : {ev.get('net_realized_r', 0):.2f}R (Hold: {ev.get('holding_bars')})\n")
            f.write("```json\n")
            f.write(json.dumps(ev['signal'], indent=2) + "\n")
            f.write("```\n")
    
    # Save JSON
    all_baseline_evals = evals_train + evals_val + evals_test
    with open(json_path, "w") as f:
        json.dump(all_baseline_evals, f, indent=2)
    
    logger.info(f"Saved {config_name} baseline to {md_path} and {json_path}")

def main():
    parser = argparse.ArgumentParser(description="Run Corrected Baseline")
    parser.add_argument("--config-name", default="calibration_round1", help="Config name for output files")
    parser.add_argument("--oi-mode", choices=["required", "optional"], default="optional", help="OI mode")
    parser.add_argument("--max-symbols", type=int, default=None, help="Max symbols")
    args = parser.parse_args()

    os.makedirs("reports", exist_ok=True)
    client = CachedFuturesClient()
    tuner = ThresholdTuner()

    # Build config with specified OI mode
    config = AppConfig()
    config.oi.mode = args.oi_mode
    config.validate()  # This will validate TP/RR consistency
    
    logger.info(f"Config: {config.compute_hash()}, OI mode: {args.oi_mode}")
    logger.info(f"TP ladder: {config.tp.r_multiples}, min_rr_tp1: {config.risk.min_rr_tp1}")

    with client.store.get_connection() as conn:
        res = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'").fetchone()
        if not res or not res[0]:
            logger.error("No data in DB. Run download_history.py first.")
            return
        global_start, global_end = res[0], res[1]
        sym_res = conn.execute("SELECT DISTINCT symbol FROM klines").fetchall()
        all_symbols = [r[0] for r in sym_res]
    
    target_symbols = all_symbols[:args.max_symbols] if args.max_symbols else all_symbols
    scan_end = global_end - (3 * 24 * 60 * 60 * 1000)
    all_timestamps = generate_timestamps(global_start, scan_end)
    total_days = (scan_end - global_start) / (1000 * 60 * 60 * 24)
    logger.info(f"Dataset: {len(target_symbols)} symbols, {total_days:.1f} days, {len(all_timestamps)} timestamps.")

    # Chronological Split (60% Train, 20% Val, 20% Test)
    n = len(all_timestamps)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    train_ts = all_timestamps[:train_end]
    val_ts = all_timestamps[train_end:val_end]
    test_ts = all_timestamps[val_end:]
    train_days = len(train_ts) / 96.0
    val_days = len(val_ts) / 96.0
    test_days = len(test_ts) / 96.0

    logger.info("=== Corrected Baseline Run ===")
    baseline_evals_train = generate_evaluations_for_config(config, train_ts, target_symbols, client)
    baseline_evals_val = generate_evaluations_for_config(config, val_ts, target_symbols, client)
    baseline_evals_test = generate_evaluations_for_config(config, test_ts, target_symbols, client)

    save_baseline_report(baseline_evals_train, baseline_evals_val, baseline_evals_test, 
                         train_ts, all_timestamps, target_symbols, tuner, args.config_name)

    logger.info("=== Baseline Complete ===")

if __name__ == "__main__":
    main()