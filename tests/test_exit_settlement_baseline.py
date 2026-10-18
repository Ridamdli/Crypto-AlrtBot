"""Integration baselines: settlement vs supervised exit plans.

Pins CURRENT behavior (Option A of the Phase-5 report: plans are
advisory; settlement uses original signal levels). These tests document
the architecture as built — they do not endorse it as the end-state.
All fixtures are synthetic; no network, no live data.
"""
import inspect

from src.backtest.evaluator import SignalOutcomeEvaluator
from src.exits.ledger import ExitDecisionLedger
from src.exits.models import ExitAction, SupervisorDecision
from src.papertrade.portfolio import PaperPortfolio
from tests.test_exits import _decide, bars, sig


def _eval_klines():
    # LONG entry 100 fills on bar 0 (low 99.5), rises through TP 104 on
    # bar 4 without touching SL 98 (liq ~90.4 at 10x untouched).
    closes = [100.0, 101.0, 102.0, 103.0, 104.5, 105.0]
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else 99.8
        out.append({"timestamp": i * 900000, "open": o,
                    "high": max(o, c) + 0.1, "low": min(o, c) - 0.5,
                    "close": c, "volume": 1000.0})
    return out


def _long_signal():
    return sig("LONG", entry=100.0, sl=98.0, tp=104.0, sid="SIG-BASE")


def test_settlement_ignores_supervised_plans():
    """The evaluator takes (signal, klines) only: no plan parameter exists,
    so an applied supervised plan cannot change a settlement result."""
    params = inspect.signature(SignalOutcomeEvaluator.evaluate_signal).parameters
    assert "plan" not in params and "exit" not in " ".join(params)
    ev = SignalOutcomeEvaluator()
    before = ev.evaluate_signal(_long_signal(), _eval_klines())
    assert before["tp1_hit"] is True and before["status"] == "TP1_HIT"
    # A tightened supervised plan exists elsewhere; settlement is identical.
    after = ev.evaluate_signal(_long_signal(), _eval_klines())
    assert after["net_realized_r"] == before["net_realized_r"]
    assert after["status"] == before["status"]


def test_supervised_change_coexists_without_rewriting_history(tmp_path):
    """Applied TIGHTEN bumps the ledger plan while the outcome record
    keeps the original signal levels (the Phase-5 I1 split, pinned)."""
    from src.exits.models import PositionState
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.TIGHTEN_STOP.value

    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.record(d, apply=True) is True
    assert led.active_plan(s).stop_loss != 98.0  # supervised level moved
    assert led.active_plan(s).stop_loss == d.plan_after["stop_loss"]

    ev = SignalOutcomeEvaluator()
    res = ev.evaluate_signal(_long_signal(), _eval_klines())
    assert res["tp1_hit"] is True  # settled on ORIGINAL tp=104
    assert res["net_realized_r"] > 0


def test_finalized_outcome_skipped_by_supervision(tmp_path):
    """A final outcome is never re-supervised (current monitor behavior)."""
    from src.exits.monitor import ExitMonitor

    s = sig("LONG", entry=100.0, sl=98.0, tp=104.0, sid="SIG-FINAL")

    class FakeTracker:
        def load_ledger(self):
            return {s["signal_id"]: {"final": True, "status": "TP1_HIT",
                                     "net_realized_r": 2.0}}

        def load_all_signals(self):
            return [s]

    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    mon = ExitMonitor(tracker=FakeTracker(), ledger=led, fetcher=None,
                      apply=False)
    summary = mon.run_cycle()
    assert summary["evaluated"] == 0
    assert summary["skipped"] >= 1
    assert led.decisions_for(s["signal_id"]) == []


def test_reduce_invalidate_flag_do_not_alter_settlement(tmp_path):
    """REDUCE / INVALIDATE / FLAG decisions change neither evaluator
    output nor booked paper equity (current behavior, pinned)."""
    ev = SignalOutcomeEvaluator()
    base = ev.evaluate_signal(_long_signal(), _eval_klines())
    pf = PaperPortfolio(storage_dir=str(tmp_path))
    oc = {"SIG-BASE": {"final": True, "net_realized_r": base["net_realized_r"],
                       "risk_amount": 10.0}}
    pf.settle(oc)
    before = pf.summary()["equity"]

    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    for action in ("REDUCE_PAPER_POSITION", "INVALIDATE_PENDING_SETUP",
                   "FLAG_CONFLICT"):
        led.record(SupervisorDecision(signal_id="SIG-BASE", action=action,
                                      rationale="baseline pin",
                                      decision_key=f"pin-{action}"),
                   apply=True)
    pf2 = PaperPortfolio(storage_dir=str(tmp_path))
    pf2.settle(oc)
    assert pf2.summary()["equity"] == before
    again = ev.evaluate_signal(_long_signal(), _eval_klines())
    assert again["net_realized_r"] == base["net_realized_r"]


def test_lineage_survives_ledger_roundtrip(tmp_path):
    """Phase-4 lineage fix, pinned through persistence: reloaded active
    plan basis still equals its decision key."""
    from src.exits.models import PositionState
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.TIGHTEN_STOP.value
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.record(d, apply=True) is True
    led2 = ExitDecisionLedger(storage_dir=str(tmp_path))  # restart
    stored = led2.decisions_for(s["signal_id"])[0]
    assert led2.active_plan(s).basis_decision_key == stored["decision_key"]
    assert led2.has_key(d.decision_key) is True


def test_repeat_evaluation_dedupe(tmp_path):
    """Identical repeated decisions share one key; the ledger suppresses
    the duplicate the way the monitor's has_key gate does."""
    from src.exits.models import PositionState
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d1 = _decide(s, kl, PositionState.OPEN)
    d2 = _decide(s, kl, PositionState.OPEN)
    assert d1.decision_key == d2.decision_key
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.record(d1, apply=True) is True
    assert led.has_key(d2.decision_key) is True  # monitor would suppress
