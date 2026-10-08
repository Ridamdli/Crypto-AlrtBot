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
