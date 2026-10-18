"""
Tests for all strategy families.
"""

import pytest

from src.strategies import (
    BollingerATRStrategy,
    DonchianBreakoutStrategy,
    EMATrendStrategy,
    MomentumFilterStrategy,
    RSIMeanReversionStrategy,
    VolumeBreakoutStrategy,
)


def _make_klines(count: int = 50, base_price: float = 100.0, trend: str = "neutral") -> list[dict]:
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


def _make_klines_1h(count: int = 50, base_price: float = 100.0, trend: str = "up") -> list[dict]:
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


def _make_klines_4h(count: int = 50, base_price: float = 100.0, trend: str = "up") -> list[dict]:
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
        """LONG: entry > SL, single TP > entry."""
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
                assert cand.tp > cand.entry_price, f"LONG TP must be above entry: {StrategyClass.__name__}"
    
    @pytest.mark.parametrize("StrategyClass", [
        DonchianBreakoutStrategy,
        MomentumFilterStrategy,
        EMATrendStrategy,
        BollingerATRStrategy,
        RSIMeanReversionStrategy,
        VolumeBreakoutStrategy,
    ])
    def test_short_signal_geometry(self, StrategyClass):
        """SHORT: entry < SL, single TP < entry."""
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
                assert cand.tp < cand.entry_price, f"SHORT TP must be below entry: {StrategyClass.__name__}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def _atr_klines(closes, volume=1000.0, last_volume=3000.0,
                wick_up=0.2, wick_dn=0.2):
    """Deterministic klines from an explicit close series."""
    klines = []
    prev = closes[0]
    for i, c in enumerate(closes):
        klines.append({
            "timestamp": 1700000000000 + i * 900000,
            "open": prev,
            "high": max(prev, c) + wick_up,
            "low": min(prev, c) - wick_dn,
            "close": c,
            "volume": last_volume if i == len(closes) - 1 else volume,
        })
        prev = c
    return klines


def _gen(strategy, klines, current_price):
    return strategy.generate_candidates(
        klines_15m=klines,
        klines_1h=[],
        klines_4h=[],
        btc_regime="neutral",
        oi_data={},
        funding_rate=0.0,
        current_price=current_price,
    )


