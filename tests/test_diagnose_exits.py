"""Self-check for the Phase 03-B diagnose script: classification, rule
mapping and worker attribution must be exhaustive and directionally correct —
the whole diagnosis rests on these three pure functions."""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "diagnose_exits",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "diagnose_exits.py",
)
dx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dx)

from src.exits.models import ExitAction  # noqa: E402


def test_signed_move_r_direction_and_short():
    # LONG: end above ref is favorable (+); SHORT: mirrored.
    assert dx.signed_move_r("LONG", 100.0, [110.0], 20.0) == 0.5
    assert dx.signed_move_r("SHORT", 100.0, [110.0], 20.0) == -0.5
    # horizon slice: only the 24th bar counts, not the last one in the list
    long_tail = [100.0] * 23 + [104.0, 999.0]
    assert dx.signed_move_r("LONG", 100.0, long_tail, 16.0) == 0.25
    # degenerate inputs -> None (UNSCORED downstream)
    assert dx.signed_move_r("LONG", None, [101.0], 20.0) is None
    assert dx.signed_move_r("LONG", 100.0, [], 20.0) is None
    assert dx.signed_move_r("LONG", 100.0, [101.0], 0.0) is None
    # fewer bars than the horizon: use what exists
    assert dx.signed_move_r("LONG", 100.0, [98.0], 20.0) == -0.1


def test_classify_exhaustive():
    C, P, M, N, U = ("CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED")
    # adopted action: directional judgment only
    assert dx.classify("CLOSE_PAPER_POSITION", True, -0.3) == C
    assert dx.classify("TIGHTEN_STOP", False, 0.3) == P
    assert dx.classify("ADJUST_TAKE_PROFITS", False, -0.1) == N
    # boundary: |move| < 0.25 is NEUTRAL even for an adopted close
    assert dx.classify("CLOSE_PAPER_POSITION", True, -0.249) == N
    assert dx.classify("CLOSE_PAPER_POSITION", True, 0.249) == N
    # no action + exit proposal: worsened -> MISSED, improved -> CORRECT
    assert dx.classify("NO_CHANGE", True, -0.25) == M
    assert dx.classify("FLAG_CONFLICT", True, -0.5) == M
    assert dx.classify("NO_CHANGE", True, 0.3) == C
    # no action + no proposal: never MISSED, never CORRECT
    assert dx.classify("NO_CHANGE", False, -2.0) == N
    assert dx.classify("NO_CHANGE", False, 2.0) == N
    # boundary on MISSED (exactly -0.25 counts)
    assert dx.classify("NO_CHANGE", True, -0.25) == M
    assert dx.classify("NO_CHANGE", True, -0.249) == N
    assert dx.classify("NO_CHANGE", True, None) == U
    assert dx.classify("CLOSE_PAPER_POSITION", True, None) == U


def test_rule_of_exhaustive():
    assert dx.rule_of("INSUFFICIENT_DATA", "Retaining plan: market data unusable (x).") == "R1_data"
    assert dx.rule_of("INVALIDATE_PENDING_SETUP", "Invalidating pending setup: y.") == "R2_safety"
    r2 = ("Closing paper position: completed close 1 beyond active stop 2. "
          "The market printed through the plan's own invalidation level on completed candles.")
    assert dx.rule_of("CLOSE_PAPER_POSITION", r2) == "R2_safety"
    r3 = ("Closing paper position: uncontradicted reversal evidence with "
          "material damage. No worker defends the thesis.")
    assert dx.rule_of("CLOSE_PAPER_POSITION", r3) == "R3_reversal"
    assert dx.rule_of("FLAG_CONFLICT", "Flagging conflict: ...") == "R3_reversal"
    assert dx.rule_of("TIGHTEN_STOP", "Applying TIGHTEN_STOP proposed by stop (...)") == "R4_adjust"
    assert dx.rule_of("REDUCE_PAPER_POSITION", "Applying REDUCE... proposed by riskreward") == "R4_adjust"
    guard = "Retaining plan: stop proposed TIGHTEN_STOP but RiskGuard rejected it (x)."
    assert dx.rule_of("NO_CHANGE", guard) == "R4_guard_rejected"
    assert dx.rule_of("NO_CHANGE", "Retaining plan v1 for open position: ...") == "R5_default"
    # unknown CLOSE rationale must surface as UNKNOWN (caller counts these)
    assert dx.rule_of("CLOSE_PAPER_POSITION", "something unforeseen") == "UNKNOWN"
    assert dx.rule_of("SOMETHING_ELSE", "whatever") == "UNKNOWN"


def test_adopted_worker_attribution():
    a = [{"worker_id": "stop", "verdict": "x",
          "proposed_action": ExitAction.TIGHTEN_STOP.value}]
    assert dx.adopted_worker("TIGHTEN_STOP", "Applying TIGHTEN_STOP proposed by stop", a) == "stop"
    assert dx.adopted_worker(
        "CLOSE_PAPER_POSITION",
        "Closing paper position: the market printed through the plan's own level", a) == "structure"
    assert dx.adopted_worker(
        "CLOSE_PAPER_POSITION",
        "Closing paper position: uncontradicted reversal evidence", a) == "counter"
    assert dx.adopted_worker("FLAG_CONFLICT", "Flagging conflict: ...", a) == "counter"
    assert dx.adopted_worker("NO_CHANGE", "Retaining plan ...", a) is None


def test_classify_record_missed_rr_dead():
    # The core 03-B case: riskreward proposes CLOSE (RR_DEAD), supervisor
    # returns NO_CHANGE, price then worsens -> MISSED, no worker credited.
    rec = {
        "side": "LONG", "ref_close": 100.0, "action": "NO_CHANGE",
        "rationale": "Retaining plan v1 for open position: riskreward=RR_DEAD. ...",
        "assessments": [
            {"worker_id": "riskreward", "verdict": "RR_DEAD",
             "proposed_action": ExitAction.CLOSE_PAPER_POSITION.value,
             "evidence_quality": 1.0},
            {"worker_id": "structure", "verdict": "THESIS_INTACT",
             "proposed_action": None, "evidence_quality": 1.0},
        ],
    }
    future = [96.0]  # -4 / risk 20 => -0.2R... use 20 risk => -0.2 -> NEUTRAL
    dx.classify_record(rec, future, 20.0)
    assert rec["class"] == "NEUTRAL" and rec["rule"] == "R5_default"
    assert rec["adopted_by"] is None and rec["has_exit_proposal"] is True

    rec2 = dict(rec, move_r=None)
    rec2["assessments"] = rec["assessments"]
    dx.classify_record(rec2, [94.0], 20.0)  # -6/20 = -0.3R -> MISSED
    assert rec2["class"] == "MISSED"
