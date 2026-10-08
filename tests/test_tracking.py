"""Tests for the live outcome tracker (results ledger for future analysis)."""
import json
import os
import time

from src.tracking.outcome_tracker import OutcomeTracker


def _raw_row(ts, o, h, l, c, v=1000.0):
    return [ts, str(o), str(h), str(l), str(c), str(v), ts + 1, "0", "0", "0", "0", "0"]


class StubClient:
    def __init__(self, rows):
        self._rows = rows

    def get_klines(self, symbol, interval, limit=500, end_time=None):
        return self._rows


def _signal(ts_ms, entry=100.0, sl=97.0, tp1=103.0, tp2=106.0, tp3=109.0, side="LONG"):
    return {
        "signal_id": "SIG-TEST-1",
        "timestamp": ts_ms,
        "symbol": "TESTUSDT",
        "side": side,
        "strategy_id": "donchian_20",
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop_loss": sl,
    }


def test_tp1_hit_is_tracked_not_final():
    t0 = int(time.time() * 1000) - 3 * 3600 * 1000  # 3h ago -> 12 bars elapsed
    rows = [
        _raw_row(t0 + 900000, 99.0, 101.0, 98.5, 100.0),    # touches entry 100
        _raw_row(t0 + 1800000, 100.0, 103.5, 99.5, 102.0),  # hits TP1 103
    ]
    tr = OutcomeTracker(client=StubClient(rows))
    ledger = {}
    rec = tr.track_signal(_signal(t0), ledger)
    assert rec["tp1_hit"] is True
    assert rec["entry_triggered"] is True
    assert rec["status"] == "TP1_HIT"
    assert rec["final"] is False  # trade still open, not terminal
    assert ledger["SIG-TEST-1"]["net_realized_r"] == rec["net_realized_r"]


def test_stop_out_is_terminal():
    t0 = 1700000000000
    rows = [_raw_row(t0 + 900000, 99.0, 100.5, 96.0, 98.0)]  # entry then SL 97
    tr = OutcomeTracker(client=StubClient(rows))
    rec = tr.track_signal(_signal(t0), {})
    assert rec["sl_hit"] is True
    assert rec["status"] == "STOPPED_OUT"
    assert rec["final"] is True


def test_young_signal_without_entry_is_never_finalized_early():
    now_ms = int(time.time() * 1000)
    t0 = now_ms - 30 * 60 * 1000  # 30 min ago -> ~2 bars elapsed
    rows = [
        _raw_row(t0 + 900000, 105.0, 106.0, 104.0, 105.5),
        _raw_row(t0 + 1800000, 105.5, 106.5, 104.5, 106.0),
        _raw_row(t0 + 2700000, 106.0, 107.0, 105.0, 106.5),
    ]
    tr = OutcomeTracker(client=StubClient(rows))
    rec = tr.track_signal(_signal(t0), {})
    assert rec["entry_triggered"] is False
    assert rec["final"] is False  # only 3 future bars < max_entry_bars=6


def test_liq_estimator_long_short():
    from src.papertrade.liquidation import estimate_liq_price, liq_distance_pct
    liq_long = estimate_liq_price(100.0, "LONG", 10)
    assert abs(liq_long - 90.4) < 1e-9
    liq_short = estimate_liq_price(100.0, "SHORT", 10)
    assert abs(liq_short - 109.6) < 1e-9
    assert estimate_liq_price(100.0, "LONG", 0) is None
    assert estimate_liq_price(0, "LONG", 10) is None
    assert abs(liq_distance_pct(100.0, 90.4, "LONG") - 9.6) < 1e-9