class TestMomentumATRStopLevels:
    """ATR stop must compare price-to-price (Donchian pattern).

    Reference numbers below were produced by the corrected implementation
    and hand-checked against the formulas. The ATR-wins cases fail under
    the old implementation (which selected structural in both); the
    structural, filter-off, and clamp cases pin behavior shared by both.
    """

    def test_long_atr_stop_wins_over_structural(self):
        strategy = MomentumFilterStrategy({"params": {}}, "ETHUSDT")
        closes = [100.0 + 0.12 * i for i in range(30)]  # ends 103.48
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(102.96, abs=1e-6)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - cd.metadata["atr"], abs=1e-6)
        risk = cd.entry_price - cd.stop_loss
        assert risk == pytest.approx(0.52, abs=1e-6)
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)

    def test_short_atr_stop_wins_over_structural(self):
        strategy = MomentumFilterStrategy({"params": {}}, "ETHUSDT")
        closes = [103.6 - 0.12 * i for i in range(30)]  # ends 100.12
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(100.64, abs=1e-6)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price + cd.metadata["atr"], abs=1e-6)
        risk = cd.stop_loss - cd.entry_price
        assert risk == pytest.approx(0.52, abs=1e-6)
        assert cd.tp == pytest.approx(cd.entry_price - 2.0 * risk, abs=1e-6)

    def test_structural_stop_wins_when_tighter(self):
        # Huge upside wicks inflate ATR while the 20-bar swing low stays
        # elevated: structural (100.7991) beats the ATR level (~100.04).
        # Same output before and after the fix: pins the fallback branch
        # (incl. the atr_filter guard shape) against silent regression.
        strategy = MomentumFilterStrategy({"params": {}}, "ETHUSDT")
        closes = [100.0 + 0.15 * i for i in range(29)] + [104.5]
        klines = _atr_klines(closes, wick_up=4.0, wick_dn=0.3)
        cands = [c for c in _gen(strategy, klines, closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(100.7991, abs=1e-4)
        assert cd.stop_loss > cd.entry_price - cd.metadata["atr"]
        assert cd.stop_loss < cd.entry_price
        assert cd.entry_price - cd.stop_loss > 0

    def test_atr_filter_off_falls_back_to_structural(self):
        strategy = MomentumFilterStrategy(
            {"params": {"atr_filter": False}}, "ETHUSDT")
        closes = [103.6 - 0.12 * i for i in range(30)]
        klines = _atr_klines(closes)
        cands = [c for c in _gen(strategy, klines, closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(102.9428, abs=1e-4)
        assert cd.stop_loss > cd.entry_price
        risk = cd.stop_loss - cd.entry_price
        assert risk == pytest.approx(2.8228, abs=1e-4)
        assert cd.tp == pytest.approx(cd.entry_price - 2.0 * risk, abs=1e-4)

    def test_min_risk_clamp_bounds_tight_stops(self):
        # Elevated swing with oversized upside-wick ATR: structural wins
        # max(), then the 0.5*ATR clamp binds exactly. Same under both
        # implementations; pins the clamp so it cannot regress silently.
        closes = [103.2] * 8 + [103.2, 102.7, 102.35, 102.8]
        g = 102.8
        while len(closes) < 29:
            g += 0.13
            closes.append(round(g, 4))
        closes.append(104.3)
        assert len(closes) == 30
        strategy = MomentumFilterStrategy({"params": {}}, "ETHUSDT")
        klines = []
        prev = float(closes[0])
        for i, c in enumerate(closes):
            c = float(c)
            wu = 8.0 if 15 <= i <= 28 else 0.2
            klines.append({"timestamp": 1700000000000 + i * 900000,
                           "open": prev, "high": max(prev, c) + wu,
                           "low": min(prev, c) - 0.2, "close": c,
                           "volume": 3000.0 if i == 29 else 1000.0})
            prev = c
        cands = [c for c in _gen(strategy, klines, closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - 0.5 * cd.metadata["atr"], abs=1e-9)
        assert cd.stop_loss == pytest.approx(100.3929, abs=1e-4)
        assert cd.entry_price - cd.stop_loss > 0

    def test_stops_and_tp_geometry_and_sizing(self):
        from src.engines.risk_engine import RiskEngine
        strategy = MomentumFilterStrategy({"params": {}}, "ETHUSDT")
        closes = [100.0 + 0.12 * i for i in range(30)]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        risk = cd.entry_price - cd.stop_loss
        assert risk > 0
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0,
                            max_leverage=10)
        out = engine.calculate_risk_parameters(
            cd.entry_price, cd.stop_loss, {"tp": cd.tp})
        assert "error" not in out
        assert out["position_size"] == pytest.approx(round(10.0 / risk, 4))


class TestVolumeBreakoutATRStopLevels:
    """ATR stop must compare price-to-price (Donchian pattern).

    Reference numbers were produced by the corrected implementation on
    these exact fixtures and hand-checked against the formulas. The
    ATR-wins cases fail under the old implementation (which selected
    structural: LONG 99.7002, SHORT 100.3002); the structural,
    filter-off, and clamp cases pin behavior shared by both.
    """

    def test_long_atr_stop_wins_over_structural(self):
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        closes = [100.0] * 29 + [102.5]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(101.6321, abs=1e-4)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - 1.5 * cd.metadata["atr"], abs=1e-9)
        risk = cd.entry_price - cd.stop_loss
        assert risk == pytest.approx(0.8679, abs=1e-4)
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)

    def test_short_atr_stop_wins_over_structural(self):
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        closes = [100.0] * 29 + [97.5]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(98.3679, abs=1e-4)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price + 1.5 * cd.metadata["atr"], abs=1e-9)
        risk = cd.stop_loss - cd.entry_price
        assert risk == pytest.approx(0.8679, abs=1e-4)
        assert cd.tp == pytest.approx(cd.entry_price - 2.0 * risk, abs=1e-9)

    def test_long_structural_stop_wins_when_tighter(self):
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        closes = [(102.0) if i % 2 else 100.0 for i in range(29)] + [102.5]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(99.7002, abs=1e-4)
        assert cd.stop_loss > cd.entry_price - 1.5 * cd.metadata["atr"]
        assert cd.stop_loss < cd.entry_price
        assert cd.entry_price - cd.stop_loss > 0

    def test_short_structural_stop_wins_when_tighter(self):
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        closes = [(98.0) if i % 2 else 100.0 for i in range(29)] + [97.5]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(100.3002, abs=1e-4)
        assert cd.stop_loss < cd.entry_price + 1.5 * cd.metadata["atr"]
        assert cd.stop_loss > cd.entry_price
        assert cd.stop_loss - cd.entry_price > 0

    def test_atr_filter_off_falls_back_to_structural(self):
        strategy = VolumeBreakoutStrategy(
            {"params": {"atr_filter": False}}, "ADAUSDT")
        for closes, side in (([100.0] * 29 + [102.5], "LONG"),
                             ([100.0] * 29 + [97.5], "SHORT")):
            klines = _atr_klines(closes)
            cands = [c for c in _gen(strategy, klines, closes[-1])
                     if c.side == side]
            assert len(cands) == 1
            cd = cands[0]
            if side == "LONG":
                assert cd.stop_loss == pytest.approx(99.7002, abs=1e-4)
                assert cd.stop_loss < cd.entry_price
            else:
                assert cd.stop_loss == pytest.approx(100.3002, abs=1e-4)
                assert cd.stop_loss > cd.entry_price

    def test_min_risk_clamp_bounds_tight_stops(self):
        # One-bar 50-point dip inflates ATR(14) without touching the
        # prior-20 swing: structural wins max(), then the 0.5*ATR clamp
        # binds exactly. Same under both implementations; pins the clamp.
        klines = []
        for i in range(29):
            klines.append({"timestamp": 1700000000000 + i * 900000,
                           "open": 103.0, "high": 103.1, "low": 102.9,
                           "close": 103.0, "volume": 1000.0})
        klines.append({"timestamp": 1700000000000 + 29 * 900000,
                       "open": 103.0, "high": 104.7, "low": 50.0,
                       "close": 104.5, "volume": 5000.0})
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        cands = [c for c in _gen(strategy, klines, 104.5)
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - 0.5 * cd.metadata["atr"], abs=1e-9)
        assert cd.stop_loss == pytest.approx(102.4536, abs=1e-4)
        assert cd.entry_price - cd.stop_loss > 0

    def test_stops_and_tp_geometry_and_sizing(self):
        from src.engines.risk_engine import RiskEngine
        strategy = VolumeBreakoutStrategy({"params": {}}, "ADAUSDT")
        closes = [100.0] * 29 + [102.5]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        risk = cd.entry_price - cd.stop_loss
        assert risk > 0
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0,
                            max_leverage=10)
        out = engine.calculate_risk_parameters(
            cd.entry_price, cd.stop_loss, {"tp": cd.tp})
        assert "error" not in out
        assert out["position_size"] == pytest.approx(round(10.0 / risk, 4))


