"""
Unit tests for all math engines – deterministic, no network calls.
"""
import pytest
from src.engines.volume_engine import VolumeEngine
from src.engines.oi_engine import OIEngine
from src.engines.entry_engine import EntryEngine
from src.engines.sl_engine import StopLossEngine
from src.engines.tp_engine import TakeProfitEngine
from src.engines.risk_engine import RiskEngine
from src.engines.scoring_engine import ScoringEngine
from src.engines.funding_engine import FundingEngine
from src.engines.btc_regime import BTCRegimeEngine
from src.models.signal import SignalModel


# ─── Helpers ────────────────────────────────────────────────────────────────

def make_klines(n=50, base_price=100.0, volume=1000.0):
    """Generate synthetic OHLCV list."""
    klines = []
    for i in range(n):
        p = base_price + i * 0.1
        klines.append({
            "timestamp": i * 60000,
            "open": p,
            "high": p + 0.5,
            "low": p - 0.5,
            "close": p,
            "volume": volume,
        })
    return klines


# ─── Volume Engine ───────────────────────────────────────────────────────────

class TestVolumeEngine:
    def test_flat_volume_not_expanded(self):
        engine = VolumeEngine(rolling_window=20, min_ratio=1.5)
        klines = make_klines(50, volume=1000.0)
        result = engine.analyze_volume(klines)
        assert result["is_expanded"] is False

    def test_spike_volume_expanded(self):
        engine = VolumeEngine(rolling_window=20, min_ratio=1.5)
        klines = make_klines(50, volume=1000.0)
        klines[-1]["volume"] = 3000.0  # 3x spike
        result = engine.analyze_volume(klines)
        assert result["is_expanded"] is True
        assert result["ratio"] >= 1.5

    def test_insufficient_data_returns_zero(self):
        engine = VolumeEngine(rolling_window=20, min_ratio=1.5)
        klines = make_klines(5)
        result = engine.analyze_volume(klines)
        assert result["ratio"] == 0.0
        assert result["is_expanded"] is False


# ─── OI Engine ───────────────────────────────────────────────────────────────

class TestOIEngine:
    def make_oi_hist(self, values):
        return [{"sumOpenInterestValue": str(v)} for v in values]

    def test_price_up_oi_up_is_bullish(self):
        engine = OIEngine(min_oi_change_pct=2.0)
        oi_hist = self.make_oi_hist([1000, 1100])
        result = engine.analyze_oi(oi_hist, "up")
        assert result["signal"] == "bullish"

    def test_price_down_oi_up_is_bearish(self):
        engine = OIEngine(min_oi_change_pct=2.0)
        oi_hist = self.make_oi_hist([1000, 1100])
        result = engine.analyze_oi(oi_hist, "down")
        assert result["signal"] == "bearish"

    def test_oi_decrease_sets_divergence(self):
        engine = OIEngine(min_oi_change_pct=2.0)
        oi_hist = self.make_oi_hist([1000, 900])
        result = engine.analyze_oi(oi_hist, "up")
        assert result["divergence"] is True

    def test_insufficient_data(self):
        engine = OIEngine()
        result = engine.analyze_oi([], "up")
        assert result["oi_change_pct"] == 0.0


# ─── Entry Engine ────────────────────────────────────────────────────────────

class TestEntryEngine:
    def test_market_entry_equals_current_price(self):
        engine = EntryEngine()
        result = engine.calculate_entry("market", 100.0, [], "LONG")
        assert result["entry_price"] == 100.0
        assert result["entry_type"] == "market"

    def test_pullback_long_entry_below_price(self):
        engine = EntryEngine(pullback_pct=0.5)
        result = engine.calculate_entry("pullback", 100.0, [], "LONG")
        assert result["entry_price"] < 100.0

    def test_pullback_short_entry_above_price(self):
        engine = EntryEngine(pullback_pct=0.5)
        result = engine.calculate_entry("pullback", 100.0, [], "SHORT")
        assert result["entry_price"] > 100.0


# ─── Stop Loss Engine ────────────────────────────────────────────────────────

class TestStopLossEngine:
    def test_atr_long_sl_below_entry(self):
        engine = StopLossEngine(atr_period=5, atr_multiplier=1.5)
        klines = make_klines(20)
        sl = engine.calculate_sl("atr", 100.0, "LONG", klines)
        assert sl < 100.0

    def test_atr_short_sl_above_entry(self):
        engine = StopLossEngine(atr_period=5, atr_multiplier=1.5)
        klines = make_klines(20)
        sl = engine.calculate_sl("atr", 100.0, "SHORT", klines)
        assert sl > 100.0

    def test_structural_long_sl_below_entry(self):
        engine = StopLossEngine()
        klines = make_klines(20, base_price=100.0)
        sl = engine.calculate_sl("structural", 101.0, "LONG", klines)
        assert sl < 101.0

    def test_structural_short_sl_above_entry(self):
        engine = StopLossEngine()
        klines = make_klines(20, base_price=100.0)
        sl = engine.calculate_sl("structural", 99.0, "SHORT", klines)
        assert sl > 99.0


# ─── Take Profit Engine ──────────────────────────────────────────────────────

