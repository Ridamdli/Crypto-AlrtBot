"""
Comprehensive tests for:
  - Deterministic Replay Harness  (Phase 1)
  - Historical Signal Outcome Evaluator  (Phase 2)
  - Threshold Tuner / metrics  (Phase 3)
  - Signal Lifecycle Tracking  (Phase 5)
  - Scheduler duplicate prevention  (Phase 6)

All tests are network-free and use synthetic/mock data.
"""

import copy
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# ── imports under test ─────────────────────────────────────────────────────────
from src.backtest.replay import ReplayHarness
from src.backtest.evaluator import SignalOutcomeEvaluator, AmbiguousResolution
from src.backtest.tuner import ThresholdTuner, DatasetSplit, PerformanceMetrics
from src.models.lifecycle import SignalLifecycleTracker, SignalState
from src.scheduler import BotScheduler
from src.config import AppConfig
from src.engines.oi_engine import OIEngine


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_kline(open_=100.0, high=105.0, low=95.0, close=100.0, ts=0):
    return {"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}


def _long_signal(**overrides):
    base = {
        "signal_id": "SIG-TEST-LONG-001",
        "symbol": "TESTUSDT",
        "side": "LONG",
        "entry": 100.0,
        "tp1": 103.0,
        "tp2": 106.0,
        "tp3": 109.0,
        "stop_loss": 97.0,
    }
    base.update(overrides)
    return base


def _short_signal(**overrides):
    base = {
        "signal_id": "SIG-TEST-SHORT-001",
        "symbol": "TESTUSDT",
        "side": "SHORT",
        "entry": 100.0,
        "tp1": 97.0,
        "tp2": 94.0,
        "tp3": 91.0,
        "stop_loss": 103.0,
    }
    base.update(overrides)
    return base


def _eval_result(entry_triggered=True, tp1=False, tp2=False, tp3=False, sl=False,
                 net_r=0.0, holding=10, first_event="TIMEOUT"):
    """Synthetic evaluation result for tuner metric tests."""
    return {
        "entry_triggered": entry_triggered,
        "tp1_hit": tp1,
        "tp2_hit": tp2,
        "tp3_hit": tp3,
        "sl_hit": sl,
        "net_realized_r": net_r,
        "holding_bars": holding,
        "first_event": first_event,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Deterministic Replay Harness
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplayHarness:
    """
    Tests that:
      1. Replay(ts=X) == Replay(ts=X)  —  identical signals for identical inputs
      2. Different inputs produce different results
      3. No future data is used (end_time parameter is forwarded)
    """

    def _build_harness_with_mock_pipeline(self, signals_to_return):
        """Creates a ReplayHarness whose pipeline is replaced by a deterministic mock."""
        mock_pipeline = MagicMock()
        mock_pipeline.run_pipeline.return_value = signals_to_return
        harness = ReplayHarness(storage_dir="data_store/test_replay")
        harness.pipeline = mock_pipeline
        return harness, mock_pipeline

    def test_same_timestamp_same_result(self):
        """Replay(timestamp=X) == Replay(timestamp=X)"""
        expected = [{"signal_id": "SIG-001", "symbol": "BTCUSDT", "confidence": 80}]
        harness, _ = self._build_harness_with_mock_pipeline(expected)

        run1 = harness.replay_timestamp("2026-01-01T00:00:00Z")
        run2 = harness.replay_timestamp("2026-01-01T00:00:00Z")

        assert run1 == run2

    def test_assert_deterministic_passes_on_identical_runs(self):
        """assert_deterministic should return True for two identical signal lists."""
        sig = {
            "signal_id": "SIG-001",
            "symbol": "ETHUSDT",
            "side": "LONG",
            "confidence": 75,
            "entry": 100.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "tp3": 109.0,
            "stop_loss": 97.0,
            "leverage": 10,
            "margin": 10.0,
            "position_notional": 100.0,
            "risk_amount": 1.0,
            "risk_reward_tp1": 1.0,
            "risk_reward_tp2": 2.0,
            "risk_reward_tp3": 3.0,
            "conditions": ["BTC bullish"],
            "invalidation_conditions": ["Close below 97"],
            "management_rules": ["TP1 → 50%"],
            "configuration_hash": "abc123",
        }
        run1 = [sig]
        run2 = [copy.deepcopy(sig)]

        assert ReplayHarness.assert_deterministic(run1, run2) is True

    def test_assert_deterministic_fails_on_price_mismatch(self):
        """assert_deterministic must raise AssertionError if entry price differs."""
        sig1 = {"signal_id": "SIG-001", "symbol": "X", "side": "LONG", "confidence": 80,
                "entry": 100.0, "tp1": 103.0, "tp2": 106.0, "tp3": 109.0, "stop_loss": 97.0,
                "leverage": 10, "margin": 10.0, "position_notional": 100.0, "risk_amount": 1.0,
                "risk_reward_tp1": 1.0, "risk_reward_tp2": 2.0, "risk_reward_tp3": 3.0,
                "conditions": [], "invalidation_conditions": [], "management_rules": [],
                "configuration_hash": "abc"}
        sig2 = copy.deepcopy(sig1)
        sig2["entry"] = 101.0  # deliberate difference

        with pytest.raises(AssertionError, match="entry"):
            ReplayHarness.assert_deterministic([sig1], [sig2])

    def test_assert_deterministic_fails_on_count_mismatch(self):
        """Different number of signals must raise AssertionError."""
        with pytest.raises(AssertionError, match="Candidate counts"):
            ReplayHarness.assert_deterministic([], [{"signal_id": "SIG-001"}])

    def test_end_time_forwarded_to_pipeline(self):
        """Replay must pass as_of_time to pipeline.run_pipeline (no future data)."""
        harness, mock_pipeline = self._build_harness_with_mock_pipeline([])
        ts = "2026-06-01T12:00:00Z"
        harness.replay_timestamp(ts)
        mock_pipeline.run_pipeline.assert_called_once_with(as_of_time=ts, symbols=None)

    def test_replay_range_returns_all_timestamps(self):
        """replay_range must return one entry per requested timestamp."""
        harness, _ = self._build_harness_with_mock_pipeline([])
        timestamps = ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"]
        results = harness.replay_range(timestamps)
        assert len(results) == 3
        for ts in timestamps:
            assert ts in results

    def test_changing_input_changes_pipeline_call(self):
        """Verifying that a different timestamp causes a different pipeline call."""
        harness, mock_pipeline = self._build_harness_with_mock_pipeline([])
        harness.replay_timestamp("2026-01-01T00:00:00Z")
        harness.replay_timestamp("2026-06-15T08:00:00Z")
        calls = [c.kwargs["as_of_time"] for c in mock_pipeline.run_pipeline.call_args_list]
        assert calls[0] != calls[1]

    def test_no_future_data_after_timestamp(self):
        """
        The pipeline must never be called without an end_time constraint when
        replaying historical data.
        """
        harness, mock_pipeline = self._build_harness_with_mock_pipeline([])
        harness.replay_timestamp("2024-03-15T00:00:00Z")
        call_kwargs = mock_pipeline.run_pipeline.call_args.kwargs
        assert call_kwargs.get("as_of_time") is not None
        assert call_kwargs.get("as_of_time") != ""


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Historical Signal Outcome Evaluator
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutcomeEvaluatorLong:
    """LONG-specific outcome paths."""

    def _eval(self, signal, klines, **kwargs):
        evaluator = SignalOutcomeEvaluator(max_entry_bars=4, max_holding_bars=20, **kwargs)
        return evaluator.evaluate_signal(signal, klines)

    def test_long_no_entry_returns_expired(self):
        """LONG: entry never triggered → EXPIRED."""
        signal = _long_signal()
        # Klines never touch entry (100) from below — all prices stay above 101
        klines = [_make_kline(open_=102, high=105, low=101, close=103)] * 6
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is False
        assert result["expired"] is True
        assert result["first_event"] == "EXPIRED"

    def test_long_entry_triggered_then_tp1_hit(self):
        """LONG: entry triggered (low <= 100), then TP1 (103) hit on next bar."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=103, low=99, close=101),   # entry bar (low 99 <= 100)
            _make_kline(open_=101, high=104, low=100, close=103),  # TP1 bar (high 104 >= 103)
        ]
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is True
        assert result["tp1_hit"] is True
        assert result["first_event"] == "TP1"

    def test_long_tp_progression_tp1_tp2_tp3(self):
        """LONG: full TP1 → TP2 → TP3 progression."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=101, low=99, close=101),    # entry (low 99 <= 100)
            _make_kline(open_=101, high=104, low=100, close=103),   # TP1 hit (103)
            _make_kline(open_=103, high=107, low=103, close=106),   # TP2 hit (106)
            _make_kline(open_=106, high=110, low=106, close=109),   # TP3 hit (109)
        ]
        result = self._eval(signal, klines)
        assert result["tp1_hit"] is True
        assert result["tp2_hit"] is True
        assert result["tp3_hit"] is True
        assert result["sl_hit"] is False
        assert result["realized_r"] > 0

    def test_long_sl_hit_full_loss(self):
        """LONG: SL hit on full position → realized_r == -1.0."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=101, low=99, close=101),   # entry
            _make_kline(open_=99, high=99, low=96, close=97),       # SL hit (low 96 <= 97 SL)
        ]
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is True
        assert result["sl_hit"] is True
        assert result["first_event"] == "SL"
        assert result["realized_r"] == pytest.approx(-1.0)

    def test_long_tp1_then_sl_at_breakeven(self):
        """LONG: after TP1, SL moves to entry (100). Trailing SL hit → net 0 on remainder."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=101, low=99, close=101),    # entry (low 99 <= 100)
            _make_kline(open_=101, high=104, low=101, close=103),   # TP1 hit
            _make_kline(open_=103, high=103, low=99, close=101),    # SL back to entry (100) hit
        ]
        result = self._eval(signal, klines)
        assert result["tp1_hit"] is True
        assert result["tp2_hit"] is False
        assert result["sl_hit"] is True
        # Remaining 50% stopped at breakeven → net R positive (TP1 portion only)
        assert result["realized_r"] > 0

    def test_long_mfe_mae_positive(self):
        """MFE and MAE must be non-negative."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=101, low=99, close=101),  # entry
            _make_kline(open_=101, high=108, low=95, close=101),  # swings both ways
        ]
        result = self._eval(signal, klines)
        if result["entry_triggered"]:
            assert result["mfe"] >= 0
            assert result["mae"] >= 0

    def test_long_fees_reduce_net_r(self):
        """net_realized_r must be less than realized_r (fees applied)."""
        signal = _long_signal()
        klines = [
            _make_kline(open_=101, high=101, low=99, close=101),  # entry
            _make_kline(open_=101, high=110, low=101, close=109), # all TPs hit
            _make_kline(open_=109, high=120, low=109, close=115), # extra room
            _make_kline(open_=115, high=120, low=110, close=115),
        ]
        result = self._eval(signal, klines)
        if result["entry_triggered"] and result["realized_r"] > 0:
            assert result["net_realized_r"] < result["realized_r"]


class TestOutcomeEvaluatorShort:
    """SHORT-specific outcome paths."""

    def _eval(self, signal, klines, **kwargs):
        evaluator = SignalOutcomeEvaluator(max_entry_bars=4, max_holding_bars=20, **kwargs)
        return evaluator.evaluate_signal(signal, klines)

    def test_short_no_entry_returns_expired(self):
        """SHORT: entry never triggered → EXPIRED."""
        signal = _short_signal()
        # Klines never reach entry (100) from above — all prices stay below 99
        klines = [_make_kline(open_=97, high=98, low=95, close=97)] * 6
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is False
        assert result["expired"] is True

    def test_short_entry_triggered_then_tp1_hit(self):
        """SHORT: entry triggered (high >= 100), then TP1 (97) hit."""
        signal = _short_signal()
        klines = [
            _make_kline(open_=99, high=101, low=99, close=100),   # entry (high 101 >= 100)
            _make_kline(open_=100, high=100, low=96, close=97),   # TP1 (low 96 <= 97)
        ]
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is True
        assert result["tp1_hit"] is True

    def test_short_sl_hit_full_loss(self):
        """SHORT: SL (103) hit → realized_r == -1.0."""
        signal = _short_signal()
        klines = [
            _make_kline(open_=99, high=101, low=99, close=100),   # entry
            _make_kline(open_=100, high=104, low=100, close=103), # SL hit (high 104 >= 103)
        ]
        result = self._eval(signal, klines)
        assert result["entry_triggered"] is True
        assert result["sl_hit"] is True
        assert result["realized_r"] == pytest.approx(-1.0)

    def test_short_full_tp_progression(self):
        """SHORT: TP1 → TP2 → TP3 full progression."""
        signal = _short_signal()
        klines = [
            _make_kline(open_=99, high=101, low=99, close=100),    # entry
            _make_kline(open_=100, high=100, low=96, close=97),    # TP1 (97)
            _make_kline(open_=97, high=97, low=93, close=94),      # TP2 (94)
            _make_kline(open_=94, high=94, low=90, close=91),      # TP3 (91)
        ]
        result = self._eval(signal, klines)
        assert result["tp3_hit"] is True
        assert result["realized_r"] > 0

    def test_short_sl_above_entry(self):
        """SHORT geometry: SL must be above entry."""
        signal = _short_signal()
        assert signal["stop_loss"] > signal["entry"]


class TestAmbiguousCandle:
    """
    Tests the resolution policy for candles where both TP and SL are touched.
    Per the PRD: must NOT silently assume the favorable event happened first.
    """

    def _eval_with_ambiguous(self, side, resolution):
        if side == "LONG":
            signal = _long_signal()
            # Bar where both SL (97) and TP1 (103) are touched
            candles = [
                _make_kline(open_=101, high=101, low=99, close=101),  # entry
                _make_kline(open_=100, high=104, low=96, close=100),  # ambiguous: TP1 & SL both touched
            ]
        else:
            signal = _short_signal()
            candles = [
                _make_kline(open_=99, high=101, low=99, close=100),   # entry
                _make_kline(open_=100, high=104, low=96, close=100),  # ambiguous: SL (103) & TP1 (97) both touched
            ]
        evaluator = SignalOutcomeEvaluator(
            max_entry_bars=2,
            max_holding_bars=5,
            ambiguous_resolution=resolution,
        )
        return evaluator.evaluate_signal(signal, candles)

    def test_conservative_resolution_long_sl_first(self):
        """CONSERVATIVE policy: SL hit first on ambiguous LONG candle."""
        result = self._eval_with_ambiguous("LONG", AmbiguousResolution.CONSERVATIVE)
        if result["entry_triggered"]:
            # Conservative = SL before TP
            assert result["first_event"] == "SL"

    def test_optimistic_resolution_long_tp_first(self):
        """OPTIMISTIC policy: TP hit first on ambiguous LONG candle."""
        result = self._eval_with_ambiguous("LONG", AmbiguousResolution.OPTIMISTIC)
        if result["entry_triggered"]:
            assert result["first_event"] == "TP1"

    def test_conservative_resolution_short_sl_first(self):
        """CONSERVATIVE policy: SL hit first on ambiguous SHORT candle."""
        result = self._eval_with_ambiguous("SHORT", AmbiguousResolution.CONSERVATIVE)
        if result["entry_triggered"]:
            assert result["first_event"] == "SL"

    def test_open_proximity_resolution(self):
        """OPEN_PROXIMITY: whichever of TP/SL is closer to candle open is hit first."""
        # LONG: entry 100, TP1=103, SL=97, candle open=101
        # dist_to_TP=2, dist_to_SL=4 → TP closer → TP first
        signal = _long_signal()
        candles = [
            _make_kline(open_=101, high=101, low=99, close=101),           # entry
            _make_kline(open_=101, high=104, low=96, close=100),           # ambiguous (open=101)
        ]
        evaluator = SignalOutcomeEvaluator(
            max_entry_bars=2,
            max_holding_bars=5,
            ambiguous_resolution=AmbiguousResolution.OPEN_PROXIMITY,
        )
        result = evaluator.evaluate_signal(signal, candles)
        if result["entry_triggered"]:
            # open=101, dist_to_TP1(103)=2, dist_to_SL(97)=4 → TP1 closer
            assert result["first_event"] == "TP1"

    def test_conservative_is_default(self):
        """Default resolution policy must be CONSERVATIVE (risk-first principle)."""
        ev = SignalOutcomeEvaluator()
        assert ev.ambiguous_resolution == AmbiguousResolution.CONSERVATIVE


class TestOutcomeEvaluatorEdgeCases:
    def test_batch_evaluation(self):
        """evaluate_batch runs multiple signals."""
        ev = SignalOutcomeEvaluator(max_entry_bars=3, max_holding_bars=10)
        signals = [_long_signal(), _short_signal()]
        market_klines = {
            "TESTUSDT": [_make_kline(open_=101, high=105, low=96, close=100)] * 10
        }
        results = ev.evaluate_batch(signals, market_klines)
        assert len(results) == 2

    def test_empty_klines_returns_expired(self):
        """No subsequent klines → signal must expire."""
        ev = SignalOutcomeEvaluator(max_entry_bars=3, max_holding_bars=10)
        result = ev.evaluate_signal(_long_signal(), [])
        assert result["entry_triggered"] is False
        assert result["expired"] is True

    def test_max_entry_bars_respected(self):
        """Entry is never triggered if all bars within max_entry_bars are out of range."""
        ev = SignalOutcomeEvaluator(max_entry_bars=2, max_holding_bars=10)
        signal = _long_signal()  # entry at 100
        # 10 bars all above 100
        klines = [_make_kline(open_=101, high=105, low=101, close=103)] * 10
        result = ev.evaluate_signal(signal, klines)
        assert result["entry_triggered"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Threshold Tuner / Calibration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerformanceMetrics:
    """Tests for the ThresholdTuner.calculate_metrics method."""

    def _tuner(self):
        return ThresholdTuner()

    def test_empty_evaluations(self):
        m = self._tuner().calculate_metrics([])
        assert m.signals_total == 0
        assert m.win_rate == 0.0

    def test_all_winners(self):
        evals = [_eval_result(tp1=True, net_r=1.0, first_event="TP1")] * 5
        m = self._tuner().calculate_metrics(evals, num_days=1.0)
        assert m.win_rate == pytest.approx(1.0)
        assert m.expected_r > 0

    def test_all_losers(self):
        evals = [_eval_result(sl=True, net_r=-1.0, first_event="SL")] * 5
        m = self._tuner().calculate_metrics(evals, num_days=1.0)
        assert m.win_rate == pytest.approx(0.0)
        assert m.profit_factor == 0.0

    def test_mixed_signals(self):
        evals = (
            [_eval_result(tp1=True, net_r=1.5, first_event="TP1")] * 3
            + [_eval_result(sl=True, net_r=-1.0, first_event="SL")] * 2
        )
        m = self._tuner().calculate_metrics(evals, num_days=2.0)
        assert m.signals_total == 5
        assert 0 < m.win_rate < 1
        assert m.profit_factor > 0
        assert m.average_r != 0.0

    def test_signals_per_day(self):
        evals = [_eval_result(net_r=0.5)] * 6
        m = self._tuner().calculate_metrics(evals, num_days=2.0)
        assert m.signals_per_day == pytest.approx(3.0)

    def test_worst_losing_streak(self):
        evals = (
            [_eval_result(net_r=1.0)] * 2
            + [_eval_result(sl=True, net_r=-1.0)] * 4  # streak of 4
            + [_eval_result(net_r=1.0)]
        )
        m = self._tuner().calculate_metrics(evals, num_days=1.0)
        assert m.worst_losing_streak == 4

    def test_max_drawdown_non_negative(self):
        evals = [_eval_result(net_r=-1.0)] * 3 + [_eval_result(net_r=2.0)]
        m = self._tuner().calculate_metrics(evals, num_days=1.0)
        assert m.max_drawdown_r >= 0

    def test_non_entered_signals_excluded_from_rates(self):
        """Signals where entry was never triggered should not count in hit rates."""
        evals = (
            [_eval_result(entry_triggered=False)] * 5
            + [_eval_result(entry_triggered=True, tp1=True, net_r=1.0)] * 5
        )
        m = self._tuner().calculate_metrics(evals, num_days=1.0)
        assert m.entry_rate == pytest.approx(0.5)
        assert m.tp1_hit_rate == pytest.approx(1.0)  # all entered ones hit TP1


class TestDatasetSplit:
    def test_valid_chronological_split(self):
        split = DatasetSplit(
            train_timestamps=[1, 2, 3],
            validation_timestamps=[4, 5],
            test_timestamps=[6, 7],
        )
        assert split.validate_chronology() is True

    def test_overlapping_train_validation_raises(self):
        split = DatasetSplit(
            train_timestamps=[1, 5],
            validation_timestamps=[4, 6],
            test_timestamps=[7],
        )
        with pytest.raises(ValueError, match="Train/Validation"):
            split.validate_chronology()

    def test_overlapping_validation_test_raises(self):
        split = DatasetSplit(
            train_timestamps=[1, 2],
            validation_timestamps=[3, 7],
            test_timestamps=[6, 8],
        )
        with pytest.raises(ValueError, match="Validation/Test"):
            split.validate_chronology()


class TestParameterSweep:
    def test_sweep_returns_results_sorted_by_expected_r(self):
        """Sweep must return rows sorted by expected_r descending."""
        tuner = ThresholdTuner()
        base_config = AppConfig()

        # Stub evaluations_provider: returns results that vary by volume threshold
        def mock_provider(cfg):
            # Higher min_ratio → fewer signals (2 entries)
            if cfg.volume.min_ratio >= 2.0:
                return [_eval_result(tp1=True, net_r=2.0)] * 2 + [_eval_result(sl=True, net_r=-1.0)]
            else:
                return [_eval_result(sl=True, net_r=-1.0)] * 5

        results = tuner.parameter_sweep(
            base_config=base_config,
            param_grid={"volume.min_ratio": [1.5, 2.0, 2.5]},
            evaluations_provider=mock_provider,
            num_days=10.0,
            sort_by="expected_r",
        )
        assert len(results) == 3
        # Best expected_r should be first
        assert results[0]["expected_r"] >= results[-1]["expected_r"]

    def test_sweep_applies_params_to_config(self):
        """Sweep must correctly apply dot-notation params to config."""
        tuner = ThresholdTuner()
        base_config = AppConfig()
        seen_ratios = []

        def mock_provider(cfg):
            seen_ratios.append(cfg.volume.min_ratio)
            return []

        tuner.parameter_sweep(
            base_config=base_config,
            param_grid={"volume.min_ratio": [1.0, 3.0]},
            evaluations_provider=mock_provider,
            num_days=1.0,
        )
        assert 1.0 in seen_ratios
        assert 3.0 in seen_ratios

    def test_sweep_does_not_mutate_base_config(self):
        """parameter_sweep must not modify the original base_config."""
        tuner = ThresholdTuner()
        base_config = AppConfig()
        original_ratio = base_config.volume.min_ratio

        tuner.parameter_sweep(
            base_config=base_config,
            param_grid={"volume.min_ratio": [9.9]},
            evaluations_provider=lambda cfg: [],
            num_days=1.0,
        )
        assert base_config.volume.min_ratio == original_ratio

    def test_sweep_invalid_param_key_raises(self):
        """Bad param_grid key format must raise ValueError."""
        tuner = ThresholdTuner()
        base_config = AppConfig()

        with pytest.raises(ValueError, match="section.field"):
            tuner.parameter_sweep(
                base_config=base_config,
                param_grid={"invalid_key_no_dot": [1.0]},
                evaluations_provider=lambda cfg: [],
                num_days=1.0,
            )

    def test_sweep_unknown_section_raises(self):
        tuner = ThresholdTuner()
        with pytest.raises(ValueError, match="no section"):
            tuner.parameter_sweep(
                base_config=AppConfig(),
                param_grid={"nonexistent.field": [1.0]},
                evaluations_provider=lambda cfg: [],
                num_days=1.0,
            )

    def test_sweep_report_markdown_contains_all_metrics(self):
        """sweep_report_markdown must include the required metric columns."""
        tuner = ThresholdTuner()
        sweep_results = [
            {
                "param_label": "volume.min_ratio=1.5",
                "signals_total": 10, "signals_per_day": 1.0, "entry_rate": 0.8,
                "tp1_hit_rate": 0.6, "tp2_hit_rate": 0.4, "tp3_hit_rate": 0.2,
                "sl_rate": 0.3, "win_rate": 0.6, "average_r": 0.5,
                "expected_r": 0.4, "profit_factor": 1.8, "max_drawdown_r": 2.0,
                "worst_losing_streak": 2, "avg_holding_bars": 15.0,
            }
        ]
        report = tuner.sweep_report_markdown(sweep_results)
        assert "expected_r" in report
        assert "win_rate" in report
        assert "profit_factor" in report
        assert "volume.min_ratio=1.5" in report

    def test_walk_forward_splits_chronologically(self):
        """walk_forward_evaluate must not shuffle — first window is earliest data."""
        tuner = ThresholdTuner()
        # 12 evaluations (windows of 4)
        evals = [_eval_result(net_r=float(i)) for i in range(12)]
        results = tuner.walk_forward_evaluate(evals, n_windows=3, train_ratio=0.6)
        assert len(results) == 3
        for r in results:
            assert "train_metrics" in r
            assert "test_metrics" in r
            assert r["train_n"] > 0

    def test_walk_forward_too_few_evals_returns_empty(self):
        tuner = ThresholdTuner()
        results = tuner.walk_forward_evaluate([_eval_result()], n_windows=3)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5 — Signal Lifecycle Tracking
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalLifecycle:
    """Tests for SignalLifecycleTracker state machine."""

    def _tracker(self, state=None):
        return SignalLifecycleTracker(
            signal_id="SIG-TEST-001",
            initial_state=state or SignalState.GENERATED,
        )

    # ── Valid full progressions ────────────────────────────────────────────────

    def test_full_long_winning_path(self):
        """GENERATED → WAITING_FOR_ENTRY → ACTIVE → TP1_HIT → TP2_HIT → TP3_HIT"""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "order placed")
        t.transition_to(SignalState.ACTIVE, "entry filled")
        t.transition_to(SignalState.TP1_HIT, "TP1 reached")
        t.transition_to(SignalState.TP2_HIT, "TP2 reached")
        t.transition_to(SignalState.TP3_HIT, "TP3 reached")
        assert t.current_state == SignalState.TP3_HIT
        assert t.is_terminal is True

    def test_stopped_out_path(self):
        """ACTIVE → STOPPED_OUT is valid."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "ready")
        t.transition_to(SignalState.ACTIVE, "filled")
        t.transition_to(SignalState.STOPPED_OUT, "SL hit")
        assert t.current_state == SignalState.STOPPED_OUT
        assert t.is_terminal is True

    def test_expired_path(self):
        """WAITING_FOR_ENTRY → EXPIRED is valid."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "waiting")
        t.transition_to(SignalState.EXPIRED, "24h elapsed")
        assert t.current_state == SignalState.EXPIRED
        assert t.is_terminal is True

    def test_invalidated_from_generated(self):
        """GENERATED → INVALIDATED is valid (pre-entry invalidation)."""
        t = self._tracker()
        t.transition_to(SignalState.INVALIDATED, "BTC regime flipped")
        assert t.current_state == SignalState.INVALIDATED

    def test_invalidated_from_waiting(self):
        """WAITING_FOR_ENTRY → INVALIDATED is valid."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "ready")
        t.transition_to(SignalState.INVALIDATED, "15m close below SL")
        assert t.current_state == SignalState.INVALIDATED

    def test_invalidated_from_active(self):
        """ACTIVE → INVALIDATED is valid."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "ready")
        t.transition_to(SignalState.ACTIVE, "filled")
        t.transition_to(SignalState.INVALIDATED, "structure broken")
        assert t.current_state == SignalState.INVALIDATED

    def test_tp1_then_stopped_out_at_breakeven(self):
        """TP1_HIT → STOPPED_OUT (trailing/breakeven stop) is valid."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "ok")
        t.transition_to(SignalState.ACTIVE, "filled")
        t.transition_to(SignalState.TP1_HIT, "TP1 hit")
        t.transition_to(SignalState.STOPPED_OUT, "breakeven stop")
        assert t.current_state == SignalState.STOPPED_OUT

    # ── Invalid transitions ────────────────────────────────────────────────────

    def test_cannot_skip_waiting_to_active(self):
        """GENERATED → ACTIVE is illegal (must go via WAITING_FOR_ENTRY)."""
        t = self._tracker()
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            t.transition_to(SignalState.ACTIVE, "skip")

    def test_cannot_go_back_from_terminal(self):
        """TP3_HIT (terminal) → any state must fail."""
        t = self._tracker(state=SignalState.TP3_HIT)
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            t.transition_to(SignalState.GENERATED, "reset")

    def test_expired_is_terminal(self):
        t = self._tracker(state=SignalState.EXPIRED)
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            t.transition_to(SignalState.ACTIVE, "try again")

    def test_stopped_out_is_terminal(self):
        t = self._tracker(state=SignalState.STOPPED_OUT)
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            t.transition_to(SignalState.TP1_HIT, "wishful thinking")

    def test_cannot_jump_tp1_to_tp3(self):
        """TP1_HIT → TP3_HIT skips TP2 — illegal."""
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "ok")
        t.transition_to(SignalState.ACTIVE, "filled")
        t.transition_to(SignalState.TP1_HIT, "TP1")
        with pytest.raises(ValueError, match="Illegal lifecycle transition"):
            t.transition_to(SignalState.TP3_HIT, "skip TP2")

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_idempotent_same_state_no_error(self):
        """Transitioning to the current state is a no-op (idempotent)."""
        t = self._tracker()
        result = t.transition_to(SignalState.GENERATED, "replay")
        assert result is True
        assert t.current_state == SignalState.GENERATED

    def test_idempotent_does_not_add_history_entry(self):
        """Idempotent transition must not add a new history entry."""
        t = self._tracker()
        initial_history_len = len(t.history)
        t.transition_to(SignalState.GENERATED, "duplicate")
        assert len(t.history) == initial_history_len

    # ── History & Serialization ───────────────────────────────────────────────

    def test_history_records_all_transitions(self):
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "r1")
        t.transition_to(SignalState.ACTIVE, "r2")
        # history: [GENERATED init entry] + [WAITING_FOR_ENTRY] + [ACTIVE]
        assert len(t.history) == 3

    def test_to_dict_contains_required_keys(self):
        t = self._tracker()
        d = t.to_dict()
        assert "signal_id" in d
        assert "current_state" in d
        assert "is_terminal" in d
        assert "history" in d

    def test_repeated_evaluation_idempotent(self):
        """
        Evaluating the same transition twice should not change the state
        or corrupt history (idempotency requirement).
        """
        t = self._tracker()
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "first call")
        state_after_first = t.current_state
        t.transition_to(SignalState.WAITING_FOR_ENTRY, "second call — idempotent")
        assert t.current_state == state_after_first


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6 — Scheduler
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerDuplicatePrevention:
    """Tests for BotScheduler duplicate suppression logic (no network required)."""

    def _scheduler(self, cooldown_hours=6.0):
        with patch("src.scheduler.SignalPipeline"), \
             patch("src.scheduler.TelegramBot"), \
             patch("src.scheduler.SignalPersistence"):
            sched = BotScheduler(cooldown_hours=cooldown_hours, state_file="data_store/test_state.json")
        return sched

    def test_duplicate_within_cooldown_is_suppressed(self):
        """Signal published < cooldown_hours ago is a duplicate."""
        sched = self._scheduler(cooldown_hours=6.0)
        now_utc = datetime.now(timezone.utc)
        recent_ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {"published_signals": {"BTCUSDT_LONG": recent_ts}}
        assert sched.is_duplicate("BTCUSDT", "LONG", state) is True

    def test_duplicate_after_cooldown_is_allowed(self):
        """Signal published > cooldown_hours ago is NOT a duplicate."""
        sched = self._scheduler(cooldown_hours=1.0)
        old_ts = "2020-01-01T00:00:00Z"  # way in the past
        state = {"published_signals": {"ETHUSDT_SHORT": old_ts}}
        assert sched.is_duplicate("ETHUSDT", "SHORT", state) is False

    def test_no_previous_entry_not_duplicate(self):
        """Symbol/side not in state → not a duplicate."""
        sched = self._scheduler()
        state = {"published_signals": {}}
        assert sched.is_duplicate("SOLUSDT", "LONG", state) is False

    def test_different_side_not_duplicate(self):
        """Same symbol but different side is not considered a duplicate."""
        sched = self._scheduler(cooldown_hours=6.0)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {"published_signals": {"BTCUSDT_LONG": now_ts}}
        # LONG is a dup, but SHORT is not
        assert sched.is_duplicate("BTCUSDT", "LONG", state) is True
        assert sched.is_duplicate("BTCUSDT", "SHORT", state) is False

    def test_scan_cycle_suppresses_duplicate_on_invalid_signal(self):
        """
        If pipeline returns a candidate that fails SignalModel validation,
        it must not be published (no Telegram call).
        """
        with patch("src.scheduler.SignalPipeline") as MockPipeline, \
             patch("src.scheduler.TelegramBot") as MockBot, \
             patch("src.scheduler.SignalPersistence"):

            mock_pipeline_instance = MockPipeline.return_value
            # Return a dict missing required fields — SignalModel will reject it
            mock_pipeline_instance.run_pipeline.return_value = [
                {"symbol": "BADUSDT", "side": "LONG"}  # missing many required fields
            ]
            sched = BotScheduler(state_file="data_store/test_state_invalid.json")
            sched.pipeline = mock_pipeline_instance

            published = sched.run_scan_cycle()
            # Should return empty because SignalModel validation fails
            assert published == []
            MockBot.return_value.send_message.assert_not_called()

    def test_scan_cycle_api_failure_returns_empty(self):
        """Transient pipeline failure must not crash — returns []."""
        with patch("src.scheduler.SignalPipeline"), \
             patch("src.scheduler.TelegramBot"), \
             patch("src.scheduler.SignalPersistence"):

            sched = BotScheduler(state_file="data_store/test_state_err.json")
            sched.pipeline = MagicMock()
            sched.pipeline.run_pipeline.side_effect = ConnectionError("API timeout")

            result = sched.run_scan_cycle(max_retries=2, retry_delay=0.0)
            assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# Risk Engine – additional math coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineMath:
    """Additional risk math tests to cover position sizing and margin."""

    from src.engines.risk_engine import RiskEngine

    def test_position_notional_correct(self):
        from src.engines.risk_engine import RiskEngine
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0, max_leverage=20)
        # risk_per_trade = 10 USDT, risk_distance = 5 USDT
        # position_size = 10 / 5 = 2 contracts, notional = 2 * 100 = 200
        result = engine.calculate_risk_parameters(100.0, 95.0, {"tp1": 110.0})
        assert result["position_notional"] == pytest.approx(200.0)

    def test_margin_equals_notional_over_leverage(self):
        from src.engines.risk_engine import RiskEngine
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0, max_leverage=20)
        result = engine.calculate_risk_parameters(100.0, 95.0, {"tp1": 110.0})
        expected_margin = result["position_notional"] / result["leverage"]
        assert result["margin"] == pytest.approx(expected_margin, rel=1e-3)

    def test_max_leverage_hard_cap(self):
        from src.engines.risk_engine import RiskEngine
        engine = RiskEngine(account_size=10_000.0, max_risk_pct=1.0, max_leverage=3)
        # Very tight SL → requires high leverage, should be capped at 3
        result = engine.calculate_risk_parameters(1000.0, 999.9, {"tp1": 1001.0})
        assert result["leverage"] <= 3

    def test_risk_amount_equals_account_times_risk_pct(self):
        from src.engines.risk_engine import RiskEngine
        engine = RiskEngine(account_size=500.0, max_risk_pct=2.0, max_leverage=10)
        result = engine.calculate_risk_parameters(100.0, 95.0, {"tp1": 110.0})
        assert result["risk_amount"] == pytest.approx(10.0)  # 500 * 2% = 10

    def test_short_rr_calculated_correctly(self):
        from src.engines.risk_engine import RiskEngine
        engine = RiskEngine(account_size=1000.0, max_risk_pct=1.0, max_leverage=10)
        # SHORT: entry=100, SL=105, TP1=95 → risk=5, reward=5 → R:R=1.0
        result = engine.calculate_risk_parameters(100.0, 105.0, {"tp1": 95.0})
        assert result["risk_reward_ratios"]["rr_tp1"] == pytest.approx(1.0)


