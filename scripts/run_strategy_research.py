#!/usr/bin/env python
"""
Strategy Research Runner CLI
Run strategy research across multiple coins and strategies.
Optimized with in-memory data caching for fast historical backtesting.
"""
import os
import json
import argparse
import logging
import bisect
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from src.config import AppConfig, CONFIG, DataAvailability
from src.data.cached_client import CachedFuturesClient
from src.data.ohlcv_fetcher import OHLCVFetcher
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.backtest.tuner import ThresholdTuner
from src.strategies import STRATEGY_REGISTRY, STRATEGY_TIMEFRAMES
from src.data.symbol_registry import get_symbol_registry
from src.engines.btc_regime import BTCRegimeEngine
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OverlapWindow:
    """Represents the common overlap window for a symbol."""
    symbol: str
    min_ts: int
    max_ts: int
    min_dt: str
    max_dt: str
    expected_15m_candles: int
    has_btc_regime: bool
    btc_regime_min_ts: Optional[int] = None
    btc_regime_max_ts: Optional[int] = None


class HistoricalDataCache:
    """
    In-memory cache for historical OHLCV data.
    Loads all data once per (symbol, interval) and provides fast slicing by timestamp.
    """
    
    def __init__(self, client: CachedFuturesClient):
        self.client = client
        self._cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._timestamps_index: Dict[Tuple[str, str], List[int]] = {}
    
    def load_symbol_intervals(self, symbol: str, intervals: List[str]) -> None:
        """Pre-load all historical data for a symbol across multiple intervals."""
        for interval in intervals:
            self._load_interval(symbol, interval)
    
    def load_btc_regime_data(self) -> None:
        """Pre-load BTC 4h data for regime evaluation."""
        self._load_interval("BTCUSDT", "4h")
    
    def _load_interval(self, symbol: str, interval: str) -> None:
        """Load all historical klines for a symbol/interval into memory."""
        key = (symbol, interval)
        if key in self._cache:
            return
        
        with self.client.store.get_connection() as conn:
            query = """
                SELECT timestamp, open, high, low, close, volume 
                FROM klines 
                WHERE symbol=? AND interval=?
                ORDER BY timestamp ASC
            """
            rows = conn.execute(query, (symbol, interval)).fetchall()
        
        formatted = []
        timestamps = []
        for r in rows:
            formatted.append({
                "timestamp": r["timestamp"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"]
            })
            timestamps.append(r["timestamp"])
        
        self._cache[key] = formatted
        self._timestamps_index[key] = timestamps
        logger.info(f"Cached {len(formatted)} {interval} klines for {symbol}")
    
    def get_klines_up_to(self, symbol: str, interval: str, end_time: int, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get klines up to end_time (inclusive), limited to the most recent `limit` candles.
        Uses binary search for O(log n) lookup + slice.
        """
        key = (symbol, interval)
        if key not in self._cache:
            self._load_interval(symbol, interval)
            if key not in self._cache:
                return []
        
        timestamps = self._timestamps_index[key]
        if not timestamps:
            return []
        
        # Find rightmost index where timestamp <= end_time
        idx = bisect.bisect_right(timestamps, end_time)
        if idx == 0:
            return []  # No data before end_time
        
        # Get the last `limit` candles up to idx
        start_idx = max(0, idx - limit)
        return self._cache[key][start_idx:idx]
    
    def get_future_klines(self, symbol: str, start_ts: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Get future klines after start_ts for evaluation."""
        key = (symbol, "15m")
        if key not in self._cache:
            self._load_interval(symbol, "15m")
            if key not in self._cache:
                return []
        
        timestamps = self._timestamps_index[key]
        # Find first index where timestamp > start_ts
        idx = bisect.bisect_right(timestamps, start_ts)
        if idx >= len(timestamps):
            return []
        
        end_idx = min(idx + limit, len(timestamps))
        return self._cache[key][idx:end_idx]
    
    def has_sufficient_data(self, symbol: str, interval: str, end_time: int, min_candles: int = 25) -> bool:
        """Check if there are enough candles up to end_time."""
        klines = self.get_klines_up_to(symbol, interval, end_time, limit=min_candles)
        return len(klines) >= min_candles
    
    def get_symbol_timerange(self, symbol: str, interval: str) -> Tuple[Optional[int], Optional[int]]:
        """Get min/max timestamp for a symbol/interval."""
        key = (symbol, interval)
        if key not in self._cache:
            self._load_interval(symbol, interval)
            if key not in self._cache:
                return None, None
        
        timestamps = self._timestamps_index[key]
        if not timestamps:
            return None, None
        
        return timestamps[0], timestamps[-1]


def compute_overlap_window(
    cache: HistoricalDataCache,
    symbol: str,
    btc_regime_min_ts: int,
    btc_regime_max_ts: int,
) -> Optional[OverlapWindow]:
    """
    Compute the common overlap window where:
    - Symbol has 15m, 1h, 4h data
    - BTC 4h regime data exists
    """
    intervals = ["15m", "1h", "4h"]
    symbol_ranges = []
    
    for interval in intervals:
        min_ts, max_ts = cache.get_symbol_timerange(symbol, interval)
        if min_ts is None or max_ts is None:
            logger.warning(f"{symbol} {interval}: no data available")
            return None
        symbol_ranges.append((min_ts, max_ts))
    
    # Symbol-only overlap
    symbol_min = max(r[0] for r in symbol_ranges)
    symbol_max = min(r[1] for r in symbol_ranges)
    
    if symbol_min > symbol_max:
        logger.warning(f"{symbol}: no overlap between symbol intervals")
        return None
    
    # Full overlap with BTC regime
    overlap_min = max(symbol_min, btc_regime_min_ts)
    overlap_max = min(symbol_max, btc_regime_max_ts)
    
    has_btc_regime = overlap_min <= overlap_max
    
    if has_btc_regime:
        expected_15m = (overlap_max - overlap_min) // 900000 + 1
        min_dt = datetime.fromtimestamp(overlap_min/1000, tz=timezone.utc).isoformat()
        max_dt = datetime.fromtimestamp(overlap_max/1000, tz=timezone.utc).isoformat()
        
        return OverlapWindow(
            symbol=symbol,
            min_ts=overlap_min,
            max_ts=overlap_max,
            min_dt=min_dt,
            max_dt=max_dt,
            expected_15m_candles=expected_15m,
            has_btc_regime=True,
            btc_regime_min_ts=btc_regime_min_ts,
            btc_regime_max_ts=btc_regime_max_ts,
        )
    else:
        # No BTC regime overlap - return symbol-only overlap info for regime-neutral runs
        expected_15m = (symbol_max - symbol_min) // 900000 + 1
        min_dt = datetime.fromtimestamp(symbol_min/1000, tz=timezone.utc).isoformat()
        max_dt = datetime.fromtimestamp(symbol_max/1000, tz=timezone.utc).isoformat()
        
        return OverlapWindow(
            symbol=symbol,
            min_ts=symbol_min,
            max_ts=symbol_max,
            min_dt=min_dt,
            max_dt=max_dt,
            expected_15m_candles=expected_15m,
            has_btc_regime=False,
            btc_regime_min_ts=btc_regime_min_ts,
            btc_regime_max_ts=btc_regime_max_ts,
        )


def generate_timestamps(start_ts: int, end_ts: int, interval_ms: int = 900000) -> List[int]:
    """Generate timestamps at specified interval."""
    ts = start_ts
    remainder = ts % interval_ms
    if remainder != 0:
        ts += (interval_ms - remainder)
    timestamps = []
    while ts <= end_ts:
        timestamps.append(ts)
        ts += interval_ms
    return timestamps


def run_focused_research(
    config: AppConfig,
    symbol: str,
    strategy_family: str,
    overlap: OverlapWindow,
    use_btc_regime: bool,
    output_dir: str = "reports",
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
) -> Dict[str, Any]:
    """
    Run focused historical backtest for a single symbol and strategy.
    Uses cached historical data - no repeated SQLite queries.
    """
    os.makedirs(output_dir, exist_ok=True)

    client = CachedFuturesClient()
    evaluator = SignalOutcomeEvaluator()
    tuner = ThresholdTuner()
    symbol_registry = get_symbol_registry(config)
    btc_regime_engine = BTCRegimeEngine()

    # Verify symbol is available
    if not symbol_registry.is_available(symbol):
        logger.error(f"Symbol {symbol} is not available (DATA_MISSING)")
        return {}

    # Verify strategy exists
    if strategy_family not in STRATEGY_REGISTRY:
        logger.error(f"Strategy family '{strategy_family}' not found")
        return {}

    # Create strategy instance
    strategy_class = STRATEGY_REGISTRY.get(strategy_family)
    strategy = strategy_class({"params": {}}, symbol)

    # Initialize data cache and pre-load required data
    cache = HistoricalDataCache(client)
    
    # Determine required intervals for this strategy
    required_intervals = ["15m", "1h", "4h"]
    cache.load_symbol_intervals(symbol, required_intervals)
    if use_btc_regime:
        cache.load_btc_regime_data()

    # Use overlap window for timestamp generation
    scan_end = overlap.max_ts - (3 * 24 * 60 * 60 * 1000)  # 3 days buffer
    all_timestamps = generate_timestamps(overlap.min_ts, scan_end)

    # Chronological split
    n = len(all_timestamps)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train_ts = all_timestamps[:train_end]
    val_ts = all_timestamps[train_end:val_end]
    test_ts = all_timestamps[val_end:]

    train_days = len(train_ts) / 96.0
    val_days = len(val_ts) / 96.0
    test_days = len(test_ts) / 96.0

    regime_suffix = "regime_aware" if use_btc_regime else "regime_neutral"
    logger.info(f"=== Focused Research: {symbol} + {strategy_family} ({regime_suffix}) ===")
    logger.info(f"Overlap window: {overlap.min_dt} to {overlap.max_dt} ({overlap.expected_15m_candles:,} 15m candles)")
    logger.info(f"BTC regime available: {overlap.has_btc_regime}")
    logger.info(f"TRAIN: {len(train_ts)} timestamps ({train_days:.1f} days)")
    logger.info(f"VAL: {len(val_ts)} timestamps ({val_days:.1f} days)")
    logger.info(f"TEST: {len(test_ts)} timestamps ({test_days:.1f} days)")

    all_evals = []

    # Run on TRAIN set
    logger.info(f"Generating evaluations on TRAIN ({len(train_ts)} timestamps)...")

    skipped_no_data = 0
    skipped_no_btc = 0
    skipped_insufficient = 0

    for i, ts in enumerate(train_ts):
        if i % 1000 == 0:
            logger.info(f"  Progress: {i}/{len(train_ts)}")

        try:
            # Check if target symbol has sufficient 15m data
            if not cache.has_sufficient_data(symbol, "15m", ts, min_candles=25):
                skipped_insufficient += 1
                continue

            # Get multi-timeframe data for target symbol (fast in-memory slice)
            klines_15m = cache.get_klines_up_to(symbol, "15m", ts, limit=100)
            klines_1h = cache.get_klines_up_to(symbol, "1h", ts, limit=100)
            klines_4h = cache.get_klines_up_to(symbol, "4h", ts, limit=100)

            if len(klines_15m) < 25:
                skipped_insufficient += 1
                continue

            current_price = float(klines_15m[-1]["close"])

            # Get BTC regime data
            btc_regime = "neutral"
            if use_btc_regime and overlap.has_btc_regime:
                btc_4h = cache.get_klines_up_to("BTCUSDT", "4h", ts, limit=100)
                if len(btc_4h) >= 50:
                    btc_regime = btc_regime_engine.evaluate_regime(btc_4h)
                else:
                    skipped_no_btc += 1
            elif not overlap.has_btc_regime:
                skipped_no_btc += 1

            # Fetch OI data (still uses DB query - typically small/fast)
            oi_hist = client.get_open_interest_hist(symbol, period="15m", limit=10, end_time=ts)
            oi_data = {"signal": "neutral", "oi_change_pct": 0.0, "status": "neutral"}

            # Fetch funding rate (still uses DB query - typically small/fast)
            funding_hist = client.get_funding_rate(symbol, limit=1, end_time=ts)
            funding_rate = float(funding_hist[-1].get("fundingRate", 0)) if funding_hist else 0.0

            # Generate candidates from strategy
            strategy_candidates = strategy.generate_candidates(
                klines_15m=klines_15m,
                klines_1h=klines_1h,
                klines_4h=klines_4h,
                btc_regime=btc_regime,
                oi_data=oi_data,
                funding_rate=funding_rate,
                current_price=current_price,
            )

            for cand in strategy_candidates:
                signal_dict = {
                    "signal_id": f"research_{symbol}_{strategy_family}_{regime_suffix}_{ts}",
                    "timestamp": datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "symbol": cand.symbol,
                    "side": cand.side,
                    "strategy_id": cand.strategy_id,
                    "strategy_version": cand.strategy_version,
                    "entry": cand.entry_price,
                    "tp1": cand.tp1,
                    "tp2": cand.tp2,
                    "tp3": cand.tp3,
                    "stop_loss": cand.stop_loss,
                    "btc_regime": btc_regime,
                }

                # Fetch future klines for evaluation (fast in-memory slice)
                future_klines = cache.get_future_klines(symbol, ts, limit=100)
                if not future_klines:
                    skipped_no_data += 1
                    continue

                ev = evaluator.evaluate_signal(signal_dict, future_klines)
                ev["signal"] = signal_dict
                all_evals.append(ev)

        except Exception as e:
            logger.error(f"Error at {ts} for {symbol}: {e}")
            continue

    logger.info(f"  Skipped: no_future_data={skipped_no_data}, no_btc_regime={skipped_no_btc}, insufficient_candles={skipped_insufficient}")

    if not all_evals:
        logger.warning("No evaluations generated")
        return {}

    # Calculate metrics
    metrics = tuner.calculate_metrics(all_evals, num_days=train_days)

    results = {
        "symbol": symbol,
        "strategy": strategy_family,
        "regime_condition": regime_suffix,
        "use_btc_regime": use_btc_regime,
        "overlap_window": {
            "min_ts": overlap.min_ts,
            "max_ts": overlap.max_ts,
            "min_dt": overlap.min_dt,
            "max_dt": overlap.max_dt,
            "expected_15m_candles": overlap.expected_15m_candles,
            "has_btc_regime": overlap.has_btc_regime,
        },
        "metrics": metrics.__dict__,
        "eval_count": len(all_evals),
        "train_timestamps": len(train_ts),
        "skipped": {
            "no_future_data": skipped_no_data,
            "no_btc_regime": skipped_no_btc,
            "insufficient_candles": skipped_insufficient,
        },
    }

    logger.info(f"{symbol} + {strategy_family} ({regime_suffix}): {len(all_evals)} evals, exp_r={metrics.expected_r:.3f}, win_rate={metrics.win_rate:.3f}, PF={metrics.profit_factor:.2f}")

    # Generate report
    os.makedirs(output_dir, exist_ok=True)
    regime_tag = "regime_aware" if use_btc_regime else "regime_neutral"
    summary_path = os.path.join(output_dir, f"research_{symbol}_{strategy_family}_{regime_tag}.md")
    with open(summary_path, "w") as f:
        f.write(f"# Strategy Research: {symbol} + {strategy_family} ({regime_tag})\n\n")
        f.write(f"**Overlap Window**: {overlap.min_dt} to {overlap.max_dt} ({overlap.expected_15m_candles:,} 15m candles)\n")
        f.write(f"**BTC Regime Available**: {overlap.has_btc_regime}\n")
        f.write(f"**Condition**: {'Regime-aware (BTC regime filter applied)' if use_btc_regime else 'Regime-neutral (no BTC regime filter)'}\n")
        f.write(f"**Train**: {len(train_ts)} timestamps ({train_days:.1f} days)\n")
        f.write(f"**Val**: {len(val_ts)} timestamps ({val_days:.1f} days)\n")
        f.write(f"**Test**: {len(test_ts)} timestamps ({test_days:.1f} days)\n\n")
        f.write("## Results\n\n")
        m = metrics.__dict__
        f.write(f"| Metric | Value |\n")
        f.write(f"| :--- | :---: |\n")
        f.write(f"| Signals Evaluated | {len(all_evals)} |\n")
        f.write(f"| Win Rate | {m['win_rate']:.3f} |\n")
        f.write(f"| Average R | {m['average_r']:.3f} |\n")
        f.write(f"| Expected R | {m['expected_r']:.3f} |\n")
        f.write(f"| Profit Factor | {m['profit_factor']:.2f} |\n")
        f.write(f"| Max Drawdown (R) | {m['max_drawdown_r']:.1f} |\n")
        f.write(f"| Sharpe | {m.get('sharpe', 0):.3f} |\n")
        f.write(f"| Sortino | {m.get('sortino', 0):.3f} |\n")
        f.write(f"\n")
        f.write("## Skipped Timestamps\n\n")
        f.write(f"| Reason | Count |\n")
        f.write(f"| :--- | :---: |\n")
        f.write(f"| Insufficient candles (warmup) | {skipped_insufficient} |\n")
        f.write(f"| No BTC regime data | {skipped_no_btc} |\n")
        f.write(f"| No future data for evaluation | {skipped_no_data} |\n")

    json_path = os.path.join(output_dir, f"research_{symbol}_{strategy_family}_{regime_tag}.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info(f"Results saved to {output_dir}")
    return results


def run_research(
    config: AppConfig,
    symbols: Optional[List[str]] = None,
    strategy_families: Optional[List[str]] = None,
    output_dir: str = "reports",
    walk_forward_windows: int = 3,
    train_ratio: float = 0.6,
) -> Dict[str, Any]:
    """
    Run complete strategy research pipeline (multi-symbol, multi-strategy).
    Uses focused historical research path - NO SignalPipeline.
    Runs TWO explicit conditions per symbol/strategy:
    1. Regime-aware (with BTC regime filter)
    2. Regime-neutral (no BTC regime filter)
    """
    os.makedirs(output_dir, exist_ok=True)

    client = CachedFuturesClient()
    symbol_registry = get_symbol_registry(config)

    # Get available symbols (only AVAILABLE, not DATA_MISSING)
    available_symbols = [s for s in symbol_registry.get_available_symbols() 
                         if symbol_registry.get_data_availability(s) == DataAvailability.AVAILABLE]
    target_symbols = symbols or available_symbols
    target_symbols = [s for s in target_symbols if symbol_registry.is_available(s)]

    # Get strategy families to test
    if strategy_families:
        test_families = [f for f in strategy_families if f in STRATEGY_REGISTRY]
    else:
        test_families = STRATEGY_REGISTRY.list_families()

    logger.info(f"Target symbols ({len(target_symbols)}): {target_symbols}")
    logger.info(f"Strategy families ({len(test_families)}): {test_families}")

    # Pre-compute BTC regime data range
    cache = HistoricalDataCache(client)
    cache.load_btc_regime_data()
    btc_regime_min_ts, btc_regime_max_ts = cache.get_symbol_timerange("BTCUSDT", "4h")
    
    if btc_regime_min_ts is None:
        logger.error("No BTCUSDT 4h data available for regime evaluation")
        return {}
    
    logger.info(f"BTCUSDT 4h regime data: {datetime.fromtimestamp(btc_regime_min_ts/1000, tz=timezone.utc).isoformat()} to {datetime.fromtimestamp(btc_regime_max_ts/1000, tz=timezone.utc).isoformat()}")

    # Compute overlap windows for each symbol
    overlap_windows = {}
    for symbol in target_symbols:
        overlap = compute_overlap_window(cache, symbol, btc_regime_min_ts, btc_regime_max_ts)
        if overlap is None:
            logger.warning(f"{symbol}: no valid data overlap, skipping")
            continue
        overlap_windows[symbol] = overlap
        logger.info(f"{symbol}: overlap={overlap.min_dt} to {overlap.max_dt} ({overlap.expected_15m_candles:,} candles), BTC_regime={overlap.has_btc_regime}")

    # Filter to symbols with valid overlaps
    valid_symbols = list(overlap_windows.keys())
    logger.info(f"Valid symbols for research: {valid_symbols}")

    all_results = {}

    # Run research for each symbol x strategy x regime_condition
    for family in test_families:
        logger.info(f"=== Testing {family} ===")

        family_results = {}

        for symbol in valid_symbols:
            overlap = overlap_windows[symbol]
            
            # Condition 1: Regime-aware (only if BTC regime available)
            if overlap.has_btc_regime:
                logger.info(f"  {symbol} + {family} [REGIME-AWARE]")
                result = run_focused_research(config, symbol, family, overlap, use_btc_regime=True, output_dir=output_dir, train_ratio=train_ratio)
                if result:
                    family_results[f"{symbol}_regime_aware"] = result
            
            # Condition 2: Regime-neutral (always run)
            logger.info(f"  {symbol} + {family} [REGIME-NEUTRAL]")
            result = run_focused_research(config, symbol, family, overlap, use_btc_regime=False, output_dir=output_dir, train_ratio=train_ratio)
            if result:
                family_results[f"{symbol}_regime_neutral"] = result

        all_results[family] = family_results

    # Generate combined summary report
    summary_path = os.path.join(output_dir, "research_summary.md")
    with open(summary_path, "w") as f:
        f.write("# Strategy Research Summary\n\n")
        f.write(f"**BTC Regime Data**: {datetime.fromtimestamp(btc_regime_min_ts/1000, tz=timezone.utc).isoformat()} to {datetime.fromtimestamp(btc_regime_max_ts/1000, tz=timezone.utc).isoformat()}\n")
        f.write(f"**Symbols**: {len(valid_symbols)} ({', '.join(valid_symbols)})\n")
        f.write(f"**Strategy Families**: {len(test_families)}\n\n")

        f.write("## Overlap Windows\n\n")
        f.write("| Symbol | Overlap Window | 15m Candles | BTC Regime |\n")
        f.write("| :--- | :--- | :---: | :---: |\n")
        for symbol in valid_symbols:
            o = overlap_windows[symbol]
            f.write(f"| {symbol} | {o.min_dt} to {o.max_dt} | {o.expected_15m_candles:,} | {'Yes' if o.has_btc_regime else 'No'} |\n")
        f.write("\n")

        f.write("## Results by Strategy Family\n\n")
        for family, results in all_results.items():
            f.write(f"### {family}\n\n")
            f.write("| Symbol + Condition | Signals | Win Rate | Avg R | Exp R | Profit Factor | Max DD |\n")
            f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |\n")
            for key, res in results.items():
                m = res["metrics"]
                f.write(f"| {key} | {res['eval_count']} | {m['win_rate']:.3f} | {m['average_r']:.3f} | {m['expected_r']:.3f} | {m['profit_factor']:.2f} | {m['max_drawdown_r']:.1f} |\n")
            f.write("\n")

    # Save JSON
    json_path = os.path.join(output_dir, "research_results.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info(f"Results saved to {output_dir}")
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Run Strategy Research")
    parser.add_argument("--symbol", type=str, help="Single symbol to research")
    parser.add_argument("--strategy", type=str, help="Specific strategy family")
    parser.add_argument("--all", action="store_true", help="Run all coins/strategies")
    parser.add_argument("--output-dir", default="reports", help="Output directory")
    parser.add_argument("--regime-aware", action="store_true", help="Run regime-aware condition only")
    parser.add_argument("--regime-neutral", action="store_true", help="Run regime-neutral condition only")
    args = parser.parse_args()

    if args.all:
        run_research(CONFIG, output_dir=args.output_dir)
    elif args.symbol and args.strategy:
        # Compute overlap for single symbol
        client = CachedFuturesClient()
        cache = HistoricalDataCache(client)
        cache.load_btc_regime_data()
        btc_min, btc_max = cache.get_symbol_timerange("BTCUSDT", "4h")
        
        if btc_min is None:
            logger.error("No BTCUSDT 4h data available")
            return
        
        cache.load_symbol_intervals(args.symbol, ["15m", "1h", "4h"])
        overlap = compute_overlap_window(cache, args.symbol, btc_min, btc_max)
        
        if overlap is None:
            logger.error(f"No valid overlap for {args.symbol}")
            return
        
        # Determine which condition(s) to run
        if args.regime_aware and not args.regime_neutral:
            use_btc = True
        elif args.regime_neutral and not args.regime_aware:
            use_btc = False
        else:
            # Default: run both if BTC regime available, otherwise regime-neutral only
            if overlap.has_btc_regime:
                # Run both
                run_focused_research(CONFIG, args.symbol, args.strategy, overlap, use_btc_regime=True, output_dir=args.output_dir)
                run_focused_research(CONFIG, args.symbol, args.strategy, overlap, use_btc_regime=False, output_dir=args.output_dir)
                return
            else:
                use_btc = False
        
        run_focused_research(CONFIG, args.symbol, args.strategy, overlap, use_btc_regime=use_btc, output_dir=args.output_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()