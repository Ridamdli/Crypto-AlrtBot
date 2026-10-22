"""W-DD Option A: the published score must be explainable from the record.

Persistence-only change: `confidence` keeps its exact pre-existing value, and
`scoring_breakdown` is added beside it. Audits join outcome rows by `signal_id`
(pariston_outcomes.json is an evidence export written outside this tree).
"""
import json

from src.config import CONFIG
from src.engines.scoring_engine import SCORING_ENGINE_VERSION, ScoringEngine
from src.models.signal import SignalModel


def _cand(**over):
    cand = {
        "signal_id": "SIG-20261022-AAA111",
        "timestamp": "2026-10-22T00:00:00Z",
        "symbol": "BTCUSDT",
        "side": "SHORT",
        "strategy_id": "momentum_volume_v1",
        "entry": 100.0,
        "tp": 96.0,
        "stop_loss": 102.0,
        "leverage": 5,
        "margin": 20.0,
        "position_notional": 100.0,
        "risk_amount": 1.0,
        "risk_reward": 2.0,
        "conditions": [],
        "invalidation_conditions": [],
        "management_rules": [],
        "btc_regime": "bearish",
        "price_trend": "down",
        "volume_data": {"ratio": 1.9256},
        "oi_data": {"status": "UNKNOWN", "signal": "neutral", "oi_change_pct": 0.3567},
        "risk_data": {"risk_reward_ratios": {"rr_tp": 2.0}},
        "funding_penalty": 0,
        "funding_rate": -0.0000332,
    }
    cand.update(over)
    return cand


def test_rank_candidates_records_a_self_consistent_breakdown():
    eng = ScoringEngine(config=CONFIG)
    [out] = eng.rank_candidates([_cand()], top_n=None, config=CONFIG)

    bd = out["scoring_breakdown"]
    assert bd["engine"] == SCORING_ENGINE_VERSION
    assert set(bd["points"]) == {
        "regime", "structure", "momentum", "volume", "oi", "funding", "liquidity", "rr"}
    assert sum(bd["points"].values()) == bd["total_raw"] == out["confidence"]
    assert bd["clamped"] is False
    assert bd["inputs"]["btc_regime"] == "bearish"
    assert bd["inputs"]["rr_tp"] == 2.0


def test_score_is_unchanged_by_the_breakdown():
    """The refactor must not move any published number."""
    eng = ScoringEngine(config=CONFIG)
    cases = [_cand(), _cand(btc_regime="neutral", price_trend="up"),
             _cand(oi_data={"status": "REJECTED", "signal": "bearish"}),
             _cand(volume_data={"ratio": 0.1})]
    for cand in cases:
        bd = eng.score_breakdown(cand, CONFIG)
        assert eng.score_candidate(cand, CONFIG) == min(100, bd["total_raw"])


def test_legacy_signal_without_breakdown_loads_as_none():
    """Rows written before the contract must load, not fail validation."""
    legacy = {k: v for k, v in _cand().items() if k != "scoring_breakdown"}
    legacy["confidence"] = 67
    sig = SignalModel(**legacy)
    assert sig.scoring_breakdown is None
    assert sig.confidence == 67


def test_round_trip_preserves_the_breakdown():
    eng = ScoringEngine(config=CONFIG)
    cand = _cand()
    cand["confidence"] = eng.score_candidate(cand, CONFIG)
    cand["scoring_breakdown"] = eng.score_breakdown(cand, CONFIG)

    sig = SignalModel(**cand)
    reloaded = SignalModel(**json.loads(json.dumps(sig.model_dump())))
    assert reloaded.scoring_breakdown == sig.scoring_breakdown
    assert (reloaded.scoring_breakdown["total_raw"]
            == reloaded.confidence == cand["confidence"])


def test_outcome_joins_to_breakdown_by_signal_id():
    """The chosen evidence path: outcome rows carry signal_id, so an audit
    joins them onto the persisted signal row instead of duplicating the
    breakdown into the export."""
    eng = ScoringEngine(config=CONFIG)
    cand = _cand()
    cand["confidence"] = eng.score_candidate(cand, CONFIG)
    cand["scoring_breakdown"] = eng.score_breakdown(cand, CONFIG)
    signals = [SignalModel(**cand).model_dump()]

    # Shape of an outcomes ledger row, as written by OutcomeTracker.
    outcomes = {cand["signal_id"]: {"signal_id": cand["signal_id"],
                                    "symbol": "BTCUSDT", "net_r": 1.8}}

    by_id = {s["signal_id"]: s for s in signals}
    joined = [dict(o, breakdown=by_id[o["signal_id"]]["scoring_breakdown"])
              for o in outcomes.values() if o["signal_id"] in by_id]

    assert len(joined) == 1
    assert joined[0]["breakdown"]["total_raw"] == cand["confidence"]
    assert joined[0]["net_r"] == 1.8