# ════════════════════════════════════════════════════════════════════════════════
# Calibration Round 1 — Regression Tests
# ════════════════════════════════════════════════════════════════════════════════

class TestTPRRValidation:
    """Tests for TP/RR configuration validation (Phase 1)."""

    def test_valid_tp_rr_configuration(self):
        """Valid TP ladder with TP1 >= min_rr_tp1 should pass."""
        cfg = AppConfig()
        cfg.tp.r_multiples = (2.0, 3.0, 4.0)
        cfg.risk.min_rr_tp1 = 2.0
        cfg.validate()  # Should not raise

    def test_invalid_tp1_below_min_rr(self):
        """TP1 < min_rr_tp1 should raise ValueError."""
        cfg = AppConfig()
        cfg.tp.r_multiples = (1.5, 2.5, 3.5)
        cfg.risk.min_rr_tp1 = 2.0
        with pytest.raises(ValueError, match="TP1 R-multiple.*min_rr_tp1"):
            cfg.validate()

    def test_tp2_tp3_not_validated_against_min_rr(self):
        """Only TP1 is validated against min_rr_tp1; TP2/TP3 can be anything."""
        cfg = AppConfig()
        cfg.tp.r_multiples = (2.0, 1.5, 1.0)  # TP2/TP3 < min_rr_tp1 is allowed
        cfg.risk.min_rr_tp1 = 2.0
        cfg.validate()  # Should not raise - only TP1 matters

    def test_empty_tp_ladder_raises(self):
        """Empty TP ladder should raise."""
        cfg = AppConfig()
        cfg.tp.r_multiples = ()
        cfg.risk.min_rr_tp1 = 2.0
        with pytest.raises(ValueError, match="TP1 R-multiple"):
            cfg.validate()


