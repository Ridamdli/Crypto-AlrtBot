import os
import json
import time
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
    # Round up to next interval boundary
    remainder = ts % interval_ms
    if remainder != 0:
        ts += (interval_ms - remainder)
        
    timestamps = []
    while ts <= end_ts:
        timestamps.append(ts)
        ts += interval_ms
    return timestamps

def fetch_future_klines_for_evaluation(client: CachedFuturesClient, symbol: str, start_ts: int, limit: int = 100) -> List[Dict]:
    # We need klines AFTER the signal timestamp to evaluate outcomes.
    # We can query the SQLite DB directly.
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
    """Runs the pipeline over all timestamps and evaluates the generated signals."""
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
        # Run pipeline
        signals = pipeline.run_pipeline(as_of_time=ts, symbols=target_symbols)
        
        # Evaluate each signal
        for sig in signals:
            future_klines = fetch_future_klines_for_evaluation(client, sig["symbol"], ts, limit=100)
            ev = evaluator.evaluate_signal(sig, future_klines)
            ev["signal"] = sig
            all_evals.append(ev)
            
    return all_evals

def generate_baseline_report(evals_train, evals_val, evals_test, train_ts, total_ts, target_symbols, tuner):
    total_days = len(total_ts) / 96.0
    
    with open("reports/baseline.md", "w") as f:
        f.write("# Baseline Performance Report\n\n")
        f.write(f"**Period:** {total_days:.1f} days\n")
        f.write(f"**Symbols:** {len(target_symbols)}\n\n")
        
        # Phase 5: Metrics Table
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
        
        # Phase 6: Frequency Analysis
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

        # Phase 7: Baseline Quality Analysis
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
            if c < 75: return "60-74"
            if c < 85: return "75-84"
            return "85-100"
        write_strat("Confidence", conf_bucket)
        
        def rr_bucket(e):
            rr = e["signal"].get("risk_reward_tp1", 1.0)
            if rr < 1.0: return "<1.0"
            if rr < 1.5: return "1.0-1.5"
            if rr < 2.0: return "1.5-2.0"
            return ">2.0"
        write_strat("R:R to TP1", rr_bucket)

        # Best / Worst 
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

