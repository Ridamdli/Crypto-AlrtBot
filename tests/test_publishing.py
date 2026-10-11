"""Regression tests for uncapped publishing and scan heartbeat."""
import json
import os

from src.engines.scoring_engine import ScoringEngine
from src.scheduler import BotScheduler


def _cand(conf):
    c = {"symbol": "BTCUSDT", "side": "LONG", "confidence": conf}
    return c


def test_rank_candidates_uncapped_returns_all_viable():
    eng = ScoringEngine.__new__(ScoringEngine)
    eng.min_confidence = 60
    # bypass scoring: pre-set confidence and stub score_candidate
    eng.score_candidate = lambda cand, config=None: cand["confidence"]
    cands = [_cand(90), _cand(80), _cand(70), _cand(60), _cand(59)]
    out = eng.rank_candidates(cands, top_n=None)
    assert [c["confidence"] for c in out] == [90, 80, 70, 60]


def test_rank_candidates_top_n_still_caps_when_asked():
    eng = ScoringEngine.__new__(ScoringEngine)
    eng.min_confidence = 60
    eng.score_candidate = lambda cand, config=None: cand["confidence"]
    cands = [_cand(90), _cand(80), _cand(70)]
    out = eng.rank_candidates(cands, top_n=2)
    assert [c["confidence"] for c in out] == [90, 80]


def _full_cand():
    return {
        "signal_id": "SIG-WIRE-1",
        "timestamp": "2026-10-11T12:00:00Z",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "strategy_id": "donchian_20",
        "entry": 110.20,
        "tp1": 112.40,
        "tp2": 113.50,
        "tp3": 114.60,
        "stop_loss": 108.55,
        "leverage": 5,
        "margin": 20.0,
        "position_notional": 100.0,
        "risk_amount": 1.65,
        "risk_reward_tp1": 1.33,
        "risk_reward_tp2": 2.0,
        "risk_reward_tp3": 2.67,
        "confidence": 75,
        "conditions": ["test"],
        "invalidation_conditions": ["test"],
        "management_rules": ["test"],
    }


def test_scan_cycle_routes_published_signals_to_paper_share(tmp_path):
    """run_scan_cycle must hand published signals to _share_paper_to_invo.

    Regression: the cycle once called the dead _share_to_invo hook (gated on
    unset INVO_ENABLED), so Telegram fired while Invo never attempted a post.
    """
    from types import SimpleNamespace

    state_file = str(tmp_path / "sched_state.json")

    class OnePipeline:
        def run_pipeline(self):
            return [_full_cand()]

    sched = BotScheduler(pipeline=OnePipeline(), state_file=state_file)
    sched.bot = SimpleNamespace(send_message=lambda msg: None)
    sched.persistence = SimpleNamespace(save_signal=lambda sig: None)
    sched._track_outcomes = lambda: None
    seen = {}
    sched._share_paper_to_invo = lambda published: seen.setdefault("n", len(published))
    out = sched.run_scan_cycle()
    assert len(out) == 1
    assert seen.get("n") == 1
    assert not hasattr(sched, "_share_to_invo")


def test_empty_scan_cycle_writes_heartbeat(tmp_path):
    state_file = str(tmp_path / "sched_state.json")

    class EmptyPipeline:
        def run_pipeline(self):
            return []

    sched = BotScheduler(pipeline=EmptyPipeline(), state_file=state_file)
    out = sched.run_scan_cycle()
    assert out == []
    hb_path = os.path.join(str(tmp_path), "scan_history.json")
    assert os.path.exists(hb_path)
    with open(hb_path, encoding="utf-8") as f:
        history = json.load(f)
    assert history[-1]["status"] == "ok"
    assert history[-1]["candidates"] == 0
    assert history[-1]["published"] == 0
