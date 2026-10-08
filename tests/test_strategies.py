"""
Tests for all strategy families.
"""
import pytest
import pandas as pd
from datetime import datetime, timezone

from src.strategies import (
    DonchianBreakoutStrategy,
    MomentumFilterStrategy,
    EMATrendStrategy,
    BollingerATRStrategy,
    RSIMeanReversionStrategy,
    VolumeBreakoutStrategy,
)


def _make_klines(count: int = 50, base_price: float = 100.0, trend: str = "neutral") -> List[Dict]:
    """Generate synthetic klines for testing."""
    klines = []
    price = base_price
    ts = 1700000000000  # Some base timestamp
    
    for i in range(count):
        if trend == "up":
            price *= 1.001
        elif trend == "down":
            price *= 0.999
        elif trend == "volatile":
            price *= 1.002 if i % 2 == 0 else 0.998
        
        # Volume: higher on last candle for breakout tests
        vol = 1000000.0
        if i == count - 1 and trend in ["up", "down"]:
            vol = 3000000.0  # 3x volume on breakout candle
        
        klines.append({
            "timestamp": ts + i * 900000,
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": vol,
        })
    return klines


def _make_funding_rate(rate: float = 0.0) -> float:
    return rate


def _make_oi_data() -> dict:
    return {"status": "UNKNOWN", "signal": "neutral", "oi_change_pct": 0.0, "reason": "test"}


def _make_klines_1h(count: int = 50, base_price: float = 100.0, trend: str = "up") -> List[Dict]:
    """Generate 1h klines with proper volume."""
    klines = []
    price = base_price
    ts = 1700000000000
    
    for i in range(count):
        if trend == "up":
            price *= 1.001
        elif trend == "down":
            price *= 0.999
        
        klines.append({
            "timestamp": ts + i * 3600000,
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 1000000.0,
        })
    return klines


def _make_klines_4h(count: int = 50, base_price: float = 100.0, trend: str = "up") -> List[Dict]:
    """Generate 4h klines with proper volume."""
    klines = []
    price = base_price
    ts = 1700000000000
    
    for i in range(count):
        if trend == "up":
            price *= 1.001
        elif trend == "down":
            price *= 0.999
        
        klines.append({
            "timestamp": ts + i * 14400000,
            "open": price * 0.999,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 1000000.0,
        })
    return klines


class TestDonchianBreakoutStrategy:
    def test_donchian_long_breakout(self):
        """Test Donchian breakout generates LONG signal."""
        strategy = DonchianBreakoutStrategy({"params": {"lookback": 20}}, "BTCUSDT")
        
        # Create klines with clear breakout
        klines = _make_klines(50, 100.0, "up")
        # Make last candle break above 20-period high with high volume
        klines[-1]["close"] = 110.0
        klines[-1]["high"] = 110.5
        klines[-1]["low"] = 109.5
        klines[-1]["volume"] = 3000000.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "up"),
            klines_4h=_make_klines_4h(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=110.0,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"
        assert candidates[0].entry_price > 0
        assert candidates[0].stop_loss < candidates[0].entry_price
    
    def test_donchian_short_breakout(self):
        """Test Donchian breakout generates SHORT signal."""
        strategy = DonchianBreakoutStrategy({"params": {"lookback": 20}}, "BTCUSDT")
        
        klines = _make_klines(50, 100.0, "down")
        klines[-1]["close"] = 90.0
        klines[-1]["high"] = 90.5
        klines[-1]["low"] = 89.5
        klines[-1]["volume"] = 3000000.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "down"),
            klines_4h=_make_klines_4h(50, 100.0, "down"),
            btc_regime="bearish",
            oi_data={},
            funding_rate=0.0,
            current_price=90.0,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "SHORT"
        assert candidates[0].stop_loss > candidates[0].entry_price
    
    def test_donchian_no_signal_in_range(self):
        """Test no signal when price in channel."""
        strategy = DonchianBreakoutStrategy({"params": {"lookback": 20}}, "BTCUSDT")
        
        klines = _make_klines(50, 100.0, "neutral")
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "neutral"),
            klines_4h=_make_klines_4h(50, 100.0, "neutral"),
            btc_regime="neutral",
            oi_data={},
            funding_rate=0.0,
            current_price=100.0,
        )
        
        assert len(candidates) == 0


