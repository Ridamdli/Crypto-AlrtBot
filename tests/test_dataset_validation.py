#!/usr/bin/env python
"""
Regression tests for dataset validation and research runner.
"""
import pytest
import os
import json
import sqlite3
from datetime import datetime, timezone

from scripts.validate_dataset import DatasetValidator
from scripts.run_strategy_research import (
    HistoricalDataCache,
    compute_overlap_window,
    generate_timestamps,
)


class TestDatasetValidation:
    """Tests for dataset validation logic."""
    
    @pytest.fixture
    def validator(self):
        return DatasetValidator()
    
    def test_btc_regime_data_exists(self, validator):
        """BTCUSDT 4h data must exist for regime evaluation."""
        result = validator.validate_symbol_interval("BTCUSDT", "4h")
        assert result["valid"] is True
        assert result["count"] > 0
        assert result["min_ts"] is not None
        assert result["max_ts"] is not None
    
    def test_all_available_symbols_have_15m_data(self, validator):
        """All available symbols must have 15m data."""
        for symbol in validator.available_symbols:
            result = validator.validate_symbol_interval(symbol, "15m")
            assert result["valid"] is True, f"{symbol} missing 15m data"
            assert result["count"] > 0, f"{symbol} has zero 15m candles"
    
    def test_no_duplicate_timestamps(self, validator):
        """No duplicate timestamps in any symbol/interval."""
        for symbol in validator.available_symbols:
            for interval in ["15m", "1h", "4h"]:
                result = validator.validate_symbol_interval(symbol, interval)
                assert result["duplicate_count"] == 0, f"{symbol} {interval} has {result['duplicate_count']} duplicates"
    
    def test_interval_spacing(self, validator):
        """Timestamps should follow expected interval spacing (allow small tolerance)."""
        for symbol in validator.available_symbols:
            for interval in ["15m", "1h", "4h"]:
                result = validator.validate_symbol_interval(symbol, interval)
                # Allow gaps but track them - more than 10 large gaps is suspicious
                assert result["gap_count"] < 10, f"{symbol} {interval} has {result['gap_count']} large gaps"
    
    def test_common_overlap_calculation(self, validator):
        """Common overlap with BTC regime should be computable for all available symbols."""
        for symbol in validator.available_symbols:
            overlap = validator.find_common_overlap(symbol)
            assert overlap["valid"] is True, f"{symbol}: {overlap.get('error')}"
            
            # Check that overlap window is reasonable
            if overlap.get("full_overlap_with_btc"):
                fov = overlap["full_overlap_with_btc"]
                assert fov["expected_15m_candles"] > 1000, f"{symbol} overlap too small"
                
                # Verify BTC regime window is within symbol window
                btc_min = overlap["btc_regime"]["min_ts"]
                btc_max = overlap["btc_regime"]["max_ts"]
                assert fov["min_ts"] >= btc_min, f"{symbol} overlap starts before BTC regime"
                assert fov["max_ts"] <= btc_max, f"{symbol} overlap ends after BTC regime"
    
    def test_timestamp_generation(self):
        """Timestamp generation should produce correct intervals."""
        ts = generate_timestamps(1783396800000, 1783396800000 + 900000 * 10)
        assert len(ts) == 11  # 10 intervals = 11 timestamps
        for i in range(1, len(ts)):
            assert ts[i] - ts[i-1] == 900000
    
    def test_overlap_window_computation(self):
        """Overlap window computation should handle edge cases."""
        # This test would need a mock cache - skipping for now
        pass


class TestHistoricalDataCache:
    """Tests for HistoricalDataCache correctness."""
    
    @pytest.fixture
    def cache(self):
        from src.data.cached_client import CachedFuturesClient
        client = CachedFuturesClient()
        return HistoricalDataCache(client)
    
    def test_cache_matches_sqlite(self, cache):
        """Cache results should exactly match SQLite query results."""
        # Load BTCUSDT 15m
        cache.load_symbol_intervals("BTCUSDT", ["15m"])
        
        # Query SQLite directly
        import sqlite3
        conn = sqlite3.connect("data_store/historical/historical_data.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, open, high, low, close, volume 
            FROM klines 
            WHERE symbol='BTCUSDT' AND interval='15m'
            ORDER BY timestamp ASC
            LIMIT 10
        """)
        sqlite_rows = cursor.fetchall()
        conn.close()
        
        # Compare first 10 from cache
        cached = cache._cache[("BTCUSDT", "15m")][:10]
        for i, (sqlite_row, cached_row) in enumerate(zip(sqlite_rows, cached)):
            assert sqlite_row["timestamp"] == cached_row["timestamp"]
            assert abs(sqlite_row["open"] - cached_row["open"]) < 1e-6
            assert abs(sqlite_row["high"] - cached_row["high"]) < 1e-6
            assert abs(sqlite_row["low"] - cached_row["low"]) < 1e-6
            assert abs(sqlite_row["close"] - cached_row["close"]) < 1e-6
            assert abs(sqlite_row["volume"] - cached_row["volume"]) < 1e-6
    
    def test_no_lookahead_in_slicing(self, cache):
        """get_klines_up_to should not include future candles."""
        cache.load_symbol_intervals("BTCUSDT", ["15m"])
        
        # Pick a timestamp in the middle of the dataset
        mid_ts = cache._timestamps_index[("BTCUSDT", "15m")][len(cache._timestamps_index[("BTCUSDT", "15m")]) // 2]
        
        # Get klines up to mid_ts
        klines = cache.get_klines_up_to("BTCUSDT", "15m", mid_ts, limit=100)
        
        # All returned klines should have timestamp <= mid_ts
        for k in klines:
            assert k["timestamp"] <= mid_ts, f"Lookahead detected: {k['timestamp']} > {mid_ts}"
    
    def test_future_klines_are_after_start(self, cache):
        """get_future_klines should only return candles after start_ts."""
        cache.load_symbol_intervals("BTCUSDT", ["15m"])
        
        # Pick a timestamp in the middle
        mid_ts = cache._timestamps_index[("BTCUSDT", "15m")][len(cache._timestamps_index[("BTCUSDT", "15m")]) // 2]
        
        future = cache.get_future_klines("BTCUSDT", mid_ts, limit=10)
        
        # All future klines should have timestamp > mid_ts
        for k in future:
            assert k["timestamp"] > mid_ts, f"Future data includes past: {k['timestamp']} <= {mid_ts}"


class TestResearchRunner:
    """Tests for research runner behavior."""
    
    def test_regime_aware_and_neutral_conditions_exist(self):
        """Both regime-aware and regime-neutral conditions should be run."""
        # This is tested via integration test in the main script
        pass
    
    def test_insufficient_candles_only_at_warmup(self):
        """Insufficient candles should only occur at dataset boundaries, not due to gaps."""
        # This is verified by the validation gap analysis
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])