def _ema_klines(closes, volume=1000.0, last_volume=5000.0):
    """Deterministic klines with correctly chained opens."""
    klines = []
    prev = float(closes[0])
    for i, c in enumerate(closes):
        c = float(c)
        klines.append({"timestamp": 1700000000000 + i * 900000,
                       "open": prev, "high": max(prev, c) + 0.2,
                       "low": min(prev, c) - 0.2, "close": c,
                       "volume": last_volume if i == len(closes) - 1 else volume})
        prev = c
    return klines


def _ema_gen(strategy, klines, current_price):
    return strategy.generate_candidates(
        klines_15m=klines,
        klines_1h=[],
        klines_4h=[],
        btc_regime="neutral",
        oi_data={},
        funding_rate=0.0,
        current_price=current_price,
    )


class TestEMATrendATRStopLevels:
    """ATR stop must compare price-to-price (Donchian pattern).

    Reference numbers were produced by the corrected implementation on
    these exact fixtures and hand-checked against the formulas. The
    ATR-wins cases fail under the old implementation (which selected
    structural: LONG 101.858, SHORT 101.6415); the structural,
    filter-off, and clamp cases pin behavior shared by both.
    """

    def test_long_atr_stop_wins_over_structural(self):
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        base = [100.0 + 0.06 * i for i in range(55)]
        closes = base + [base[-1] - 0.24, base[-1] - 0.42, base[-1] - 0.60]
        cands = [c for c in _ema_gen(strategy, _ema_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(101.905, abs=1e-3)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - 1.5 * cd.metadata["atr"], abs=1e-9)
        risk = cd.entry_price - cd.stop_loss
        assert risk == pytest.approx(0.735, abs=1e-3)
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)

    def test_short_atr_stop_wins_over_structural(self):
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        base = [103.5 - 0.06 * i for i in range(55)]
        closes = base + [base[-1] + 0.24, base[-1] + 0.42, base[-1] + 0.60]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(101.595, abs=1e-3)
        assert cd.stop_loss == pytest.approx(
            cd.entry_price + 1.5 * cd.metadata["atr"], abs=1e-9)
        risk = cd.stop_loss - cd.entry_price
        assert risk == pytest.approx(0.735, abs=1e-3)
        assert cd.tp == pytest.approx(cd.entry_price - 2.0 * risk, abs=1e-9)

    def test_long_structural_stop_wins_when_tighter(self):
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        base = [100.0 + 0.06 * i for i in range(55)]
        closes = base + [base[-1] - 0.4, base[-1] - 0.7, base[-1] - 1.0]
        cands = [c for c in _ema_gen(strategy, _ema_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(101.858, abs=1e-3)
        assert cd.stop_loss > cd.entry_price - 1.5 * cd.metadata["atr"]
        assert cd.stop_loss < cd.entry_price
        assert cd.entry_price - cd.stop_loss > 0

    def test_short_structural_stop_wins_when_tighter(self):
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        base = [103.5 - 0.06 * i for i in range(55)]
        closes = base + [base[-1] + 0.4, base[-1] + 0.7, base[-1] + 1.0]
        cands = [c for c in _gen(strategy, _atr_klines(closes), closes[-1])
                 if c.side == "SHORT"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(101.6415, abs=1e-3)
        assert cd.stop_loss < cd.entry_price + 1.5 * cd.metadata["atr"]
        assert cd.stop_loss > cd.entry_price
        assert cd.stop_loss - cd.entry_price > 0

    def test_atr_filter_off_falls_back_to_structural(self):
        strategy = EMATrendStrategy(
            {"params": {"atr_stop": False}}, "SOLUSDT")
        base = [100.0 + 0.06 * i for i in range(55)]
        closes = base + [base[-1] - 0.4, base[-1] - 0.7, base[-1] - 1.0]
        cands = [c for c in _ema_gen(strategy, _ema_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        # atr_stop off forces ATR distance 0, but the min-risk clamp still
        # applies (0.5% fallback): structural would sit inside it.
        assert cd.stop_loss == pytest.approx(101.7288, abs=1e-4)
        assert cd.stop_loss < cd.entry_price
        assert cd.entry_price - cd.stop_loss > 0

    def test_min_risk_clamp_bounds_tight_stops(self):
        # Elevated swing with oversized upside-wick ATR: structural wins
        # max(), then the 0.5*ATR clamp binds exactly. Same under both
        # implementations; pins the clamp so it cannot regress silently.
        closes = [102.0 + 0.05 * i for i in range(44)]
        wicks = [(0.2, 0.2)] * 44 + [(5.0, 0.2)] * 14 + [(0.2, 0.2)]
        closes = closes + [104.2 - 0.1 * i for i in range(10)] + [103.3]
        assert len(closes) == 55
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        klines = []
        prev = float(closes[0])
        for i, c in enumerate(closes):
            c = float(c)
            wu, wd = wicks[i]
            klines.append({"timestamp": 1700000000000 + i * 900000,
                           "open": prev, "high": max(prev, c) + wu,
                           "low": min(prev, c) - wd, "close": c,
                           "volume": 5000.0 if i == len(closes) - 1 else 1000.0})
            prev = c
        cands = [c for c in _ema_gen(strategy, klines, closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        assert cd.stop_loss == pytest.approx(
            cd.entry_price - 0.5 * cd.metadata["atr"], abs=1e-9)
        assert cd.stop_loss == pytest.approx(101.175, abs=1e-3)
        assert cd.entry_price - cd.stop_loss > 0

    def test_stops_and_tp_geometry_and_sizing(self):
        from src.engines.risk_engine import RiskEngine
        strategy = EMATrendStrategy({"params": {}}, "SOLUSDT")
        base = [100.0 + 0.06 * i for i in range(55)]
        closes = base + [base[-1] - 0.24, base[-1] - 0.42, base[-1] - 0.60]
        cands = [c for c in _ema_gen(strategy, _ema_klines(closes), closes[-1])
                 if c.side == "LONG"]
        assert len(cands) == 1
        cd = cands[0]
        risk = cd.entry_price - cd.stop_loss
        assert risk > 0
        assert cd.tp == pytest.approx(cd.entry_price + 2.0 * risk, abs=1e-9)
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0,
                            max_leverage=10)
        out = engine.calculate_risk_parameters(
            cd.entry_price, cd.stop_loss, {"tp": cd.tp})
        assert "error" not in out
        assert out["position_size"] == pytest.approx(round(10.0 / risk, 4))