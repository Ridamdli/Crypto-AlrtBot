#!/usr/bin/env python
"""
Comprehensive Data Quality Validation for Historical Market Data
Validates: missing candles, duplicates, timestamp gaps, invalid prices, missing OI, missing funding, symbol metadata
"""
import sqlite3
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}

DB_PATH = "data_store/historical/historical_data.db"

def validate_klines(conn: sqlite3.Connection, symbol: str, interval: str) -> Dict[str, Any]:
    """Comprehensive kline validation."""
    cursor = conn.cursor()
    
    # Fetch all klines for symbol/interval
    cursor.execute("""
        SELECT timestamp, open, high, low, close, volume 
        FROM klines 
        WHERE symbol=? AND interval=? 
        ORDER BY timestamp ASC
    """, (symbol, interval))
    
    rows = cursor.fetchall()
    if not rows:
        return {"valid": False, "issues": ["No data"], "stats": {}}
    
    klines = [dict(r) for r in rows]
    interval_ms = INTERVAL_MS[interval]
    
    issues = []
    stats = {
        "total_candles": len(klines),
        "duplicate_timestamps": 0,
        "missing_candles": 0,
        "price_anomalies": 0,
        "zero_volume_candles": 0,
        "negative_volume_candles": 0,
        "first_timestamp": klines[0]["timestamp"],
        "last_timestamp": klines[-1]["timestamp"],
        "time_span_days": (klines[-1]["timestamp"] - klines[0]["timestamp"]) / (1000 * 60 * 60 * 24),
        "expected_candles": 0,
    }
    
    # Check duplicates
    timestamps = [k["timestamp"] for k in klines]
    unique_ts = set(timestamps)
    dup_count = len(timestamps) - len(unique_ts)
    if dup_count > 0:
        issues.append(f"Duplicate timestamps: {dup_count}")
        stats["duplicate_timestamps"] = dup_count
    
    # Check timestamp gaps
    expected_count = (klines[-1]["timestamp"] - klines[0]["timestamp"]) // interval_ms + 1
    missing_count = expected_count - len(klines)
    missing_pct = missing_count / expected_count if expected_count > 0 else 0
    stats["expected_candles"] = expected_count
    stats["missing_candles"] = missing_count
    stats["missing_pct"] = missing_pct
    
    if missing_pct > 0.05:  # 5% threshold
        issues.append(f"Missing candles: {missing_count} ({missing_pct*100:.1f}%)")
    elif missing_count > 0:
        issues.append(f"Minor gaps: {missing_count} candles ({missing_pct*100:.2f}%)")
    
    # Validate each candle
    for i, k in enumerate(klines):
        ts, o, h, l, c, v = k["timestamp"], k["open"], k["high"], k["low"], k["close"], k["volume"]
        
        # OHLC validity
        if not (l <= o <= h and l <= c <= h):
            issues.append(f"Candle {i} (ts={ts}): Invalid OHLC O={o} H={h} L={l} C={c}")
            stats["price_anomalies"] += 1
        
        # Zero/negative volume
        if v <= 0:
            if v == 0:
                stats["zero_volume_candles"] += 1
            else:
                stats["negative_volume_candles"] += 1
        
        # Price jump detection (compare with previous close)
        if i > 0:
            prev_c = klines[i-1]["close"]
            if prev_c > 0:
                ratio = max(c, prev_c) / min(c, prev_c)
                if ratio > 10:  # 10x price change in one candle
                    issues.append(f"Candle {i} (ts={ts}): Extreme jump {prev_c} -> {c} ({ratio:.1f}x)")
                    stats["price_anomalies"] += 1
    
    stats["issues_count"] = len(issues)
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def validate_oi(conn: sqlite3.Connection, symbol: str, period: str) -> Dict[str, Any]:
    """Validate Open Interest data."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, sumOpenInterestValue 
        FROM open_interest 
        WHERE symbol=? AND period=? 
        ORDER BY timestamp ASC
    """, (symbol, period))
    
    rows = cursor.fetchall()
    if not rows:
        return {"valid": False, "issues": ["No OI data"], "stats": {}}
    
    data = [dict(r) for r in rows]
    issues = []
    stats = {"total_points": len(data)}
    
    # Check duplicates
    timestamps = [d["timestamp"] for d in data]
    dup_count = len(timestamps) - len(set(timestamps))
    if dup_count > 0:
        issues.append(f"Duplicate OI timestamps: {dup_count}")
    
    # Check zero/negative OI (stored as REAL in DB)
    zero_oi = sum(1 for d in data if float(d.get("sumOpenInterestValue", 0)) <= 0)
    stats["zero_oi_count"] = zero_oi
    stats["zero_oi_pct"] = zero_oi / len(data) if data else 0
    
    if zero_oi > len(data) * 0.1:
        issues.append(f"Excessive zero/negative OI: {zero_oi}/{len(data)}")
    
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def validate_funding(conn: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    """Validate Funding Rate data."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, fundingRate 
        FROM funding_rate 
        WHERE symbol=? 
        ORDER BY timestamp ASC
    """, (symbol,))
    
    rows = cursor.fetchall()
    if not rows:
        return {"valid": False, "issues": ["No funding data"], "stats": {}}
    
    data = [dict(r) for r in rows]
    issues = []
    stats = {"total_points": len(data)}
    
    # Check duplicates
    timestamps = [d["timestamp"] for d in data]
    dup_count = len(timestamps) - len(set(timestamps))
    if dup_count > 0:
        issues.append(f"Duplicate funding timestamps: {dup_count}")
    
    # Extreme funding rates (> 1% per 8h)
    extreme = sum(1 for d in data if abs(d.get("fundingRate", 0)) > 0.01)
    stats["extreme_funding_count"] = extreme
    stats["extreme_funding_pct"] = extreme / len(data) if data else 0
    
    if extreme > len(data) * 0.05:
        issues.append(f"Excessive extreme funding: {extreme}/{len(data)}")
    
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def check_symbol_metadata(conn: sqlite3.Connection, symbols: List[str]) -> Dict[str, Any]:
    """Check symbol metadata consistency across tables."""
    issues = []
    stats = {}
    
    for symbol in symbols:
        sym_issues = []
        cursor = conn.cursor()
        
        # Check klines exist for all intervals
        for interval in ["15m", "1h", "4h"]:
            cursor.execute("SELECT COUNT(*) as cnt FROM klines WHERE symbol=? AND interval=?", (symbol, interval))
            cnt = cursor.fetchone()["cnt"]
            if cnt == 0:
                sym_issues.append(f"No {interval} klines")
        
        # Check OI exists
        cursor.execute("SELECT COUNT(*) as cnt FROM open_interest WHERE symbol=? AND period='15m'", (symbol,))
        oi_cnt = cursor.fetchone()["cnt"]
        if oi_cnt == 0:
            sym_issues.append("No OI data")
        
        # Check funding exists
        cursor.execute("SELECT COUNT(*) as cnt FROM funding_rate WHERE symbol=?", (symbol,))
        fr_cnt = cursor.fetchone()["cnt"]
        if fr_cnt == 0:
            sym_issues.append("No funding data")
        
        if sym_issues:
            issues.append(f"{symbol}: {', '.join(sym_issues)}")
        stats[symbol] = {"has_all_data": len(sym_issues) == 0}
    
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def check_timestamp_alignment(conn: sqlite3.Connection, symbols: List[str]) -> Dict[str, Any]:
    """Check that all symbols have overlapping timestamp ranges."""
    issues = []
    stats = {}
    
    # Get global min/max for 15m klines
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as cnt
        FROM klines 
        WHERE interval='15m' 
        GROUP BY symbol
    """)
    ranges = {r["symbol"]: dict(r) for r in cursor.fetchall()}
    
    if not ranges:
        return {"valid": False, "issues": ["No 15m data for any symbol"], "stats": {}}
    
    global_min = min(r["min_ts"] for r in ranges.values())
    global_max = max(r["max_ts"] for r in ranges.values())
    
    for symbol, r in ranges.items():
        coverage_pct = (r["max_ts"] - r["min_ts"]) / (global_max - global_min) if global_max > global_min else 0
        stats[symbol] = {"min_ts": r["min_ts"], "max_ts": r["max_ts"], "cnt": r["cnt"], "coverage_pct": coverage_pct}
        
        if coverage_pct < 0.5:
            issues.append(f"{symbol}: Low temporal coverage ({coverage_pct*100:.1f}%)")
    
    return {"valid": len(issues) == 0, "issues": issues, "stats": stats}

def main():
    if not os.path.exists(DB_PATH):
        logger.error(f"Database not found at {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Get all symbols in DB
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM klines")
    symbols = [r["symbol"] for r in cursor.fetchall()]
    
    logger.info(f"Validating data for {len(symbols)} symbols: {symbols}")
    
    all_results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": {},
        "summary": {"total_symbols": len(symbols), "symbols_with_issues": 0, "total_issues": 0}
    }
    
    for symbol in symbols:
        logger.info(f"Validating {symbol}...")
        symbol_results = {}
        total_symbol_issues = 0
        
        # Validate klines for each interval
        for interval in ["15m", "1h", "4h"]:
            result = validate_klines(conn, symbol, interval)
            symbol_results[f"klines_{interval}"] = result
            total_symbol_issues += len(result["issues"])
            if result["issues"]:
                for issue in result["issues"]:
                    logger.warning(f"  {symbol} {interval}: {issue}")
        
        # Validate OI
        oi_result = validate_oi(conn, symbol, "15m")
        symbol_results["oi_15m"] = oi_result
        total_symbol_issues += len(oi_result["issues"])
        for issue in oi_result["issues"]:
            logger.warning(f"  {symbol} OI: {issue}")
        
        # Validate Funding
        fr_result = validate_funding(conn, symbol)
        symbol_results["funding"] = fr_result
        total_symbol_issues += len(fr_result["issues"])
        for issue in fr_result["issues"]:
            logger.warning(f"  {symbol} Funding: {issue}")
        
        symbol_results["total_issues"] = total_symbol_issues
        all_results["symbols"][symbol] = symbol_results
        all_results["summary"]["total_issues"] += total_symbol_issues
        if total_symbol_issues > 0:
            all_results["summary"]["symbols_with_issues"] += 1
    
    # Cross-symbol checks
    meta_result = check_symbol_metadata(conn, symbols)
    all_results["metadata_check"] = meta_result
    for issue in meta_result["issues"]:
        logger.warning(f"Metadata: {issue}")
    
    align_result = check_timestamp_alignment(conn, symbols)
    all_results["alignment_check"] = align_result
    for issue in align_result["issues"]:
        logger.warning(f"Alignment: {issue}")
    
    # Save report
    os.makedirs("reports", exist_ok=True)
    import json
    with open("reports/data_quality_report.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary
    print("\n" + "="*60)
    print("DATA QUALITY VALIDATION SUMMARY")
    print("="*60)
    print(f"Total symbols: {all_results['summary']['total_symbols']}")
    print(f"Symbols with issues: {all_results['summary']['symbols_with_issues']}")
    print(f"Total issues found: {all_results['summary']['total_issues']}")
    print("\nPer-symbol issue counts:")
    for symbol, res in all_results["symbols"].items():
        print(f"  {symbol}: {res['total_issues']} issues")
    print(f"\nFull report saved to: reports/data_quality_report.json")
    
    conn.close()

if __name__ == "__main__":
    main()