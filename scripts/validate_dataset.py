#!/usr/bin/env python
"""
Dataset Validation Gate
Validates historical data alignment, completeness, and overlap before research runs.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict

from src.config import AppConfig, CONFIG
from src.data.cached_client import CachedFuturesClient
from src.data.symbol_registry import get_symbol_registry
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DatasetValidator:
    """Validates historical dataset for research readiness."""
    
    INTERVAL_MS = {
        "15m": 900000,
        "1h": 3600000,
        "4h": 14400000,
    }
    
    def __init__(self, db_path: str = "data_store/historical/historical_data.db"):
        self.db_path = db_path
        self.client = CachedFuturesClient(db_path=db_path)
        self.symbol_registry = get_symbol_registry(CONFIG)
        self.available_symbols = [s for s in self.symbol_registry.get_available_symbols() 
                                   if self.symbol_registry.is_available(s)]
        self.validation_results = {}
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def validate_symbol_interval(self, symbol: str, interval: str) -> Dict[str, Any]:
        """Validate a single symbol/interval combination."""
        expected_ms = self.INTERVAL_MS[interval]
        
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Basic stats
            cursor.execute("""
                SELECT MIN(timestamp), MAX(timestamp), COUNT(*) 
                FROM klines WHERE symbol=? AND interval=?
            """, (symbol, interval))
            min_ts, max_ts, count = cursor.fetchone()
            
            if count == 0:
                return {
                    "symbol": symbol,
                    "interval": interval,
                    "valid": False,
                    "error": "No data",
                    "min_ts": None,
                    "max_ts": None,
                    "count": 0,
                }
            
            # Get all timestamps for gap/duplicate analysis
            cursor.execute("""
                SELECT timestamp FROM klines 
                WHERE symbol=? AND interval=?
                ORDER BY timestamp ASC
            """, (symbol, interval))
            timestamps = [row[0] for row in cursor.fetchall()]
        
        # Check duplicates
        unique_ts = set(timestamps)
        duplicate_count = len(timestamps) - len(unique_ts)
        
        # Check gaps
        gaps = []
        for i in range(1, len(timestamps)):
            diff = timestamps[i] - timestamps[i-1]
            if diff > expected_ms * 1.5:  # Allow 50% tolerance for DST/edge cases
                gaps.append({
                    "from": timestamps[i-1],
                    "to": timestamps[i],
                    "gap_ms": diff,
                    "expected_ms": expected_ms,
                    "missing_candles": diff // expected_ms - 1
                })
        
        # Expected count based on time range
        expected_count = (max_ts - min_ts) // expected_ms + 1
        coverage_pct = (count / expected_count * 100) if expected_count > 0 else 0
        
        return {
            "symbol": symbol,
            "interval": interval,
            "valid": True,
            "min_ts": min_ts,
            "max_ts": max_ts,
            "min_dt": datetime.fromtimestamp(min_ts/1000, tz=timezone.utc).isoformat(),
            "max_dt": datetime.fromtimestamp(max_ts/1000, tz=timezone.utc).isoformat(),
            "count": count,
            "expected_count": expected_count,
            "coverage_pct": round(coverage_pct, 2),
            "duplicate_count": duplicate_count,
            "gap_count": len(gaps),
            "gaps": gaps[:10],  # First 10 gaps
            "expected_interval_ms": expected_ms,
        }
    
    def find_common_overlap(self, symbol: str) -> Dict[str, Any]:
        """Find common overlapping time window across all intervals + BTC regime."""
        intervals = ["15m", "1h", "4h"]
        symbol_data = {}
        
        for interval in intervals:
            result = self.validate_symbol_interval(symbol, interval)
            if result["valid"]:
                symbol_data[interval] = {
                    "min_ts": result["min_ts"],
                    "max_ts": result["max_ts"],
                    "count": result["count"],
                }
        
        # BTC 4h data
        btc_result = self.validate_symbol_interval("BTCUSDT", "4h")
        if btc_result["valid"]:
            btc_data = {
                "min_ts": btc_result["min_ts"],
                "max_ts": btc_result["max_ts"],
                "count": btc_result["count"],
            }
        else:
            btc_data = None
        
        if not symbol_data or not btc_data:
            return {
                "symbol": symbol,
                "valid": False,
                "error": "Missing required data",
                "symbol_data": symbol_data,
                "btc_data": btc_data,
            }
        
        # Common overlap across symbol intervals
        symbol_min = max(d["min_ts"] for d in symbol_data.values())
        symbol_max = min(d["max_ts"] for d in symbol_data.values())
        
        # Common overlap including BTC regime
        overlap_min = max(symbol_min, btc_data["min_ts"])
        overlap_max = min(symbol_max, btc_data["max_ts"])
        
        has_overlap = overlap_min <= overlap_max
        
        # Calculate expected timestamps in overlap
        overlap_timestamps = 0
        if has_overlap:
            overlap_timestamps = (overlap_max - overlap_min) // self.INTERVAL_MS["15m"] + 1
        
        return {
            "symbol": symbol,
            "valid": has_overlap,
            "symbol_intervals": symbol_data,
            "btc_regime": btc_data,
            "symbol_only_overlap": {
                "min_ts": symbol_min,
                "max_ts": symbol_max,
                "expected_15m_candles": (symbol_max - symbol_min) // self.INTERVAL_MS["15m"] + 1 if symbol_min <= symbol_max else 0,
            } if symbol_min <= symbol_max else None,
            "full_overlap_with_btc": {
                "min_ts": overlap_min,
                "max_ts": overlap_max,
                "min_dt": datetime.fromtimestamp(overlap_min/1000, tz=timezone.utc).isoformat() if has_overlap else None,
                "max_dt": datetime.fromtimestamp(overlap_max/1000, tz=timezone.utc).isoformat() if has_overlap else None,
                "expected_15m_candles": overlap_timestamps,
            } if has_overlap else None,
            "btc_regime_starts_after_symbol": btc_data["min_ts"] > symbol_min,
            "btc_regime_ends_before_symbol": btc_data["max_ts"] < symbol_max,
        }
    
    def validate_all(self) -> Dict[str, Any]:
        """Run full validation on all available symbols."""
        logger.info(f"Validating dataset for {len(self.available_symbols)} available symbols...")
        
        all_results = {
            "validation_timestamp": datetime.now(timezone.utc).isoformat(),
            "symbols": {},
            "summary": {
                "total_symbols": len(self.available_symbols),
                "fully_valid": 0,
                "symbol_only_overlap": 0,
                "full_btc_overlap": 0,
                "no_overlap": 0,
            }
        }
        
        for symbol in self.available_symbols:
            logger.info(f"  Validating {symbol}...")
            
            # Validate each interval
            interval_results = {}
            for interval in ["15m", "1h", "4h"]:
                interval_results[interval] = self.validate_symbol_interval(symbol, interval)
            
            # Find common overlap
            overlap = self.find_common_overlap(symbol)
            
            self.validation_results[symbol] = {
                "intervals": interval_results,
                "overlap": overlap,
            }
            
            all_results["symbols"][symbol] = {
                "intervals": {k: {k2: v2 for k2, v2 in v.items() if k2 != "gaps"} for k, v in interval_results.items()},
                "overlap": overlap,
            }
            
            # Update summary
            if not overlap["valid"]:
                all_results["summary"]["no_overlap"] += 1
            elif overlap.get("btc_regime_starts_after_symbol") or overlap.get("btc_regime_ends_before_symbol"):
                all_results["summary"]["symbol_only_overlap"] += 1
            else:
                all_results["summary"]["full_btc_overlap"] += 1
                all_results["summary"]["fully_valid"] += 1
        
        return all_results
    
    def generate_report(self, results: Dict[str, Any], output_path: str = "reports/DATASET_VALIDATION.md") -> str:
        """Generate markdown validation report."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        lines = [
            "# Dataset Validation Report",
            f"**Generated**: {results['validation_timestamp']}",
            f"**Database**: {self.db_path}",
            "",
            "## Summary",
            f"- Total available symbols: {results['summary']['total_symbols']}",
            f"- Full BTC regime overlap: {results['summary']['full_btc_overlap']}",
            f"- Symbol-only overlap (BTC regime partial): {results['summary']['symbol_only_overlap']}",
            f"- No valid overlap: {results['summary']['no_overlap']}",
            "",
            "## Per-Symbol Validation",
            "",
        ]
        
        for symbol in self.available_symbols:
            data = results["symbols"][symbol]
            overlap = data["overlap"]
            
            lines.append(f"### {symbol}")
            
            if not overlap["valid"]:
                lines.append(f"- **Status**: [FAIL] NO VALID OVERLAP")
                lines.append(f"- **Reason**: {overlap.get('error', 'Unknown')}")
                lines.append("")
                continue
            
            # Interval status
            has_full_btc = not (overlap.get('btc_regime_starts_after_symbol') or overlap.get('btc_regime_ends_before_symbol'))
            lines.append(f"- **Status**: {'[OK] Full BTC overlap' if has_full_btc else '[WARN] Partial BTC overlap'}")
            
            # Interval details
            lines.append(f"- **Intervals**:")
            for interval in ["15m", "1h", "4h"]:
                iv = data["intervals"][interval]
                if iv["valid"]:
                    lines.append(f"  - {interval}: {iv['count']:,} candles ({iv['min_dt']} to {iv['max_dt']}) | Coverage: {iv['coverage_pct']}% | Duplicates: {iv['duplicate_count']} | Gaps: {iv['gap_count']}")
                else:
                    lines.append(f"  - {interval}: ❌ {iv.get('error', 'No data')}")
            
            # BTC regime
            btc = overlap.get("btc_regime")
            if btc:
                lines.append(f"- **BTCUSDT 4h regime**: {btc['count']:,} candles ({datetime.fromtimestamp(btc['min_ts']/1000, tz=timezone.utc).isoformat()} to {datetime.fromtimestamp(btc['max_ts']/1000, tz=timezone.utc).isoformat()})")
            
            # Overlap details
            if overlap.get("full_overlap_with_btc"):
                fov = overlap["full_overlap_with_btc"]
                lines.append(f"- **Full overlap window**: {fov['min_dt']} to {fov['max_dt']} ({fov['expected_15m_candles']:,} 15m candles)")
            
            if overlap.get("symbol_only_overlap"):
                sov = overlap["symbol_only_overlap"]
                lines.append(f"- **Symbol-only overlap**: {sov.get('expected_15m_candles', 0):,} 15m candles")
            
            lines.append("")
        
        # Gaps summary
        lines.append("## Gap Analysis")
        lines.append("")
        for symbol in self.available_symbols:
            data = self.validation_results[symbol]["intervals"]
            for interval in ["15m", "1h", "4h"]:
                if data[interval]["valid"] and data[interval]["gaps"]:
                    lines.append(f"### {symbol} {interval}")
                    for gap in data[interval]["gaps"][:5]:
                        lines.append(f"- Gap: {gap['missing_candles']} missing candles ({datetime.fromtimestamp(gap['from']/1000, tz=timezone.utc).isoformat()} → {datetime.fromtimestamp(gap['to']/1000, tz=timezone.utc).isoformat()})")
                    if len(data[interval]["gaps"]) > 5:
                        lines.append(f"- ... and {len(data[interval]['gaps']) - 5} more gaps")
                    lines.append("")
        
        report = "\n".join(lines)
        
        with open(output_path, "w") as f:
            f.write(report)
        
        logger.info(f"Validation report saved to {output_path}")
        return report


def main():
    validator = DatasetValidator()
    results = validator.validate_all()
    report = validator.generate_report(results)
    
    # Also save JSON
    json_path = "reports/DATASET_VALIDATION.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    print(report)
    
    # Print key summary
    print("\n" + "="*60)
    print("KEY FINDINGS:")
    print("="*60)
    for symbol in validator.available_symbols:
        overlap = results["symbols"][symbol]["overlap"]
        if overlap["valid"]:
            fov = overlap.get("full_overlap_with_btc")
            if fov:
                print(f"  {symbol}: [OK] Full overlap = {fov['expected_15m_candles']:,} 15m candles")
            else:
                btc_min = overlap.get('btc_regime', {}).get('min_ts', 0)
                btc_date = datetime.fromtimestamp(btc_min/1000, tz=timezone.utc).date() if btc_min else "unknown"
                print(f"  {symbol}: [WARN] Partial - BTC regime starts at {btc_date}")
        else:
            print(f"  {symbol}: [FAIL] {overlap.get('error', 'No overlap')}")


if __name__ == "__main__":
    main()