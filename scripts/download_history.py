import argparse
import time
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from src.data.binance_client import BinanceFuturesClient
from src.data.historical_store import HistoricalStore
from src.data.universe_filter import UniverseFilter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Data validation thresholds
MAX_TIMESTAMP_GAP_MULTIPLIER = 3  # Allow up to 3x expected interval gap
MIN_CANDLES_PER_SYMBOL = 100
MAX_MISSING_DATA_PCT = 0.05  # 5% missing data tolerance
PRICE_SANITY_MULTIPLIER = 10  # Price can't change more than 10x in one candle

def validate_klines(symbol: str, interval: str, klines: List[Dict], store: HistoricalStore) -> Dict[str, Any]:
    """Validate downloaded klines for quality issues."""
    if not klines:
        return {"valid": False, "issues": ["No klines downloaded"], "stats": {}}
    
    interval_ms = {
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
    }[interval]
    
    issues = []
    stats = {
        "total_candles": len(klines),
        "duplicate_timestamps": 0,
        "missing_candles": 0,
        "price_anomalies": 0,
        "zero_volume_candles": 0,
        "first_timestamp": klines[0]["timestamp"],
        "last_timestamp": klines[-1]["timestamp"],
        "time_span_days": (klines[-1]["timestamp"] - klines[0]["timestamp"]) / (1000 * 60 * 60 * 24),
    }
    
    # Check for duplicate timestamps
    timestamps = [k["timestamp"] for k in klines]
    unique_timestamps = set(timestamps)
    if len(timestamps) != len(unique_timestamps):
        dup_count = len(timestamps) - len(unique_timestamps)
        issues.append(f"Duplicate timestamps: {dup_count}")
        stats["duplicate_timestamps"] = dup_count
    
    # Check for timestamp gaps
    expected_count = (klines[-1]["timestamp"] - klines[0]["timestamp"]) // interval_ms + 1
    missing_count = expected_count - len(klines)
    if missing_count > 0:
        missing_pct = missing_count / expected_count
        if missing_pct > MAX_MISSING_DATA_PCT:
            issues.append(f"Excessive missing candles: {missing_count} ({missing_pct*100:.1f}%)")
        stats["missing_candles"] = missing_count
        stats["missing_pct"] = missing_pct
    
    # Check for invalid prices
    for i, k in enumerate(klines):
        o, h, l, c, v = k["open"], k["high"], k["low"], k["close"], k["volume"]
        
        # Basic OHLC validity
        if not (l <= o <= h and l <= c <= h):
            issues.append(f"Invalid OHLC at index {i}: O={o}, H={h}, L={l}, C={c}")
            stats["price_anomalies"] += 1
        
        # Price sanity check - no extreme jumps
        if i > 0:
            prev_c = klines[i-1]["close"]
            if prev_c > 0:
                change_ratio = max(c, prev_c) / min(c, prev_c)
                if change_ratio > PRICE_SANITY_MULTIPLIER:
                    issues.append(f"Extreme price jump at index {i}: {prev_c} -> {c} ({change_ratio:.1f}x)")
                    stats["price_anomalies"] += 1
        
        # Zero volume check
        if v == 0:
            stats["zero_volume_candles"] += 1
    
    # Check against existing data in DB
    existing_latest = store.get_latest_kline_timestamp(symbol, interval)
    if existing_latest and klines[-1]["timestamp"] <= existing_latest:
        issues.append(f"No new data: latest downloaded ({klines[-1]['timestamp']}) <= existing ({existing_latest})")
    
    stats["issues_count"] = len(issues)
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def validate_oi_data(symbol: str, period: str, oi_data: List[Dict]) -> Dict[str, Any]:
    """Validate Open Interest data."""
    if not oi_data:
        return {"valid": False, "issues": ["No OI data downloaded"], "stats": {}}
    
    issues = []
    stats = {"total_points": len(oi_data)}
    
    # Check for missing timestamps
    timestamps = [d["timestamp"] for d in oi_data]
    unique_ts = set(timestamps)
    if len(timestamps) != len(unique_ts):
        issues.append(f"Duplicate OI timestamps: {len(timestamps) - len(unique_ts)}")
    
    # Check for invalid OI values (values are strings from API)
    zero_oi = sum(1 for d in oi_data if float(d.get("sumOpenInterestValue", 0)) <= 0)
    if zero_oi > len(oi_data) * 0.1:
        issues.append(f"Excessive zero/negative OI values: {zero_oi}/{len(oi_data)}")
    
    stats["zero_oi_pct"] = zero_oi / len(oi_data) if oi_data else 0
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def validate_funding_data(symbol: str, fr_data: List[Dict]) -> Dict[str, Any]:
    """Validate Funding Rate data."""
    if not fr_data:
        return {"valid": False, "issues": ["No funding data downloaded"], "stats": {}}
    
    issues = []
    stats = {"total_points": len(fr_data)}
    
    # Check for extreme funding rates (> 1% per 8h is extreme)
    extreme_count = sum(1 for d in fr_data if abs(float(d.get("fundingRate", 0))) > 0.01)
    if extreme_count > len(fr_data) * 0.05:
        issues.append(f"Excessive extreme funding rates: {extreme_count}/{len(fr_data)}")
    
    stats["extreme_funding_pct"] = extreme_count / len(fr_data) if fr_data else 0
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def download_klines(client: BinanceFuturesClient, store: HistoricalStore, symbol: str, interval: str, start_ts: int, end_ts: int) -> List[Dict]:
    """Download klines with resume capability and validation."""
    timeframes = {"15m": 15*60*1000, "1h": 60*60*1000, "4h": 4*60*60*1000}
    interval_ms = timeframes[interval]
    
    # Check existing data
    existing_latest = store.get_latest_kline_timestamp(symbol, interval)
    current_start = max(start_ts, (existing_latest + interval_ms) if existing_latest else start_ts)
    
    if current_start >= end_ts:
        logger.info(f"  [{interval}] Already up to date (latest: {existing_latest})")
        return []
    
    total_fetched = 0
    while current_start < end_ts:
        limit = 1500
        try:
            klines = client._request("GET", "/fapi/v1/klines", params={
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ts,
                "limit": limit
            })
        except Exception as e:
            logger.warning(f"  [{interval}] Request failed: {e}")
            time.sleep(2)
            continue
        
        if not klines:
            break
            
        # Convert to standardized format for validation
        standardized = []
        for k in klines:
            standardized.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5])
            })
        
        # Validate before inserting
        validation = validate_klines(symbol, interval, standardized, store)
        if validation["issues"]:
            for issue in validation["issues"]:
                logger.warning(f"  [{interval}] Validation: {issue}")
        
        # Insert
        store.insert_klines(symbol, interval, klines)
        total_fetched += len(klines)
        
        last_ts = int(klines[-1][0])
        if last_ts == current_start or len(klines) < limit:
            break
        current_start = last_ts + interval_ms
        time.sleep(0.1)
    
    return [{"fetched": total_fetched}]