class TestMomentumFilterStrategy:
    def test_momentum_long(self):
        """Test momentum filter generates LONG."""
        strategy = MomentumFilterStrategy({"params": {"lookback": 20, "threshold_pct": 0.02}}, "ETHUSDT")
        
        klines = _make_klines(50, 100.0, "up")
        # Make recent price significantly above 20-period low
        klines[-1]["close"] = 105.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines(50, 100.0, "up"),
            klines_4h=_make_klines(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=105.0,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"
    
    def test_momentum_short(self):
        """Test momentum filter generates SHORT."""
        strategy = MomentumFilterStrategy({"params": {"lookback": 20, "threshold_pct": 0.02}}, "ETHUSDT")
        
        klines = _make_klines(50, 100.0, "down")
        klines[-1]["close"] = 95.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines(50, 100.0, "down"),
            klines_4h=_make_klines(50, 100.0, "down"),
            btc_regime="bearish",
            oi_data={},
            funding_rate=0.0,
            current_price=95.0,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "SHORT"


class TestEMATrendStrategy:
    def test_ema_trend_long_pullback(self):
        """Test EMA trend pullback generates LONG."""
        strategy = EMATrendStrategy({"params": {"fast_period": 20, "slow_period": 50, "pullback_pct": 1.0}}, "SOLUSDT")
        
        # Create klines with bullish EMA alignment and pullback
        klines = []
        price = 100.0
        for i in range(60):
            price *= 1.001  # Uptrend
            vol = 1000000.0
            if i == 59:
                vol = 3000000.0  # High volume on pullback
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price * 0.999,
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": vol,
            })
        # Pullback to fast EMA (within 1%)
        klines[-1]["close"] = 105.5
        klines[-1]["high"] = 105.8
        klines[-1]["low"] = 105.2
        klines[-1]["volume"] = 3000000.0
        
        # Fast EMA will be ~105.18, so pullback price should be ~104.66 (within 1% below fast EMA)
        pullback_price = 104.66
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "up"),
            klines_4h=_make_klines_4h(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=pullback_price,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"
    
    def test_ema_trend_short_pullback(self):
        """Test EMA trend pullback generates SHORT."""
        strategy = EMATrendStrategy({"params": {"fast_period": 20, "slow_period": 50, "pullback_pct": 1.0}}, "SOLUSDT")
        
        klines = []
        price = 100.0
        for i in range(60):
            price *= 0.999  # Downtrend
            vol = 1000000.0
            if i == 59:
                vol = 3000000.0
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price * 1.002,
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": vol,
            })
        # Fast EMA will be ~95.08, so pullback price should be ~95.55 (within 1% above fast EMA)
        pullback_price = 95.55
        klines[-1]["close"] = pullback_price
        klines[-1]["high"] = 95.8
        klines[-1]["low"] = 95.2
        klines[-1]["volume"] = 3000000.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "down"),
            klines_4h=_make_klines_4h(50, 100.0, "down"),
            btc_regime="bearish",
            oi_data={},
            funding_rate=0.0,
            current_price=pullback_price,
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "SHORT"


class TestBollingerATRStrategy:
    def test_bollinger_breakout_long(self):
        """Test Bollinger band breakout long."""
        strategy = BollingerATRStrategy({"params": {"bb_period": 20, "bb_std": 2.0, "bandwidth_threshold": 0.15}}, "ADAUSDT")
        
        # Create tight bands then breakout with proper volume
        klines = []
        price = 100.0
        for i in range(30):
            price += 0.1
            vol = 1000000.0
            if i == 29:
                vol = 3000000.0  # High volume on breakout
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": vol,
            })
        # Tight range then breakout with high volume
        for i in range(30, 50):
            if i < 40:
                price += 0.01  # Tight compression
            else:
                price += 1.0  # Breakout
            vol = 1000000.0
            if i >= 40:
                vol = 3000000.0  # High volume on breakout
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": vol,
            })
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "up"),
            klines_4h=_make_klines_4h(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=klines[-1]["close"],
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"


class TestRSIMeanReversionStrategy:
    def test_rsi_oversold_long(self):
        """Test RSI oversold generates LONG."""
        strategy = RSIMeanReversionStrategy({"params": {"rsi_period": 14, "oversold_threshold": 30, "trend_filter": False}}, "XRPUSDT")
        
        # Create oversold RSI scenario with proper volume
        klines = []
        price = 100.0
        for i in range(30):
            price *= 0.995  # Declining
            vol = 1000000.0
            if i == 29:
                vol = 3000000.0  # High volume on reversal
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": vol,
            })
        # RSI should be low now, then uptick with high volume
        klines.append({
            "timestamp": 1700000000000 + 30 * 900000,
            "open": price * 0.99,
            "high": price * 1.02,
            "low": price * 0.98,
            "close": price * 1.01,
            "volume": 3000000.0,
        })
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "down"),
            klines_4h=_make_klines_4h(50, 100.0, "down"),
            btc_regime="neutral",
            oi_data={},
            funding_rate=0.0,
            current_price=klines[-1]["close"],
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"
    
    def test_rsi_overbought_short(self):
        """Test RSI overbought generates SHORT."""
        strategy = RSIMeanReversionStrategy({"params": {"rsi_period": 14, "overbought_threshold": 70, "trend_filter": False}}, "XRPUSDT")
        
        klines = []
        price = 100.0
        for i in range(30):
            price *= 1.005  # Rising
            vol = 1000000.0
            if i == 29:
                vol = 3000000.0  # High volume on reversal
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": vol,
            })
        # Overbought then downtick with high volume
        klines.append({
            "timestamp": 1700000000000 + 30 * 900000,
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price * 0.99,
            "volume": 3000000.0,
        })
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "up"),
            klines_4h=_make_klines_4h(50, 100.0, "up"),
            btc_regime="neutral",
            oi_data={},
            funding_rate=0.0,
            current_price=klines[-1]["close"],
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "SHORT"