class TestTakeProfitEngine:
    def test_long_tps_above_entry(self):
        engine = TakeProfitEngine(r_multiples=(1.0, 2.0, 3.0))
        tps = engine.calculate_tp(entry_price=100.0, sl_price=98.0, side="LONG")
        assert tps["tp1"] > 100.0
        assert tps["tp2"] > tps["tp1"]
        assert tps["tp3"] > tps["tp2"]

    def test_short_tps_below_entry(self):
        engine = TakeProfitEngine(r_multiples=(1.0, 2.0, 3.0))
        tps = engine.calculate_tp(entry_price=100.0, sl_price=102.0, side="SHORT")
        assert tps["tp1"] < 100.0
        assert tps["tp2"] < tps["tp1"]
        assert tps["tp3"] < tps["tp2"]

    def test_tp_r_multiples_correct(self):
        engine = TakeProfitEngine(r_multiples=(1.0, 2.0, 3.0))
        tps = engine.calculate_tp(entry_price=100.0, sl_price=98.0, side="LONG")
        risk = 100.0 - 98.0  # 2.0
        assert abs(tps["tp1"] - (100.0 + 1.0 * risk)) < 1e-9
        assert abs(tps["tp2"] - (100.0 + 2.0 * risk)) < 1e-9
        assert abs(tps["tp3"] - (100.0 + 3.0 * risk)) < 1e-9

    def test_invalid_sl_returns_zeros(self):
        engine = TakeProfitEngine()
        tps = engine.calculate_tp(entry_price=100.0, sl_price=100.0, side="LONG")
        assert tps["tp1"] == 0.0


# ─── Risk Engine ─────────────────────────────────────────────────────────────

class TestRiskEngine:
    def test_rr_calculated_correctly(self):
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0, max_leverage=20)
        tps = {"tp1": 104.0, "tp2": 106.0, "tp3": 108.0}
        result = engine.calculate_risk_parameters(100.0, 98.0, tps)
        assert result["risk_reward_ratios"]["rr_tp1"] == pytest.approx(2.0)

    def test_leverage_capped(self):
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0, max_leverage=5)
        tps = {"tp1": 110.0, "tp2": 120.0, "tp3": 130.0}
        result = engine.calculate_risk_parameters(100.0, 95.0, tps)
        assert result["leverage"] <= 5

    def test_zero_risk_returns_error(self):
        engine = RiskEngine()
        result = engine.calculate_risk_parameters(100.0, 100.0, {})
        assert "error" in result


# ─── Funding Engine ──────────────────────────────────────────────────────────

class TestFundingEngine:
    def test_extreme_positive_penalises_long(self):
        engine = FundingEngine()
        result = engine.evaluate(0.02, "LONG")  # 2% >> 1% threshold
        assert result["penalty"] > 0

    def test_normal_funding_no_penalty(self):
        engine = FundingEngine()
        result = engine.evaluate(0.001, "LONG")
        assert result["penalty"] == 0

    def test_extreme_negative_penalises_short(self):
        engine = FundingEngine()
        result = engine.evaluate(-0.02, "SHORT")
        assert result["penalty"] > 0

    def test_extreme_positive_no_penalty_for_short(self):
        engine = FundingEngine()
        result = engine.evaluate(0.02, "SHORT")
        assert result["penalty"] == 0


# ─── Signal Model Validation ─────────────────────────────────────────────────

class TestSignalModel:
    def _base_signal(self, **overrides):
        base = {
            "signal_id": "SIG-20261004-TEST-ABCD",
            "timestamp": "2026-10-04T20:00:00Z",
            "symbol": "TESTUSDT",
            "side": "LONG",
            "entry": 100.0,
            "tp1": 102.0,
            "tp2": 104.0,
            "tp3": 106.0,
            "stop_loss": 98.0,
            "leverage": 10,
            "margin": 10.0,
            "position_notional": 100.0,
            "risk_amount": 1.0,
            "risk_reward_tp1": 1.0,
            "risk_reward_tp2": 2.0,
            "risk_reward_tp3": 3.0,
            "confidence": 75,
            "conditions": ["BTC bullish regime"],
            "invalidation_conditions": ["15m close below 98.0"],
            "management_rules": ["TP1 → close 50%"],
        }
        base.update(overrides)
        return base

    def test_valid_signal_passes(self):
        signal = SignalModel(**self._base_signal())
        assert signal.symbol == "TESTUSDT"

    def test_sl_above_entry_for_long_fails(self):
        with pytest.raises(Exception):
            SignalModel(**self._base_signal(stop_loss=102.0))

    def test_tp1_below_entry_for_long_fails(self):
        with pytest.raises(Exception):
            SignalModel(**self._base_signal(tp1=98.0))

    def test_low_rr_fails(self):
        with pytest.raises(Exception):
            SignalModel(**self._base_signal(risk_reward_tp1=0.3))

    def test_short_geometry_valid(self):
        signal = SignalModel(**self._base_signal(
            side="SHORT",
            entry=100.0,
            tp1=98.0, tp2=96.0, tp3=94.0,
            stop_loss=102.0,
        ))
        assert signal.side == "SHORT"


# ─── BTC Regime Engine ───────────────────────────────────────────────────────

class TestBTCRegimeEngine:
    def _make_bullish_klines(self, n=60):
        """Rising price series triggers bullish regime."""
        klines = []
        for i in range(n):
            p = 30000.0 + i * 100
            klines.append({"close": p, "open": p - 50, "high": p + 50, "low": p - 50})
        return klines

    def _make_bearish_klines(self, n=60):
        """Falling price series triggers bearish regime."""
        klines = []
        for i in range(n):
            p = 50000.0 - i * 100
            klines.append({"close": p, "open": p + 50, "high": p + 50, "low": p - 50})
        return klines

    def test_bullish_regime_detected(self):
        engine = BTCRegimeEngine(ema_fast=10, ema_slow=20)
        result = engine.evaluate_regime(self._make_bullish_klines())
        assert result == "bullish"

    def test_bearish_regime_detected(self):
        engine = BTCRegimeEngine(ema_fast=10, ema_slow=20)
        result = engine.evaluate_regime(self._make_bearish_klines())
        assert result == "bearish"

    def test_insufficient_data_returns_neutral(self):
        engine = BTCRegimeEngine()
        result = engine.evaluate_regime([{"close": 100}] * 5)
        assert result == "neutral"