def main():
    parser = argparse.ArgumentParser(description="Run Research Pipeline")
    parser.add_argument("--quick", action="store_true", help="Run quick baseline with fewer symbols/timestamps")
    parser.add_argument("--scan-interval", type=str, default="15m", choices=["15m", "1h", "4h"], help="Scan interval")
    parser.add_argument("--max-symbols", type=int, default=None, help="Maximum number of symbols to scan")
    args = parser.parse_args()

    os.makedirs("reports", exist_ok=True)
    client = CachedFuturesClient()
    tuner = ThresholdTuner()

    # 1. Determine time range from DB
    with client.store.get_connection() as conn:
        res = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'").fetchone()
        if not res or not res[0]:
            logger.error("No data in DB. Run download_history.py first.")
            return
        global_start, global_end = res[0], res[1]
        
        sym_res = conn.execute("SELECT DISTINCT symbol FROM klines").fetchall()
        all_symbols = [r[0] for r in sym_res]
        
    # Limit symbols if specified
    target_symbols = all_symbols[:args.max_symbols] if args.max_symbols else all_symbols
    
    # Scan interval
    interval_map = {"15m": 900000, "1h": 3600000, "4h": 14400000}
    scan_interval_ms = interval_map[args.scan_interval]
    
    # We need to leave some room at the end for the evaluator to have future klines.
    # Subtract 3 days from the end of the scan period.
    scan_end = global_end - (3 * 24 * 60 * 60 * 1000)
    all_timestamps = generate_timestamps(global_start, scan_end, scan_interval_ms)
    
    total_days = (scan_end - global_start) / (1000 * 60 * 60 * 24)
    logger.info(f"Dataset: {len(target_symbols)} symbols, {total_days:.1f} days, {len(all_timestamps)} timestamps (interval: {args.scan_interval}).")
    
    if args.quick:
        # For quick mode, sample timestamps (e.g., every 4 hours)
        quick_interval_ms = 4 * 3600000  # 4 hours
        all_timestamps = generate_timestamps(global_start, scan_end, quick_interval_ms)
        logger.info(f"Quick mode: {len(all_timestamps)} timestamps (4h interval)")

    # 2. Chronological Split (60% Train, 20% Val, 20% Test)
    n = len(all_timestamps)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    
    train_ts = all_timestamps[:train_end]
    val_ts = all_timestamps[train_end:val_end]
    test_ts = all_timestamps[val_end:]
    
    train_days = len(train_ts) / 96.0
    val_days = len(val_ts) / 96.0
    test_days = len(test_ts) / 96.0

    # =========================================================================
    # Phase 2: Baseline Run & Phase 4: Outcome Evaluation
    # =========================================================================
    logger.info("=== Phase 2 & 4: Baseline Run ===")
    baseline_config = CONFIG
    
    baseline_evals_train = generate_evaluations_for_config(baseline_config, train_ts, target_symbols, client)
    baseline_evals_val = generate_evaluations_for_config(baseline_config, val_ts, target_symbols, client)
    baseline_evals_test = generate_evaluations_for_config(baseline_config, test_ts, target_symbols, client)

    # Save Baseline JSON
    all_baseline_evals = baseline_evals_train + baseline_evals_val + baseline_evals_test
    with open("reports/baseline.json", "w") as f:
        json.dump(all_baseline_evals, f, indent=2)

    # Generate Baseline Report
    generate_baseline_report(baseline_evals_train, baseline_evals_val, baseline_evals_test, train_ts, all_timestamps, target_symbols, tuner)

    # =========================================================================
    # Phase 8: Parameter Tuning (TRAIN ONLY)
    # =========================================================================
    logger.info("=== Phase 8: Parameter Tuning ===")
    param_grid = {
        "volume.min_ratio": [1.5, 2.0],
        "oi.min_change_pct": [1.5, 2.0, 2.5],
        "risk.min_rr_tp1": [1.5, 2.0]
    }
    
    def eval_provider_train(cfg):
        return generate_evaluations_for_config(cfg, train_ts, target_symbols, client)
        
    sweep_results = tuner.parameter_sweep(
        base_config=baseline_config,
        param_grid=param_grid,
        evaluations_provider=eval_provider_train,
        num_days=train_days,
        sort_by="expected_r"
    )
    
    # Custom Robustness Ranking
    def robustness_score(res):
        m = res
        exp_r = m["expected_r"]
        dd = m["max_drawdown_r"] if m["max_drawdown_r"] > 0 else 1.0
        pf = m["profit_factor"]
        # Penalty for too few trades
        if m["signals_total"] < 10:
            return 0
        return (exp_r * pf) / dd
        
    sweep_results.sort(key=robustness_score, reverse=True)
    
    with open("reports/tuning_train.md", "w") as f:
        f.write(tuner.sweep_report_markdown(sweep_results, title="Tuning Sweep (TRAIN Partition)"))

    # =========================================================================
    # Phase 9: Validation Set
    # =========================================================================
    logger.info("=== Phase 9: Validation ===")
    top_configs = sweep_results[:3]
    val_reports = []
    
    for i, res in enumerate(top_configs):
        import copy
        cfg = copy.deepcopy(baseline_config)
        for k, v in res["params"].items():
            section, field = k.split(".")
            setattr(getattr(cfg, section), field, v)
            
        val_evals = generate_evaluations_for_config(cfg, val_ts, target_symbols, client)
        val_m = tuner.calculate_metrics(val_evals, num_days=val_days)
        val_reports.append((res["param_label"], val_m))
        
    with open("reports/validation.md", "w") as f:
        f.write("# Validation Report (Top 3 TRAIN Configs)\n\n")
        for label, m in val_reports:
            f.write(f"### Config: {label}\n")
            f.write(f"- Win Rate: {m.win_rate}\n")
            f.write(f"- Expected R: {m.expected_r}\n")
            f.write(f"- Profit Factor: {m.profit_factor}\n")
            f.write(f"- Max Drawdown: {m.max_drawdown_r}\n\n")

    # =========================================================================
    # Phase 10: Final Unseen TEST
    # =========================================================================
    logger.info("=== Phase 10: Final Unseen Test ===")
    best_config_params = top_configs[0]["params"]
    final_cfg = copy.deepcopy(baseline_config)
    for k, v in best_config_params.items():
        section, field = k.split(".")
        setattr(getattr(final_cfg, section), field, v)
        
    test_evals = generate_evaluations_for_config(final_cfg, test_ts, target_symbols, client)
    test_m = tuner.calculate_metrics(test_evals, num_days=test_days)
    
    with open("reports/final_test.md", "w") as f:
        f.write("# Final Unseen Test Report\n\n")
        f.write(f"**Selected Config:** {top_configs[0]['param_label']}\n\n")
        m_dict = test_m.__dict__
        f.write("| Metric | TEST Result |\n")
        f.write("| :--- | :--- |\n")
        for k, v in m_dict.items():
            f.write(f"| {k} | {v} |\n")

    # =========================================================================
    # Phase 11: Walk-Forward Evaluation
    # =========================================================================
    logger.info("=== Phase 11: Walk-Forward Evaluation ===")
    # Re-evaluate all timestamps with final config
    all_final_evals = generate_evaluations_for_config(final_cfg, all_timestamps, target_symbols, client)
    wf_results = tuner.walk_forward_evaluate(
        all_final_evals, 
        n_windows=3, 
        train_ratio=0.6, 
        num_days_per_window=(total_days / 3.0)
    )
    
    with open("reports/walk_forward.md", "w") as f:
        f.write("# Walk-Forward Evaluation\n\n")
        for wf in wf_results:
            f.write(f"### Window {wf['window']}\n")
            f.write(f"- Train N: {wf['train_n']}, Test N: {wf['test_n']}\n")
            f.write(f"- Train Exp R: {wf['train_metrics']['expected_r']}, Test Exp R: {wf['test_metrics']['expected_r']}\n")
            f.write(f"- Train WinRate: {wf['train_metrics']['win_rate']}, Test WinRate: {wf['test_metrics']['win_rate']}\n\n")

    # =========================================================================
    # Phase 12: Robustness Checks
    # =========================================================================
    logger.info("=== Phase 12: Robustness Checks ===")
    robustness_reports = []
    
    perturbations = [
        ("volume.min_ratio", getattr(final_cfg.volume, "min_ratio") * 1.05, "+5%"),
        ("volume.min_ratio", getattr(final_cfg.volume, "min_ratio") * 0.95, "-5%"),
        ("oi.min_change_pct", getattr(final_cfg.oi, "min_change_pct") * 1.05, "+5%"),
        ("oi.min_change_pct", getattr(final_cfg.oi, "min_change_pct") * 0.95, "-5%"),
        ("signals.min_confidence", getattr(final_cfg.signals, "min_confidence") + 5, "+5 points"),
        ("signals.min_confidence", getattr(final_cfg.signals, "min_confidence") - 5, "-5 points"),
    ]
    
    for field, new_val, label in perturbations:
        import copy
        cfg_p = copy.deepcopy(final_cfg)
        section, key = field.split(".")
        setattr(getattr(cfg_p, section), key, new_val)
        
        p_evals = generate_evaluations_for_config(cfg_p, test_ts, target_symbols, client)
        p_m = tuner.calculate_metrics(p_evals, num_days=test_days)
        robustness_reports.append((f"{field} {label} ({new_val:.2f})", p_m))
        
    with open("reports/robustness.md", "w") as f:
        f.write("# Robustness Checks (TEST Partition)\n\n")
        f.write("## Parameter Perturbations\n")
        f.write("| Perturbation | Win Rate | Exp R | Max DD | Signals |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- |\n")
        f.write(f"| **BASELINE TEST** | {test_m.win_rate:.2f} | {test_m.expected_r:.2f} | {test_m.max_drawdown_r:.2f} | {test_m.signals_total} |\n")
        for label, m in robustness_reports:
            f.write(f"| {label} | {m.win_rate:.2f} | {m.expected_r:.2f} | {m.max_drawdown_r:.2f} | {m.signals_total} |\n")
            
        f.write("\n## Execution Friction Perturbations\n")
        f.write("| Friction Scenario | Win Rate | Exp R | Max DD |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")
        
        # Test higher fees and slippage
        scenarios = [
            ("2x Taker Fee (0.08%)", 0.0008, 0.0002),
            ("High Slippage (0.1%)", 0.0004, 0.0010),
        ]
        for name, fee, slip in scenarios:
            ev_engine = SignalOutcomeEvaluator(taker_fee_pct=fee, slippage_pct=slip)
            f_evals = []
            for sig in [e["signal"] for e in test_evals]:
                if type(sig.get("timestamp")) == int:
                    ts = int(sig["timestamp"])
                else:
                    ts = int(datetime.fromisoformat(sig["timestamp"].replace("Z", "+00:00")).timestamp() * 1000)
                future_klines = fetch_future_klines_for_evaluation(client, sig["symbol"], ts, limit=100)
                f_ev = ev_engine.evaluate_signal(sig, future_klines)
                f_ev["signal"] = sig
                f_evals.append(f_ev)
            fm = tuner.calculate_metrics(f_evals, num_days=test_days)
            f.write(f"| {name} | {fm.win_rate:.2f} | {fm.expected_r:.2f} | {fm.max_drawdown_r:.2f} |\n")
            
    import subprocess
    commit_hash = "unknown"
    try:
        commit_hash = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
    except:
        pass
        
    import dataclasses
    reproducibility = {
        "dataset_start": global_start,
        "dataset_end": global_end,
        "date_range_days": total_days,
        "symbols": target_symbols,
        "strategy_version": "1.0.0",
        "code_commit": commit_hash,
        "baseline_configuration_hash": baseline_config.compute_hash(),
        "final_configuration_hash": final_cfg.compute_hash(),
        "final_configuration": dataclasses.asdict(final_cfg)
    }
    with open("reports/reproducibility.json", "w") as f:
        json.dump(reproducibility, f, indent=2, default=str)
        
    logger.info("=== Research Run Complete ===")

if __name__ == "__main__":
    main()