class TestVolumeBreakoutStrategy:
    def test_volume_breakout_long(self):
        """Test volume breakout generates LONG."""
        strategy = VolumeBreakoutStrategy({"params": {"structural_lookback": 20, "volume_multiplier": 2.0}}, "ADAUSDT")
        
        # Create structural resistance then breakout with volume
        klines = []
        price = 100.0
        for i in range(25):
            price += 0.1
            vol = 1000000.0
            if i == 24:
                vol = 3000000.0  # High volume on breakout
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": vol,
            })
        # Breakout with high volume
        for i in range(25, 35):
            if i < 30:
                price += 0.1
            else:
                price += 1.5
            vol = 1000000.0
            if i >= 30:
                vol = 3000000.0  # High volume on breakout
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": vol,
            })
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "up"),
            klines_4h=_make_klines_4h(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=klines[-1]["close"],
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "LONG"
    
    def test_volume_breakout_short(self):
        """Test volume breakout generates SHORT."""
        strategy = VolumeBreakoutStrategy({"params": {"structural_lookback": 20, "volume_multiplier": 2.0}}, "ADAUSDT")
        
        klines = []
        price = 100.0
        for i in range(25):
            price -= 0.1
            vol = 1000000.0
            if i == 24:
                vol = 3000000.0
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": vol,
            })
        for i in range(25, 35):
            if i < 30:
                price -= 0.1
            else:
                price -= 1.5
            vol = 1000000.0
            if i >= 30:
                vol = 3000000.0  # High volume on breakout
            klines.append({
                "timestamp": 1700000000000 + i * 900000,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": vol,
            })
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines_1h(50, 100.0, "down"),
            klines_4h=_make_klines_4h(50, 100.0, "down"),
            btc_regime="bearish",
            oi_data={},
            funding_rate=0.0,
            current_price=klines[-1]["close"],
        )
        
        assert len(candidates) >= 1
        assert candidates[0].side == "SHORT"


class TestStrategyGeometry:
    """Test that all strategies produce valid signal geometry."""
    
    @pytest.mark.parametrize("StrategyClass", [
        DonchianBreakoutStrategy,
        MomentumFilterStrategy,
        EMATrendStrategy,
        BollingerATRStrategy,
        RSIMeanReversionStrategy,
        VolumeBreakoutStrategy,
    ])
    def test_long_signal_geometry(self, StrategyClass):
        """LONG: entry > SL, TP1/2/3 > entry."""
        strategy = StrategyClass({"params": {}}, "TESTUSDT")
        
        # Create klines that should trigger a LONG
        klines = _make_klines(50, 100.0, "up")
        klines[-1]["close"] = 110.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines(50, 100.0, "up"),
            klines_4h=_make_klines(50, 100.0, "up"),
            btc_regime="bullish",
            oi_data={},
            funding_rate=0.0,
            current_price=110.0,
        )
        
        for cand in candidates:
            if cand.side == "LONG":
                assert cand.entry_price > cand.stop_loss, f"LONG SL must be below entry: {StrategyClass.__name__}"
                assert cand.tp1 > cand.entry_price, f"LONG TP1 must be above entry: {StrategyClass.__name__}"
                assert cand.tp2 > cand.tp1, f"LONG TP2 > TP1: {StrategyClass.__name__}"
                assert cand.tp3 > cand.tp2, f"LONG TP3 > TP2: {StrategyClass.__name__}"
    
    @pytest.mark.parametrize("StrategyClass", [
        DonchianBreakoutStrategy,
        MomentumFilterStrategy,
        EMATrendStrategy,
        BollingerATRStrategy,
        RSIMeanReversionStrategy,
        VolumeBreakoutStrategy,
    ])
    def test_short_signal_geometry(self, StrategyClass):
        """SHORT: entry < SL, TP1/2/3 < entry."""
        strategy = StrategyClass({"params": {}}, "TESTUSDT")
        
        klines = _make_klines(50, 100.0, "down")
        klines[-1]["close"] = 90.0
        
        candidates = strategy.generate_candidates(
            klines_15m=klines,
            klines_1h=_make_klines(50, 100.0, "down"),
            klines_4h=_make_klines(50, 100.0, "down"),
            btc_regime="bearish",
            oi_data={},
            funding_rate=0.0,
            current_price=90.0,
        )
        
        for cand in candidates:
            if cand.side == "SHORT":
                assert cand.entry_price < cand.stop_loss, f"SHORT SL must be above entry: {StrategyClass.__name__}"
                assert cand.tp1 < cand.entry_price, f"SHORT TP1 must be below entry: {StrategyClass.__name__}"
                assert cand.tp2 < cand.tp1, f"SHORT TP2 < TP1: {StrategyClass.__name__}"
                assert cand.tp3 < cand.tp2, f"SHORT TP3 < TP2: {StrategyClass.__name__}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])