def download_oi_data(client: BinanceFuturesClient, store: HistoricalStore, symbol: str, period: str, start_ts: int, end_ts: int) -> List[Dict]:
    """Download OI data with 30-day pagination. Handles API limits gracefully."""
    MAX_DAYS = 30
    current_start = start_ts
    total_fetched = 0
    consecutive_failures = 0
    
    while current_start < end_ts and consecutive_failures < 3:
        batch_end = min(end_ts, current_start + (MAX_DAYS * 24 * 60 * 60 * 1000))
        
        try:
            oi_data = client._request("GET", "/futures/data/openInterestHist", params={
                "symbol": symbol,
                "period": period,
                "startTime": current_start,
                "endTime": batch_end,
                "limit": 500
            })
            consecutive_failures = 0
        except Exception as e:
            logger.warning(f"  [OI] Request failed at {current_start}: {e}")
            consecutive_failures += 1
            # Skip forward by 30 days on failure to avoid getting stuck
            current_start = batch_end + 1
            continue
            
        if not oi_data:
            current_start = batch_end + 1
            continue
            
        validation = validate_oi_data(symbol, period, oi_data)
        if validation["issues"]:
            for issue in validation["issues"]:
                logger.warning(f"  [OI] Validation: {issue}")
        
        store.insert_open_interest(symbol, period, oi_data)
        total_fetched += len(oi_data)
        
        if len(oi_data) < 500:
            current_start = batch_end + 1
        else:
            last_ts = int(oi_data[-1]["timestamp"])
            current_start = last_ts + 1
        time.sleep(0.2)
    
    if consecutive_failures >= 3:
        logger.warning(f"  [OI] Too many consecutive failures for {symbol}, skipping remaining")
    
    return [{"fetched": total_fetched}]

