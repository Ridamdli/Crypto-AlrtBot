"""Focused tests for the Exit Orchestrator subsystem (paper-only).

Conventions follow the repo suite: plain functions, tmp_path isolation,
synthetic deterministic klines (no network, no randomness except the
seeded bootstrap). Every test below maps to a requirement in §7.
"""
import json
import os

from src.backtest.evaluator import SignalOutcomeEvaluator
from src.exits import policy
from src.exits.config import ExitConfig
from src.exits.harness import CandidateEvaluator, arm_metrics, compare_arms, make_decide_fn
from src.exits.ledger import ExitDecisionLedger
from src.exits.models import (
    ExitAction,
    ExitPlan,
    PositionState,
    PositionView,
    classify_position,
)
from src.exits.monitor import ExitMonitor
from src.exits.risk_guard import RiskGuard
from src.exits.supervisor import ExitSupervisor, decision_key
from src.exits.workers import (
    CounterSignalWorker,
    RiskRewardWorker,
    StopWorker,
    StructureWorker,
    TargetWorker,
    WorkerCtx,
)

TF = 900_000
T0 = 1_700_000_000_000  # fixed epoch base: deterministic timestamps


# ── builders ──────────────────────────────────────────────────────
def bars(closes, start=T0, hi_pad=0.0, lo_pad=0.0, volume=1.0):
    """Synthetic completed 15m candles from a close series."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        ts = start + i * TF
        out.append({
            "timestamp": ts, "close_time": ts + TF - 1,
            "open": o, "high": max(o, c) + hi_pad, "low": min(o, c) - lo_pad,
            "close": c, "volume": volume,
        })
        prev = c
    return out


def flat(n, px=100.0, start=T0):
    return bars([px] * n, start=start)


def sig(side="LONG", entry=100.0, sl=99.0, tp=102.0,
        ts="2026-01-01T00:00:00Z", sid="SIG-TEST", lev=10, **kw):
    tp = kw.pop("tp1", tp)  # legacy ladder kwarg maps onto the single TP
    notional = 10.0 / abs(entry - sl) * entry
    return {
        "signal_id": sid, "timestamp": ts, "symbol": "BTCUSDT", "side": side,
        "strategy_id": "donchian_20", "strategy_version": "1.0.0",
        "entry": entry, "stop_loss": sl, "tp": tp,
        "leverage": lev, "margin": round(notional / lev, 2),
        "position_notional": round(notional, 2), "risk_amount": 10.0,
        "risk_reward": 2.0,
        "confidence": 75, "conditions": [], "invalidation_conditions": [],
        "management_rules": [],
    }


def ctx_for(signal, completed, state=PositionState.OPEN, outcome=None,
            cfg=None, now_ms=None, plan=None):
    cfg = cfg or ExitConfig()
    done, _ = policy.split_completed(completed, now_ms or 0, cfg.timeframe_ms)
    # default "now" = just after the last completed close
    if now_ms is None:
        now_ms = policy.last_close_ts(done or completed, cfg.timeframe_ms) + 1000
        done, _ = policy.split_completed(completed, now_ms, cfg.timeframe_ms)
    fresh, reason, data_ts = policy.check_freshness(done, now_ms, cfg)
    plan = plan or ExitPlan.genesis(signal)
    return WorkerCtx(signal, plan.stop_loss, [plan.tp],
                     state, done, now_ms, cfg, data_ts_ms=data_ts,
                     outcome=outcome, fresh=fresh, freshness_reason=reason)


# ── §7: policy primitives ─────────────────────────────────────────
def test_split_completed_excludes_forming_and_sorts():
    kl = bars([100.0] * 5)
    now = kl[-1]["close_time"] - 10  # last bar still forming
    done, forming = policy.split_completed(list(reversed(kl)), now, TF)
    assert len(done) == 4 and len(forming) == 1
    assert [k["timestamp"] for k in done] == sorted(k["timestamp"] for k in done)


def test_freshness_stale_thin_future():
    cfg = ExitConfig()
    kl = flat(30)
    now = policy.last_close_ts(kl, TF) + 1000
    ok, _, _ = policy.check_freshness(kl, now, cfg)
    assert ok
    ok, reason, _ = policy.check_freshness(kl, now + 3600_000, cfg)
    assert not ok and "stale" in reason
    ok, reason, _ = policy.check_freshness(flat(10), now, cfg)
    assert not ok and "thin" in reason
    ok, reason, _ = policy.check_freshness(kl, kl[0]["close_time"] - 1, cfg)
    assert not ok  # everything lies in the future


def test_opposite_break_has_no_lookahead():
    # flat then a down-break on the LAST bar only
    closes = [100.0] * 25 + [99.0]
    kl = bars(closes)
    broken, info = policy.opposite_break(kl, "LONG", 20)
    assert broken and info["last_close"] == 99.0
    # same series without the final bar: no break (proves the bar itself decides)
    broken2, _ = policy.opposite_break(kl[:-1], "LONG", 20)
    assert not broken2


def test_fee_model_pins_to_evaluator(tmp_path):
    ev = SignalOutcomeEvaluator(storage_dir=str(tmp_path))
    cfg = ExitConfig()
    assert cfg.taker_fee_pct == ev.taker_fee_pct
    assert cfg.slippage_pct == ev.slippage_pct
    assert CandidateEvaluator().taker_fee_pct == ev.taker_fee_pct
    assert CandidateEvaluator().slippage_pct == ev.slippage_pct
    # fee drag identity: entry*2*(taker+slip)/risk
    assert policy.fee_cost_r(100.0, 1.0, cfg) == round(100.0 * 2 * 0.0006 / 1.0, 3)


# ── §7: LONG/SHORT stop tightening + widen rejection ──────────────
def test_stop_worker_long_tighten_candidate():
    # swing low 99 strictly between SL 98 and entry 100
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    a = StopWorker().assess(ctx_for(s, kl))
    assert a.verdict == "STOP_TIGHTEN_CANDIDATE"
    assert a.proposed_action == ExitAction.TIGHTEN_STOP.value
    new_sl = a.proposal["stop_loss"]
    assert 98.0 < new_sl < 100.0  # tighter, never widened, never past entry


def test_stop_worker_short_tighten_candidate():
    closes = [100.0] * 24 + [100.6, 101.0, 100.8, 100.5, 100.4, 100.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=102.0)
    a = StopWorker().assess(ctx_for(s, kl))
    assert a.verdict == "STOP_TIGHTEN_CANDIDATE"
    new_sl = a.proposal["stop_loss"]
    assert 100.0 < new_sl < 102.0


def test_stop_worker_never_touches_pending():
    s = sig("LONG")
    a = StopWorker().assess(ctx_for(s, flat(30), state=PositionState.PENDING))
    assert a.proposed_action is None and a.verdict == "STOP_OK"


def test_guard_rejects_widened_stops_both_sides():
    g = RiskGuard(ExitConfig())
    s = sig("LONG", entry=100.0, sl=98.0)
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    ok, reasons = g.validate(view, "TIGHTEN_STOP", {"stop_loss": 97.5})
    assert not ok and any("widen" in r for r in reasons)
    s2 = sig("SHORT", entry=100.0, sl=102.0)
    view2 = PositionView(s2, None, PositionState.OPEN, ExitPlan.genesis(s2), False)
    ok, _ = g.validate(view2, "TIGHTEN_STOP", {"stop_loss": 102.5})
    assert not ok
    # crossing entry inverts the trade -> rejected
    ok, _ = g.validate(view, "TIGHTEN_STOP", {"stop_loss": 100.5})
    assert not ok


def test_guard_rejects_forbidden_exposure_fields():
    g = RiskGuard(ExitConfig())
    s = sig("LONG")
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    for bad in ({"stop_loss": 98.5, "leverage": 20},
                {"stop_loss": 98.5, "size": 5.0},
                {"stop_loss": 98.5, "risk_amount": 50.0}):
        ok, reasons = g.validate(view, "TIGHTEN_STOP", dict(bad))
        assert not ok and any("forbidden" in r for r in reasons)


# ── §7: TP adjustment + net R:R ───────────────────────────────────
def test_target_worker_flags_unrealistic_and_proposes_ordered():
    # ATR ~1.0, tp1 10R away -> over the 6-ATR cap
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
    kl = bars(closes)
    s = sig("LONG", entry=100.0, sl=99.0, tp1=110.0, tp2=112.0, tp3=114.0)
    a = TargetWorker().assess(ctx_for(s, kl))
    assert a.verdict == "TARGETS_UNREALISTIC"
    assert a.proposed_action == ExitAction.ADJUST_TAKE_PROFITS.value
    t = a.proposal["tp"]
    assert t > 100.0
    # economics gate held: pulled tp keeps >=1R gross and positive net
    assert policy.gross_rr(100.0, 99.0, t) >= 1.0


def test_target_worker_ok_when_realistic():
    s = sig("LONG")  # tp1 2R away, ATR of flat series ~0 -> abstain path differs
    closes = [100.0 + (0.05 if i % 2 else -0.05) for i in range(30)]
    kl = bars(closes)
    a = TargetWorker().assess(ctx_for(s, kl))
    assert a.verdict in ("TARGETS_OK", "TARGETS_UNREALISTIC")
    if a.verdict == "TARGETS_OK":
        assert a.proposed_action is None


def test_target_worker_skips_filled_tp():
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
    kl = bars(closes)
    s = sig("LONG", entry=100.0, sl=99.0, tp1=101.0)
    oc = {"tp1_hit": True}
    a = TargetWorker().assess(ctx_for(s, kl, state=PositionState.OPEN, outcome=oc))
    assert a.evidence["remaining_tps"] == []


def test_guard_rejects_bad_targets():
    g = RiskGuard(ExitConfig())
    s = sig("LONG", entry=100.0, sl=99.0)
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    # tp below entry for a LONG
    ok, _ = g.validate(view, "ADJUST_TAKE_PROFITS",
                       {"tp1": 99.5, "tp2": 101.0, "tp3": 102.0})
    assert not ok
    # tp1 under the 1R economics floor
    ok, _ = g.validate(view, "ADJUST_TAKE_PROFITS",
                       {"tp1": 100.5, "tp2": 101.0, "tp3": 102.0})
    assert not ok
    # identical ladder is a no-op
    ok, _ = g.validate(view, "ADJUST_TAKE_PROFITS",
                       {"tp1": 102.0, "tp2": 103.0, "tp3": 104.0})
    assert not ok


# ── §7: invalidation (genuine) vs opposition ──────────────────────
def test_structure_invalidates_on_stop_breach_open():
    closes = [100.0] * 25 + [101.5, 102.0, 102.5, 102.2, 102.4]  # SHORT, SL 102
    kl = bars(closes)
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    a = StructureWorker().assess(ctx_for(s, kl))
    assert a.verdict == "THESIS_INVALIDATED" and a.evidence["stop_breached"]
    assert a.proposed_action == ExitAction.CLOSE_PAPER_POSITION.value


def test_structure_invalidates_pending_on_elapsed_window():
    kl = flat(30)
    # signal ~2h before the first bar: 30 observed bars after it, window elapsed
    s = sig("LONG", ts="2023-11-14T21:00:00Z")
    now = T0 + 30 * TF  # last close + epsilon keeps data fresh
    a = StructureWorker().assess(ctx_for(s, kl, state=PositionState.PENDING, now_ms=now))
    assert a.verdict == "THESIS_INVALIDATED"
    assert a.proposed_action == ExitAction.INVALIDATE_PENDING_SETUP.value


def test_structure_spares_young_pending():
    kl = flat(30)
    now = policy.last_close_ts(kl, TF) + 1000
    # signal 3 bars old: window (6) not elapsed with observed bars
    import datetime
    sig_ts = datetime.datetime.fromtimestamp((now - 3 * TF) / 1000,
                                             tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = sig("LONG", ts=sig_ts)
    a = StructureWorker().assess(ctx_for(s, kl, state=PositionState.PENDING, now_ms=now))
    assert a.verdict in ("THESIS_INTACT", "THESIS_WEAKENED")
    assert a.proposed_action is None


def test_counter_weak_opposition_never_proposes_exit():
    # opposite break WITHOUT deterioration: last bar spikes up, prior chop flat
    closes = [100.0] * 29 + [101.0]
    kl = bars(closes, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=103.0, tp1=98.0, tp2=97.0, tp3=96.0)
    a = CounterSignalWorker().assess(ctx_for(s, kl))
    assert a.verdict in ("WEAK_OPPOSITION", "NO_OPPOSITION")
    assert a.proposed_action in (None, ExitAction.FLAG_CONFLICT.value)
    assert a.proposed_action != ExitAction.CLOSE_PAPER_POSITION.value


def test_counter_genuine_reversal_flags_without_damage():
    closes = [100.0] * 25 + [100.5, 101.5, 102.5, 103.5, 104.5]
    kl = bars(closes, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=110.0, tp1=98.0, tp2=97.0, tp3=96.0)
    a = CounterSignalWorker().assess(ctx_for(s, kl))
    assert a.verdict == "GENUINE_REVERSAL"
    # unrealized = (100-104.5)/10 = -0.45R: above damage threshold -> FLAG only
    assert a.proposed_action == ExitAction.FLAG_CONFLICT.value


def test_counter_genuine_reversal_closes_on_damage():
    closes = [100.0] * 25 + [100.5, 101.5, 102.5, 103.5, 104.5]
    kl = bars(closes, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    a = CounterSignalWorker().assess(ctx_for(s, kl))
    assert a.verdict == "GENUINE_REVERSAL"
    assert a.proposed_action == ExitAction.CLOSE_PAPER_POSITION.value


# ── §7: supervisor doctrine ───────────────────────────────────────
def _decide(signal, completed, state, outcome=None, cfg=None, **kw):
    cfg = cfg or ExitConfig()
    c = ctx_for(signal, completed, state=state, outcome=outcome, cfg=cfg, **kw)
    from src.exits.workers import ALL_WORKERS
    sup = ExitSupervisor(cfg)
    view = PositionView(signal, outcome, state, ExitPlan.genesis(signal), False)
    return sup.decide(view, [w().assess(c) for w in ALL_WORKERS], c, c.now_ms)


def test_supervisor_stale_data_abstains_despite_breach():
    closes = [100.0] * 25 + [101.5, 102.0, 102.5, 102.2, 103.5]  # breach, but stale
    kl = bars(closes)
    now = policy.last_close_ts(kl, TF) + 3600_000
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    d = _decide(s, kl, PositionState.OPEN, now_ms=now)
    assert d.action == ExitAction.INSUFFICIENT_DATA.value
    assert d.plan_after is None


def test_supervisor_invalidation_closes_open():
    closes = [100.0] * 25 + [101.5, 102.0, 102.5, 102.2, 102.4]
    kl = bars(closes)
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.CLOSE_PAPER_POSITION.value
    assert d.events_applied and d.rationale


def test_supervisor_conflict_flags_and_retains():
    # reversal evidence present, but stop far away so structure stays intact
    closes = [100.0] * 25 + [100.5, 101.5, 102.5, 103.5, 104.5]
    kl = bars(closes, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=110.0, tp1=98.0, tp2=97.0, tp3=96.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.FLAG_CONFLICT.value
    assert d.plan_after is None
    assert any(r["alternative"] == ExitAction.CLOSE_PAPER_POSITION.value
               for r in d.rejected)


def test_supervisor_weak_opposition_holds_plan():
    # Lone final-bar spike: opposite break without deterioration (WEAK).
    # PENDING state isolates the opposition question — no position-state
    # action is admissible, so any exit/adjustment would be a doctrine breach.
    closes = [100.0] * 29 + [101.0]
    kl = bars(closes, hi_pad=0.05)
    s = sig("SHORT", entry=100.0, sl=103.0, tp1=98.0, tp2=97.0, tp3=96.0)
    d = _decide(s, kl, PositionState.PENDING)
    assert d.action == ExitAction.NO_CHANGE.value
    assert "Doing nothing is the decision" in d.rationale


def test_supervisor_applies_guarded_tighten():
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.TIGHTEN_STOP.value
    assert d.plan_after["version"] == 1
    assert 98.0 < d.plan_after["stop_loss"] < 100.0
    assert d.risk_guard.get("ok") is True


def test_supervisor_guard_rejection_keeps_plan_loudly():
    # Force a bad proposal through: monkeypatched worker proposing a widened stop
    from src.exits.models import WorkerAssessment
    # Non-flat series so ATR-based materiality passes and the proposal
    # reaches RiskGuard (which must reject the widened stop).
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
    kl = bars(closes)
    s = sig("LONG", entry=100.0, sl=98.0)
    cfg = ExitConfig()
    c = ctx_for(s, kl, cfg=cfg)
    bad = WorkerAssessment("stop", "x", c.now_ms, c.data_ts_ms, "STOP_TIGHTEN_CANDIDATE",
                           proposed_action="TIGHTEN_STOP", proposal={"stop_loss": 97.0},
                           evidence_quality=1.0)
    from src.exits.workers import ALL_WORKERS
    others = [w().assess(c) for w in ALL_WORKERS if w.ID != "stop"]
    sup = ExitSupervisor(cfg)
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    d = sup.decide(view, others + [bad], c, c.now_ms)
    assert d.action == ExitAction.NO_CHANGE.value
    assert d.risk_guard.get("ok") is False
    assert d.plan_after is None


def test_supervisor_deterministic_replay():
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d1 = _decide(s, kl, PositionState.OPEN)
    d2 = _decide(s, kl, PositionState.OPEN)
    assert d1.decision_key == d2.decision_key and d1.action == d2.action


def test_decision_key_collides_only_on_identical_inputs():
    k1 = decision_key("A", 0, 11, "NO_CHANGE", {})
    assert k1 == decision_key("A", 0, 11, "NO_CHANGE", {})
    assert k1 != decision_key("A", 0, 12, "NO_CHANGE", {})
    assert k1 != decision_key("A", 0, 11, "FLAG_CONFLICT", {})


def test_applied_adjustment_basis_matches_decision_key():
    """Lineage: an applied plan's basis key must equal its decision's key,
    so plan→decision correlation joins on stored keys."""
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == ExitAction.TIGHTEN_STOP.value
    assert d.plan_after["basis_decision_key"] == d.decision_key


def test_fixed_key_matches_pre_fix_clean_key():
    """The fix changes only correlation metadata: the persisted decision
    key must equal the key computed from clean params (the pre-fix
    _finish behavior), i.e. deduplication identity is unchanged."""
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    expected = decision_key(s["signal_id"], 0, d.data_ts_ms,
                            ExitAction.TIGHTEN_STOP.value,
                            {"stop_loss": d.plan_after["stop_loss"]})
    assert d.decision_key == expected
    assert d.plan_after["basis_decision_key"] == expected


def test_distinct_proposals_distinct_basis_keys():
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)
    other = decision_key(s["signal_id"], 0, d.data_ts_ms,
                         ExitAction.TIGHTEN_STOP.value, {"stop_loss": 97.5})
    assert other != d.plan_after["basis_decision_key"]


# ── §7: guard idempotency/ordering + classification ───────────────
def test_guard_duplicate_and_stale_rejected():
    g = RiskGuard(ExitConfig())
    s = sig("LONG", entry=100.0, sl=98.0)
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    prop = {"stop_loss": 98.5, "_decision_key": "K1"}
    ok, _ = g.validate(view, "TIGHTEN_STOP", prop, seen_keys=frozenset(),
                       last_data_ts_ms=0, data_ts_ms=100)
    assert ok
    ok, reasons = g.validate(view, "TIGHTEN_STOP", prop, seen_keys=frozenset({"K1"}),
                             last_data_ts_ms=0, data_ts_ms=100)
    assert not ok and any("duplicate" in r for r in reasons)
    prop2 = {"stop_loss": 98.5, "_decision_key": "K2"}
    ok, reasons = g.validate(view, "TIGHTEN_STOP", prop2, seen_keys=frozenset(),
                             last_data_ts_ms=200, data_ts_ms=100)
    assert not ok and any("stale" in r for r in reasons)


def test_classify_position_states():
    s = sig("LONG")
    assert classify_position(s, None) == PositionState.PENDING
    assert classify_position(s, {"final": True, "entry_triggered": True}) == PositionState.CLOSED
    assert classify_position(s, {"final": True, "entry_triggered": False}) == PositionState.INVALIDATED
    assert classify_position(s, {"final": False, "entry_triggered": True}) == PositionState.OPEN
    assert classify_position(s, {"final": False, "entry_triggered": True,
                                 "tp1_hit": True}) == PositionState.OPEN
    assert classify_position(s, {"final": False, "entry_triggered": False}) == PositionState.PENDING
    assert classify_position(s, None, invalidated_by_exits=True) == PositionState.INVALIDATED


# ── §7: ledger persistence + restart ─────────────────────────────
def test_ledger_apply_and_restart_recovery(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path), max_decisions=3)
    s = sig("LONG", entry=100.0, sl=98.0)
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    d = _decide(s, kl, PositionState.OPEN)
    assert d.action == "TIGHTEN_STOP"
    assert led.record(d, apply=True)
    assert led.active_plan(s).version == 1
    assert led.has_key(d.decision_key)
    # restart: new instance recovers plans + keys
    led2 = ExitDecisionLedger(storage_dir=str(tmp_path), max_decisions=3)
    assert led2.active_plan(s).version == 1
    assert led2.has_key(d.decision_key)
    assert led2.summary()["decisions"] == 1


def test_ledger_monitor_only_freezes_plans(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    s = sig("LONG")
    d = _decide(s, bars([100.0] * 30), PositionState.OPEN)
    led.record(d, apply=False)
    assert led.active_plan(s).version == 0
    assert len(led.decisions_for("SIG-TEST")) == 1


def test_ledger_corrupt_file_fails_safe(tmp_path):
    p = os.path.join(str(tmp_path), "exit_decisions.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.healthy is False
    assert led.active_plan(sig("LONG")).version == 0


def test_ledger_outcome_link(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    d = _decide(sig("LONG"), bars([100.0] * 30), PositionState.OPEN)
    led.record(d, apply=False)
    led.outcome_link("SIG-TEST", {"status": "TP3_HIT", "net_realized_r": 3.1, "final": True})
    rec = led.decisions_for("SIG-TEST")[0]
    assert rec["linked_outcome"]["status"] == "TP3_HIT"


def _pin_decision(sid, key, action="NO_CHANGE"):
    from src.exits.models import SupervisorDecision
    return SupervisorDecision(signal_id=sid, action=action,
                              rationale="idempotency pin",
                              decision_key=key)


def test_seen_keys_survive_decision_eviction(tmp_path):
    """R2-1: evicting old audit records must not resurrect their keys."""
    led = ExitDecisionLedger(storage_dir=str(tmp_path), max_decisions=3)
    for i in range(5):
        assert led.record(_pin_decision("S", f"K{i}"), apply=False) is True
    assert len(led._state["decisions"]) == 3  # cap still bounds history
    for i in range(5):
        assert led.has_key(f"K{i}")
    assert led.seen_keys() == frozenset(f"K{i}" for i in range(5))


def test_duplicate_suppressed_after_restart(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path), max_decisions=3)
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    d = _decide(sig("LONG", entry=100.0, sl=98.0),
               bars(closes, lo_pad=0.05, hi_pad=0.05), PositionState.OPEN)
    assert d.action == "TIGHTEN_STOP"
    assert led.record(d, apply=True) is True
    led2 = ExitDecisionLedger(storage_dir=str(tmp_path), max_decisions=3)
    assert led2.has_key(d.decision_key) is True
    assert d.decision_key in led2.seen_keys()
    assert led2.active_plan(sig("LONG", entry=100.0, sl=98.0)).version == 1


def test_repeat_record_keeps_suppression_without_key_dupes(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    d = _pin_decision("S", "K-same")
    assert led.record(d, apply=False) is True
    assert led.record(d, apply=False) is True  # append behavior unchanged
    assert led.has_key("K-same") is True
    assert list(led._state["seen_keys"]).count("K-same") == 1


def test_proposal_only_decisions_unchanged(tmp_path):
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    d = _pin_decision("S", "K-flag", action="FLAG_CONFLICT")
    assert led.record(d, apply=False) is True
    assert led.has_key("K-flag") is True
    assert led.active_plan(sig("LONG")).version == 0  # plans frozen
    assert led.close_signal_ids() == []


def test_legacy_file_backfills_keys_without_rewrite(tmp_path):
    from src.exits.ledger import ExitDecisionLedger
    path = os.path.join(str(tmp_path), "exit_decisions.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1,
                   "decisions": [
                       {"signal_id": "S", "decision_key": "K-old1"},
                       {"signal_id": "S", "decision_key": "K-old2"}],
                   "plans": {}, "events": {}, "invalidated": [],
                   "last_eval": {}}, f)
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.healthy is True
    assert led.has_key("K-old1") and led.has_key("K-old2")
    assert {"K-old1", "K-old2"} <= set(led.seen_keys())


def test_corrupt_file_keys_fail_safe_empty(tmp_path):
    p = os.path.join(str(tmp_path), "exit_decisions.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    assert led.healthy is False
    assert led.seen_keys() == frozenset()
    assert led.has_key("anything") is False


# ── §7: monitor behavior ──────────────────────────────────────────
class _FakeFetcher:
    def __init__(self, klines):
        self.klines = klines
        self.calls = 0

    def fetch_standardized_klines(self, symbol, interval, limit=500, end_time=None):
        self.calls += 1
        return self.klines


class _FakeTracker:
    def __init__(self, signals, outcomes):
        self._signals = signals
        self._outcomes = {k: dict(v) for k, v in outcomes.items()}

    def load_ledger(self):
        return {k: dict(v) for k, v in self._outcomes.items()}

    def load_all_signals(self):
        return self._signals


def _monitor(tmp_path, klines, signals, outcomes, now_ms, **kw):
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker(signals, outcomes),
                      ledger=led, fetcher=_FakeFetcher(klines),
                      now_ms_fn=lambda: now_ms, **kw)
    return mon, led


def test_monitor_records_no_change_and_skips_repeat(tmp_path):
    kl = flat(30)
    now = policy.last_close_ts(kl, TF) + 1000
    s = sig("LONG")
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker([s], {}),
                      ledger=led, fetcher=_FakeFetcher(kl),
                      now_ms_fn=lambda: now)
    out1 = mon.run_cycle()
    assert out1["evaluated"] == 1
    assert out1["actions"].get("NO_CHANGE") == 1
    out2 = mon.run_cycle()
    assert out2["evaluated"] == 0 and out2["skipped"] == 1  # idempotent: no new candle


def test_monitor_never_raises_and_leaves_outcomes_untouched(tmp_path):
    class _Boom:
        def fetch_standardized_klines(self, *a, **k):
            raise ConnectionError("feed down")
    s = sig("LONG")
    outcomes = {"SIG-TEST": {"final": False, "entry_triggered": True}}
    tracker = _FakeTracker([s], outcomes)
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    mon = ExitMonitor(cfg=ExitConfig(), tracker=tracker, ledger=led,
                      fetcher=_Boom(), now_ms_fn=lambda: T0 + 10 * TF)
    out = mon.run_cycle()  # must not raise
    assert out["evaluated"] == 1  # INSUFFICIENT_DATA recorded, not applied
    assert tracker._outcomes == outcomes  # paper-side ledgers untouched


def test_monitor_applies_close_on_breach(tmp_path):
    closes = [100.0] * 25 + [101.5, 102.0, 102.5, 102.2, 102.4]
    kl = bars(closes)
    now = policy.last_close_ts(kl, TF) + 1000
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    outcomes = {"SIG-TEST": {"final": False, "entry_triggered": True}}
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker([s], outcomes),
                      ledger=led, fetcher=_FakeFetcher(kl),
                      now_ms_fn=lambda: now, apply=True)
    out = mon.run_cycle()
    assert out["actions"].get("CLOSE_PAPER_POSITION") == 1
    assert any(e["kind"] == "close" for e in led.get_events("SIG-TEST"))


def test_monitor_skips_final_and_invalidated(tmp_path):
    kl = flat(30)
    now = policy.last_close_ts(kl, TF) + 1000
    s1 = sig("LONG", sid="A")
    s2 = sig("LONG", sid="B")
    outcomes = {"A": {"final": True, "entry_triggered": True}}
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    led._state["invalidated"].append("B")
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker([s1, s2], outcomes),
                      ledger=led, fetcher=_FakeFetcher(kl),
                      now_ms_fn=lambda: now)
    out = mon.run_cycle()
    assert out["evaluated"] == 0 and out["skipped"] == 2


def test_monitor_out_of_order_feed_skipped(tmp_path):
    kl = flat(30)
    now = policy.last_close_ts(kl, TF) + 1000
    s = sig("LONG")
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    led.mark_evaluated("SIG-TEST", policy.last_close_ts(kl, TF) + TF, now)
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker([s], {}),
                      ledger=led, fetcher=_FakeFetcher(kl),
                      now_ms_fn=lambda: now)
    out = mon.run_cycle()
    assert out["evaluated"] == 0  # feed older than last eval: never backwards


# ── §7: harness equivalence + behavior ───────────────────────────
def _win_klines_long():
    # touch 100, run through 102/103/104
    seq = [101.0, 100.5, 100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 104.0]
    kl = []
    prev = 101.0
    for i, c in enumerate(seq):
        ts = T0 + i * TF
        kl.append({"timestamp": ts, "close_time": ts + TF - 1, "open": prev,
                   "high": max(prev, c) + 0.05, "low": min(prev, c) - 0.05,
                   "close": c, "volume": 1.0})
        prev = c
    return kl


def test_candidate_static_plan_matches_baseline(tmp_path):
    base = SignalOutcomeEvaluator(storage_dir=str(tmp_path))
    cand = CandidateEvaluator()
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0)
    kl = _win_klines_long()
    b = base.evaluate_signal(s, kl)
    c = cand.evaluate_signal(s, kl, None)
    for k in b:
        assert c[k] == b[k], f"divergence on {k}: {c[k]} != {b[k]}"
    assert c["status"] == "TP1_HIT"


def test_candidate_equivalence_across_scenarios():
    import tempfile
    base = SignalOutcomeEvaluator(storage_dir=tempfile.mkdtemp())
    cand = CandidateEvaluator()
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0)
    # loss: touch then dump through SL
    loss = bars([101.0, 100.0, 99.5, 98.5])
    # expired: never touch
    exp = bars([101.0, 101.5, 102.0, 102.5, 103.0, 104.0, 105.0])
    # ambiguous: one bar spanning tp1 and sl (conservative -> SL)
    amb = bars([100.5, 100.0])
    amb[1] = dict(amb[1], high=103.0, low=98.0)
    for kl in (loss, exp, amb):
        b = base.evaluate_signal(dict(s), [dict(k) for k in kl])
        c = cand.evaluate_signal(dict(s), [dict(k) for k in kl], None)
        for k in b:
            assert c[k] == b[k], f"scenario divergence on {k}"


def test_harness_executes_reduce_at_open():
    # LONG: touch 100 at bar0, drift up; reduce once at the 2nd consult,
    # executed at that bar's OPEN (101.0 -> +1R on half), then TP1 fills rest:
    # 0.5*1.0 + 0.5*2.0 = 1.5 gross.
    kl = bars([100.0, 101.0, 101.0, 101.5, 102.0, 102.5])
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0)
    calls = []

    def decide_fn(signal, plan, fill, history, data_ts):
        calls.append(max(k["timestamp"] for k in history))
        if len(calls) == 2:
            return None, [{"kind": "reduce", "fraction": 0.5}]
        return None, []

    cand = CandidateEvaluator()
    c = cand.evaluate_signal(s, kl, decide_fn)
    assert c["realized_r"] == 1.5
    # look-ahead check: history never includes the bar under evaluation.
    # consult j happens at abs_idx = entry_idx+1+j with history=klines[:abs_idx]
    # (entry_idx=0 here), so history max ts must equal klines[j].timestamp.
    assert len(calls) >= 2
    for j, ts in enumerate(calls):
        assert ts == kl[j]["timestamp"] < kl[j + 1]["timestamp"]


def test_harness_future_spike_cannot_leak():
    kl = bars([101.0, 100.0, 100.2, 100.1, 100.3, 100.2, 100.4, 109.0])
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0)
    seen = []

    def decide_fn(signal, plan, fill, history, data_ts):
        seen.append((len(history), max(k["timestamp"] for k in history)))
        return None, []

    cand = CandidateEvaluator()
    c1 = cand.evaluate_signal(s, kl, decide_fn)
    full_seen = list(seen)
    seen.clear()
    c2 = cand.evaluate_signal(s, kl[:-1], decide_fn)
    # consult histories for the shared prefix are identical: the +9 spike at
    # the end cannot influence any earlier decision (structural no-look-ahead)
    assert seen == full_seen[: len(seen)]
    assert len(full_seen) == len(seen) + 1  # exactly one extra consult sees the spike
    assert c1["plan_changes"] == 0 and c2["plan_changes"] == 0


def test_compare_arms_and_ablation_run_small():
    kl = _win_klines_long()
    # harness keeps klines strictly after the signal timestamp; synthetic bars
    # live at T0 (2023), so the signal must predate them
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0,
            ts="2023-01-01T00:00:00Z")
    klines_map = {"BTCUSDT": kl}

    def factory(disabled=frozenset()):
        return make_decide_fn(ExitConfig(), disabled=disabled)

    out = compare_arms([s], klines_map, factory, max_signals=5,
                       ablate_workers=["stop", "target"])
    assert out["n_signals"] == 1
    assert out["baseline"]["triggered"] == 1
    assert out["candidate"]["triggered"] == 1
    assert "delta_expectancy" in out
    assert set(out["ablation"]) == {"without_stop", "without_target"}
    assert out["change_stats"]["changed"] >= 0


def test_arm_metrics_withholds_ci_on_small_n():
    m = arm_metrics([{"entry_triggered": True, "net_realized_r": 1.0,
                      "first_event": "TP1", "tp1_hit": True, "tp2_hit": False,
                      "tp3_hit": False, "holding_bars": 5}])
    assert m["expectancy_net_r"] == 1.0
    assert m["expectancy_ci95"]["note"].startswith("n<10")


def test_replay_context_gives_workers_early_history():
    # klines start at the signal bar: without context the first consults see
    # thin history; with 30 pre-signal bars they see a full window.
    kl = bars([101.0, 100.0, 100.5, 101.0, 101.5, 102.0])
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0, tp2=103.0, tp3=104.0)
    lens_plain, lens_ctx = [], []

    def mk(store):
        def decide_fn(signal, plan, fill, history, data_ts):
            store.append(len(history))
            return None, []
        return decide_fn

    cand = CandidateEvaluator()
    cand.evaluate_signal(s, kl, mk(lens_plain))
    cand.evaluate_signal(s, kl, mk(lens_ctx), context_klines=flat(30))
    assert lens_plain[0] < 25  # thin: live workers would abstain
    assert lens_ctx[0] >= 25  # full window from the first consult
    assert len(lens_ctx) == len(lens_plain)  # entry path unchanged by context


def test_monitor_no_data_evaluates_on_cadence_only(tmp_path):
    class _Empty:
        def fetch_standardized_klines(self, *a, **k):
            return []

    s = sig("LONG")
    led = ExitDecisionLedger(storage_dir=str(tmp_path))
    now = [T0 + 100 * TF]
    mon = ExitMonitor(cfg=ExitConfig(), tracker=_FakeTracker([s], {}),
                      ledger=led, fetcher=_Empty(),
                      now_ms_fn=lambda: now[0])
    out1 = mon.run_cycle()
    assert out1["evaluated"] == 1  # first INSUFFICIENT_DATA record
    out2 = mon.run_cycle()
    assert out2["evaluated"] == 0  # same cycle cadence: no spam
    now[0] += 3700_000  # beyond max_reeval_s
    out3 = mon.run_cycle()
    # due again, but the identical decision dedupes (no repeated decisions):
    assert out3["evaluated"] == 0 and out3["skipped"] == 1
    assert len(led.decisions_for("SIG-TEST")) == 1


def _view_with_plan(signal, plan, state, outcome=None):
    return PositionView(signal, outcome, state, plan, False)


def _ctx_with_plan(signal, completed, plan, state, outcome=None, cfg=None, now_ms=None):
    cfg = cfg or ExitConfig()
    done, _ = policy.split_completed(completed, now_ms or 0, cfg.timeframe_ms)
    if now_ms is None:
        now_ms = policy.last_close_ts(done or completed, cfg.timeframe_ms) + 1000
        done, _ = policy.split_completed(completed, now_ms, cfg.timeframe_ms)
    fresh, reason, data_ts = policy.check_freshness(done, now_ms, cfg)
    return WorkerCtx(signal, plan.stop_loss, [plan.tp],
                     state, done, now_ms, cfg, data_ts_ms=data_ts,
                     outcome=outcome, fresh=fresh, freshness_reason=reason)


def _rr_reduce_assessment(ctx, fraction=0.5):
    from src.exits.models import WorkerAssessment
    return WorkerAssessment("riskreward", "1.0.0", ctx.now_ms, ctx.data_ts_ms,
                            "RR_DEGRADED", evidence={"remaining_net_rr": [0.3]},
                            indicators=["close"],
                            proposed_action=ExitAction.REDUCE_PAPER_POSITION.value,
                            proposal={"fraction": fraction}, evidence_quality=0.9)


# ── stability gate: materiality ─────────────────────────────────
def test_gate_blocks_immaterial_stop_ratchet():
    # Swing structure offers 98.85105, but the active plan already sits at
    # 98.85: a 0.00105 move against a 0.25-ATR floor (~0.05) is churn, not management.
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    plan = ExitPlan("SIG-TEST", 1, 98.85, 102.0, T0)
    cfg = ExitConfig()
    c = _ctx_with_plan(s, kl, plan, PositionState.OPEN, cfg=cfg)
    sup = ExitSupervisor(cfg)
    view = _view_with_plan(s, plan, PositionState.OPEN)
    d = sup.decide(view, [StopWorker().assess(c)], c, c.now_ms,
                   last_change_ts_ms=T0 - 20000_000)  # cooldown long elapsed
    assert d.action == ExitAction.NO_CHANGE.value
    assert "immaterial" in d.rationale
    assert d.plan_after is None


def test_gate_material_tighten_still_applies():
    closes = [100.0] * 24 + [99.4, 99.0, 99.2, 99.5, 99.6, 99.5]
    kl = bars(closes, lo_pad=0.05, hi_pad=0.05)
    s = sig("LONG", entry=100.0, sl=98.0)
    d = _decide(s, kl, PositionState.OPEN)  # genesis plan: 98.0 -> 98.85 clears floor
    assert d.action == ExitAction.TIGHTEN_STOP.value
    assert d.plan_after["version"] == 1


# ── stability gate: cooldown ────────────────────────────────────
def test_gate_cooldown_blocks_repeat_reduce():
    # One long flat series; each consult uses a window ending at its own
    # "now" so freshness holds at every step (stale data must never reach
    # the cooldown question — Rule 1 fires first by design).
    s = sig("LONG", entry=100.0, sl=99.0)
    kl = flat(120)
    cfg = ExitConfig()
    sup = ExitSupervisor(cfg)
    oc = {"tp1_hit": True}

    def consult(bars, plan, now_ms, last_change):
        c = _ctx_with_plan(s, bars, plan, PositionState.PARTIAL,
                           outcome=oc, cfg=cfg, now_ms=now_ms)
        view = _view_with_plan(s, plan, PositionState.PARTIAL, outcome=oc)
        return sup.decide(view, [_rr_reduce_assessment(c)], c, now_ms,
                          last_change_ts_ms=last_change), c

    now1 = kl[79]["timestamp"] + TF
    d1, _ = consult(kl[:80], ExitPlan.genesis(s), now1, 0)
    assert d1.action == ExitAction.REDUCE_PAPER_POSITION.value
    assert len(d1.events_applied) == 1
    # 1000s later with the identical proposal: cooling down, plan retained.
    plan1 = ExitPlan.from_dict(d1.plan_after)
    d2, _ = consult(kl[:80], plan1, now1 + 1000_000, plan1.decided_at_ms)
    assert d2.action == ExitAction.NO_CHANGE.value
    assert "cooling down" in d2.rationale
    # 5h later (fresh window): the same proposal may apply again.
    now3 = kl[99]["timestamp"] + TF
    assert now3 - now1 >= cfg.min_adjust_interval_s * 1000
    d3, _ = consult(kl[:100], plan1, now3, plan1.decided_at_ms)
    assert d3.action == ExitAction.REDUCE_PAPER_POSITION.value
    assert d3.plan_after["version"] == 2


def test_gate_close_bypasses_cooldown():
    # Breach 60s after a plan change: safety never waits for cooldown.
    closes = [100.0] * 25 + [101.5, 102.0, 102.5, 102.2, 102.4]
    kl = bars(closes)
    s = sig("SHORT", entry=100.0, sl=102.0, tp1=98.0, tp2=97.0, tp3=96.0)
    cfg = ExitConfig()
    plan = ExitPlan("SIG-TEST", 1, 102.0, 98.0, T0)
    c = _ctx_with_plan(s, kl, plan, PositionState.OPEN, cfg=cfg)
    now = c.now_ms
    plan = ExitPlan("SIG-TEST", 1, 102.0, 98.0, now - 60_000)
    sup = ExitSupervisor(cfg)
    view = _view_with_plan(s, plan, PositionState.OPEN)
    from src.exits.workers import ALL_WORKERS
    d = sup.decide(view, [w().assess(c) for w in ALL_WORKERS], c, now,
                   last_change_ts_ms=plan.decided_at_ms)
    assert d.action == ExitAction.CLOSE_PAPER_POSITION.value


# ── stability gate: single-TP pull + fill-aware merge ───────────
def test_gate_tp_pull_keeps_economics():
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
    kl = bars(closes)
    s = sig("LONG", entry=100.0, sl=99.0, tp1=110.0)
    cfg = ExitConfig()
    c = _ctx_with_plan(s, kl, ExitPlan.genesis(s), PositionState.OPEN, cfg=cfg)
    a = TargetWorker().assess(c)
    assert a.proposed_action == ExitAction.ADJUST_TAKE_PROFITS.value
    t = a.proposal["tp"]
    assert t > 100.0  # still on the profit side
    assert policy.gross_rr(100.0, 99.0, t) >= 1.0  # economics floor holds


def test_gate_merge_keeps_hit_tp():
    # tp hit at 102: the proposal's tp slot is stale and must not
    # overwrite history; an un-hit TP takes the proposal value.
    from src.exits.models import WorkerAssessment
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0)
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
    kl = bars(closes)
    cfg = ExitConfig()
    plan = ExitPlan.genesis(s)
    oc = {"tp1_hit": True}
    c = _ctx_with_plan(s, kl, plan, PositionState.OPEN, outcome=oc, cfg=cfg)
    prop = {"tp": 102.6}
    w = WorkerAssessment("target", "1.0.0", c.now_ms, c.data_ts_ms,
                         "TARGETS_UNREALISTIC", proposed_action="ADJUST_TAKE_PROFITS",
                         proposal=dict(prop), evidence_quality=0.9)
    sup = ExitSupervisor(cfg)
    view = _view_with_plan(s, plan, PositionState.OPEN, outcome=oc)
    d = sup.decide(view, [w], c, c.now_ms)
    # Merged levels equal the plan (hit TP preserved) → immaterial → kept.
    assert d.action == ExitAction.NO_CHANGE.value
    assert d.plan_after is None

    oc2 = {"tp1_hit": False}
    c2 = _ctx_with_plan(s, kl, plan, PositionState.OPEN, outcome=oc2, cfg=cfg)
    w2 = WorkerAssessment("target", "1.0.0", c2.now_ms, c2.data_ts_ms,
                          "TARGETS_UNREALISTIC", proposed_action="ADJUST_TAKE_PROFITS",
                          proposal=dict(prop), evidence_quality=0.9)
    view2 = _view_with_plan(s, plan, PositionState.OPEN, outcome=oc2)
    d2 = sup.decide(view2, [w2], c2, c2.now_ms)
    assert d2.action == ExitAction.ADJUST_TAKE_PROFITS.value
    assert d2.plan_after["tp"] == 102.6  # un-hit TP takes proposal


def test_gate_event_repeats_start_cooldown_in_replay():
    # End-to-end through make_decide_fn (the replay path): a persistent
    # REDUCE proposer must fire once, then be held back by the cooldown
    # even though the plan levels never changed (eval-v1 repeat bug).
    from src.exits.harness import make_decide_fn
    from src.exits.models import ExitPlan
    cfg = ExitConfig()
    decide = make_decide_fn(cfg)
    s = sig("LONG", entry=100.0, sl=99.0, tp1=102.0)
    # Flat series: stop/target/counter workers stay quiet (ATR~0 abstains,
    # no breaks); plan TP nearly exhausted so RR proposes REDUCE.
    kl = flat(40)
    plan = ExitPlan("SIG-TEST", 0, 99.0, 100.2, 0)
    fill = {"tp1_hit": False, "remaining": 1.0, "holding_bars": 3}
    dts1 = kl[-1]["close_time"]
    p1, e1 = decide(s, plan, fill, kl, dts1)
    # REDUCE applies: same levels at version 1 plus one reduce event.
    assert p1 is not None and p1.version == 1
    assert (p1.stop_loss, p1.tp) == (99.0, 100.2)
    assert len(e1) == 1 and e1[0]["kind"] == "reduce"
    # Next bar, same proposal: cooldown (last intervention 15m ago).
    kl2 = kl[1:] + [dict(kl[-1], timestamp=kl[-1]["timestamp"] + TF,
                         close_time=kl[-1]["close_time"] + TF)]
    dts2 = kl2[-1]["close_time"]
    p2, e2 = decide(s, p1, fill, kl2, dts2)
    assert p2 is None and e2 == []
    # 5h later: may fire again (version 2).
    kl3 = [dict(k, timestamp=k["timestamp"] + 5 * 3600_000,
                close_time=k["close_time"] + 5 * 3600_000) for k in kl2]
    dts3 = kl3[-1]["close_time"]
    p3, e3 = decide(s, p1, fill, kl3, dts3)
    assert p3 is not None and p3.version == 2 and len(e3) == 1


# ── §7: paper-only enforcement + pending/open discipline ─────────
def test_exits_package_has_no_execution_imports():
    # AST-level scan: docstring prose ("never import broker...") must NOT trip
    # the guard — only real imports and real call-site names count.
    import ast
    import glob
    bad_modules = {"requests", "urllib", "urllib3", "ccxt", "binance",
                   "websocket", "websockets", "http.client"}
    bad_names = {"place_order", "create_order", "execute_order", "share_paper_trade",
                 "send_message", "TelegramBot", "BinanceFuturesClient", "IMAP4",
                 "api_key", "api_secret", "PRIVATE_KEY"}
    hits = []
    for path in glob.glob(os.path.join("src", "exits", "*.py")):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if (a.name or "").split(".")[0] in bad_modules:
                        hits.append((os.path.basename(path), f"import {a.name}"))
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if mod in bad_modules:
                    hits.append((os.path.basename(path), f"from {node.module}"))
                for a in node.names:
                    if a.name in bad_names:
                        hits.append((os.path.basename(path), f"imports {a.name}"))
            elif isinstance(node, ast.Name) and node.id in bad_names:
                hits.append((os.path.basename(path), f"uses {node.id}"))
            elif isinstance(node, ast.Attribute) and node.attr in bad_names:
                hits.append((os.path.basename(path), f"calls .{node.attr}"))
    assert hits == [], f"execution-capable references in paper-only package: {hits}"


def test_pending_setup_cannot_be_tightened_or_closed():
    g = RiskGuard(ExitConfig())
    s = sig("LONG", entry=100.0, sl=98.0)
    view = PositionView(s, None, PositionState.PENDING, ExitPlan.genesis(s), False)
    ok, _ = g.validate(view, "TIGHTEN_STOP", {"stop_loss": 98.5})
    assert not ok
    ok, _ = g.validate(view, "CLOSE_PAPER_POSITION", {})
    assert not ok
    ok, _ = g.validate(view, "INVALIDATE_PENDING_SETUP", {})
    assert ok


def test_open_position_cannot_be_invalidated_as_pending():
    g = RiskGuard(ExitConfig())
    s = sig("LONG")
    view = PositionView(s, None, PositionState.OPEN, ExitPlan.genesis(s), False)
    ok, _ = g.validate(view, "INVALIDATE_PENDING_SETUP", {})
    assert not ok


def _stale_ctx(signal, completed):
    cfg = ExitConfig()
    return WorkerCtx(signal, float(signal["stop_loss"]), [float(signal["tp"])],
                     PositionState.OPEN, completed, T0, cfg,
                     fresh=False, freshness_reason="stale test")


def test_all_workers_abstain_at_zero_quality_on_stale_data():
    from src.exits.workers import ALL_WORKERS
    s = sig("LONG", entry=100.0, sl=98.0, tp=104.0)
    kl = bars([100.0] * 30)
    for cls in ALL_WORKERS:
        a = cls().assess(_stale_ctx(s, kl))
        assert a.verdict == "ABSTAIN", cls.ID
        assert a.evidence_quality == 0.0, cls.ID
        assert a.proposed_action is None, cls.ID


def test_fresh_assessment_keeps_discounted_quality():
    s = sig("LONG", entry=100.0, sl=98.0, tp=104.0)
    kl = bars([100.0] * 30)
    c = ctx_for(s, kl, state=PositionState.OPEN)
    a = StructureWorker().assess(c)
    assert a.verdict == "THESIS_INTACT"
    assert a.evidence_quality == 0.8  # fresh, full history, OI unavailable


def test_rr_dead_requires_downside_dominance():
    # Degenerate tight plan: all nets negative, but downside (1.8R) does
    # not dominate |upside| (1.0R) 2x -> REDUCE, not CLOSE.
    s = sig("LONG", entry=100.0, sl=99.9, tp=100.1)
    kl = bars([100.0] * 29 + [100.08])
    c = ctx_for(s, kl, state=PositionState.OPEN)
    a = RiskRewardWorker().assess(c)
    assert a.verdict == "RR_DEGRADED"
    assert a.proposed_action == ExitAction.REDUCE_PAPER_POSITION.value
    assert a.evidence["remaining_net_rr"][0] < 0


def test_rr_dead_still_closes_dominated_plan():
    # Price pinned near TP with fees exceeding the remainder and downside
    # far larger than |upside| -> CLOSE preserved.
    s = sig("LONG", entry=100.0, sl=98.0, tp=104.0)
    kl = bars([100.0] * 29 + [103.95])
    c = ctx_for(s, kl, state=PositionState.OPEN)
    a = RiskRewardWorker().assess(c)
    assert a.verdict == "RR_DEAD"
    assert a.proposed_action == ExitAction.CLOSE_PAPER_POSITION.value