def test_liquidation_overrides_stop_when_liq_first():
    from src.backtest.evaluator import SignalOutcomeEvaluator
    t0 = 1700000000000
    # SL at 85 is WIDER than liq 90.4 -> liq must win
    sig = _signal(t0, entry=100.0, sl=85.0, tp1=130.0, tp2=145.0, tp3=160.0)
    sig.update({"leverage": 10, "margin": 124.4, "position_notional": 1244.0})
    rows = [
        _raw_row(t0 + 900000, 99.0, 101.0, 98.0, 100.0),   # entry
        _raw_row(t0 + 1800000, 95.0, 96.0, 89.0, 92.0),    # touches liq 90.4 (SL 85 untouched)
    ]
    ev = SignalOutcomeEvaluator().evaluate_signal(sig, [
        {"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows
    ])
    assert ev["liquidated"] is True
    assert ev["status"] == "LIQUIDATED"
    assert ev["first_event"] == "LIQ"
    assert ev["sl_hit"] is False
    assert abs(ev["liq_price"] - 90.4) < 1e-9
    # loss = full margin in R: -124.4 / (15 * 12.44)
    assert abs(ev["realized_r"] - (-124.4 / (15.0 * 12.44))) < 1e-3


def test_no_leverage_means_no_liq_modeling():
    from src.backtest.evaluator import SignalOutcomeEvaluator
    t0 = 1700000000000
    sig = _signal(t0, entry=100.0, sl=85.0, tp1=130.0, tp2=145.0, tp3=160.0)
    rows = [
        _raw_row(t0 + 900000, 99.0, 101.0, 98.0, 100.0),
        _raw_row(t0 + 1800000, 95.0, 96.0, 89.0, 92.0),
    ]
    ev = SignalOutcomeEvaluator().evaluate_signal(sig, [
        {"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows
    ])
    assert ev["liquidated"] is False
    assert ev["liq_price"] is None


def test_holding_timer_final_and_open():
    from src.tracking.outcome_tracker import format_duration, holding_info
    assert format_duration(0) == "0m"
    assert format_duration(45 * 60000) == "45m"
    assert format_duration((2 * 3600 + 1800) * 1000) == "2h 30m"
    assert format_duration((26 * 3600) * 1000) == "1d 2h 0m"
    entry = 1700000000000
    closed = holding_info(entry, 8, True, entry + 10 * 3600000)
    assert closed["holding_minutes"] == 120
    assert closed["holding_time"] == "2h 0m"
    assert closed["close_timestamp"] == "2023-11-15T00:13:20Z"
    live = holding_info(entry, 8, False, entry + 90 * 60000)
    assert live["holding_minutes"] == 90.0
    assert live["holding_time"] == "1h 30m (open)"
    assert live["close_timestamp"] is None
    empty = holding_info(None, 0, False, entry)
    assert empty["holding_time"] is None


def test_tracked_record_carries_holding_fields():
    t0 = int(__import__("time").time() * 1000) - 3 * 3600 * 1000
    rows = [
        _raw_row(t0 + 900000, 99.0, 101.0, 98.5, 100.0),
        _raw_row(t0 + 1800000, 100.0, 103.5, 99.5, 102.0),
    ]
    tr = OutcomeTracker(client=StubClient(rows))
    rec = tr.track_signal(_signal(t0), {})
    assert rec["holding_minutes"] is not None
    assert "(open)" in rec["holding_time"]
    assert rec["close_timestamp"] is None


def test_track_all_persists_ledger(tmp_path):
    t0 = 1700000000000
    rows = [_raw_row(t0 + 900000, 99.0, 100.5, 96.0, 98.0)]
    store = str(tmp_path)
    with open(os.path.join(store, "signals_2024-01-01.json"), "w", encoding="utf-8") as f:
        json.dump([_signal(t0)], f)
    tr = OutcomeTracker(client=StubClient(rows), storage_dir=store)
    summary = tr.track_all()
    assert summary["signals_total"] == 1
    assert summary["ledger_size"] == 1
    with open(os.path.join(store, "outcomes.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    assert ledger["SIG-TEST-1"]["final"] is True
    # second run skips finalized signals
    summary2 = tr.track_all()
    assert summary2["skipped_final_or_nodata"] == 1
    assert summary2["evaluated_this_run"] == 0