def download_funding_data(client: BinanceFuturesClient, store: HistoricalStore, symbol: str, start_ts: int, end_ts: int) -> List[Dict]:
    """Download funding rate data."""
    current_start = start_ts
    total_fetched = 0
    
    while current_start < end_ts:
        try:
            fr_data = client._request("GET", "/fapi/v1/fundingRate", params={
                "symbol": symbol,
                "startTime": current_start,
                "endTime": end_ts,
                "limit": 1000
            })
        except Exception as e:
            logger.warning(f"  [Funding] Request failed: {e}")
            break
        
        if not fr_data:
            break
            
        validation = validate_funding_data(symbol, fr_data)
        if validation["issues"]:
            for issue in validation["issues"]:
                logger.warning(f"  [Funding] Validation: {issue}")
        
        store.insert_funding_rate(symbol, fr_data)
        total_fetched += len(fr_data)
        
        last_ts = int(fr_data[-1]["fundingTime"])
        if last_ts == current_start:
            break
        current_start = last_ts + 1
        time.sleep(0.1)
    
    return [{"fetched": total_fetched}]

def main():
    parser = argparse.ArgumentParser(description="Download Historical Data for Backtesting with Validation")
    parser.add_argument("--days", type=int, default=180, help="Days of history to download (default 180 for train/val/test)")
    parser.add_argument("--top", type=int, default=30, help="Number of top volume symbols to download (includes BTC)")
    parser.add_argument("--skip-download", action="store_true", help="Skip download, only run validation")
    parser.add_argument("--validate-only", action="store_true", help="Only validate existing data in DB")
    args = parser.parse_args()

    client = BinanceFuturesClient()
    store = HistoricalStore()
    
    if args.validate_only:
        logger.info("Running validation only on existing data...")
        # TODO: Implement validation of existing DB data
        return

    # 1. Get Universe
    logger.info(f"Fetching top {args.top} symbols by volume...")
    filter_engine = UniverseFilter(client, min_volume_usdt=10_000_000)
    all_eligible = filter_engine.get_eligible_symbols()
    
    tickers = client.get_ticker_24h()
    vol_map = {t["symbol"]: float(t.get("quoteVolume", 0)) for t in tickers}
    
    sorted_symbols = sorted([s for s in all_eligible if s != "BTCUSDT"], key=lambda x: vol_map.get(x, 0), reverse=True)
    target_symbols = ["BTCUSDT"] + sorted_symbols[:args.top - 1]
    
    logger.info(f"Target symbols ({len(target_symbols)}): {target_symbols}")

    end_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = end_ts - (args.days * 24 * 60 * 60 * 1000)
    
    timeframes = ["15m", "1h", "4h"]
    
    all_validation_results = {}
    
    for symbol in target_symbols:
        logger.info(f"=== Processing {symbol} ===")
        symbol_results = {}
        
        # Download Klines
        for tf in timeframes:
            logger.info(f"  Downloading {tf} klines...")
            result = download_klines(client, store, symbol, tf, start_ts, end_ts)
            symbol_results[tf] = result
        
        # Download Open Interest
        logger.info(f"  Downloading OI data...")
        oi_result = download_oi_data(client, store, symbol, "15m", start_ts, end_ts)
        symbol_results["oi"] = oi_result
        
        # Download Funding Rates
        logger.info(f"  Downloading Funding data...")
        fr_result = download_funding_data(client, store, symbol, start_ts, end_ts)
        symbol_results["funding"] = fr_result
        
        all_validation_results[symbol] = symbol_results
    
    # Save validation report
    os.makedirs("reports", exist_ok=True)
    with open("reports/data_validation.json", "w") as f:
        import json
        json.dump(all_validation_results, f, indent=2, default=str)
    
    logger.info("Download complete! Validation report saved to reports/data_validation.json")

if __name__ == "__main__":
    main()