class TestOITriStateSemantics:
    """Tests for OI CONFIRMED/REJECTED/UNKNOWN semantics (Phase 2)."""

    def _oi_bullish_hist(self):
        """OI increasing with price up → CONFIRMED."""
        return [
            {"sumOpenInterestValue": "1000"},
            {"sumOpenInterestValue": "1100"},
        ]

    def _oi_bearish_hist(self):
        """OI increasing with price down → CONFIRMED."""
        return [
            {"sumOpenInterestValue": "1000"},
            {"sumOpenInterestValue": "1100"},
        ]

    def _oi_divergence_hist(self):
        """Price up, OI down → REJECTED."""
        return [
            {"sumOpenInterestValue": "1000"},
            {"sumOpenInterestValue": "950"},
        ]

    def _oi_empty_hist(self):
        """No OI data → UNKNOWN."""
        return []

    def test_confirmed_bullish(self):
        """Price up + OI up = CONFIRMED bullish."""
        engine = OIEngine(min_oi_change_pct=2.0)
        result = engine.analyze_oi(self._oi_bullish_hist(), "up")
        assert result["status"] == "CONFIRMED"
        assert result["signal"] == "bullish"
        assert result["reason"] == "oi_increasing_with_price"

    def test_confirmed_bearish(self):
        """Price down + OI up = CONFIRMED bearish."""
        engine = OIEngine(min_oi_change_pct=2.0)
        result = engine.analyze_oi(self._oi_bearish_hist(), "down")
        assert result["status"] == "CONFIRMED"
        assert result["signal"] == "bearish"
        assert result["reason"] == "oi_increasing_against_price"

    def test_rejected_divergence(self):
        """Price up + OI down = REJECTED (divergence)."""
        engine = OIEngine(min_oi_change_pct=2.0)
        result = engine.analyze_oi(self._oi_divergence_hist(), "up")
        assert result["status"] == "REJECTED"
        assert result["signal"] == "bearish_divergence"
        assert result["divergence"] is True
        assert result["reason"] == "oi_decreasing_with_price_up"

    def test_unknown_insufficient_data(self):
        """Empty OI history = UNKNOWN."""
        engine = OIEngine(min_oi_change_pct=2.0)
        result = engine.analyze_oi(self._oi_empty_hist(), "up")
        assert result["status"] == "UNKNOWN"
        assert result["signal"] == "neutral"
        assert result["reason"] == "insufficient_oi_data"

    def test_unknown_below_threshold(self):
        """OI change below threshold = UNKNOWN."""
        engine = OIEngine(min_oi_change_pct=5.0)
        # Only 2% change, below 5% threshold
        hist = [
            {"sumOpenInterestValue": "1000"},
            {"sumOpenInterestValue": "1020"},
        ]
        result = engine.analyze_oi(hist, "up")
        assert result["status"] == "UNKNOWN"
        assert result["reason"] == "oi_change_below_threshold"

    def test_oi_required_mode_rejects_unknown(self):
        """OI_REQUIRED mode should reject UNKNOWN."""
        engine = OIEngine(min_oi_change_pct=2.0, oi_mode="required")
        result = engine.analyze_oi(self._oi_empty_hist(), "up")
        assert engine.should_reject_candidate(result) is True

    def test_oi_required_mode_accepts_confirmed(self):
        """OI_REQUIRED mode should accept CONFIRMED."""
        engine = OIEngine(min_oi_change_pct=2.0, oi_mode="required")
        result = engine.analyze_oi(self._oi_bullish_hist(), "up")
        assert engine.should_reject_candidate(result) is False

    def test_oi_optional_mode_never_rejects(self):
        """OI_OPTIONAL mode never rejects."""
        engine = OIEngine(min_oi_change_pct=2.0, oi_mode="optional")
        result = engine.analyze_oi(self._oi_empty_hist(), "up")
        assert engine.should_reject_candidate(result) is False
        result = engine.analyze_oi(self._oi_divergence_hist(), "up")
        assert engine.should_reject_candidate(result) is False


class TestSLLookbackIntegration:
    """Tests for structural SL lookback configuration."""

    def test_sl_lookback_in_config(self):
        """RiskConfig should have sl_lookback field."""
        cfg = AppConfig()
        assert hasattr(cfg.risk, "sl_lookback")
        assert cfg.risk.sl_lookback == 20

    def test_pipeline_uses_config_sl_lookback(self):
        """Pipeline should pass sl_lookback from config to StopLossEngine."""
        from src.pipeline import SignalPipeline
        from unittest.mock import MagicMock
        
        cfg = AppConfig()
        cfg.risk.sl_lookback = 30
        
        # Create pipeline with mock client
        mock_client = MagicMock()
        mock_client.get_open_interest_hist.return_value = []
        mock_client.get_funding_rate.return_value = []
        
        pipeline = SignalPipeline(config=cfg, client=mock_client)
        
        # The sl_engine should have the configured lookback
        assert pipeline.sl_engine.structural_lookback == 30
