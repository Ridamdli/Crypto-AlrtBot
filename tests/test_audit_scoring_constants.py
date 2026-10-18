"""Self-check for the Phase 03-A audit script: component_breakdown must mirror
ScoringEngine.score_candidate exactly on every branch, and perturbations must
shift totals only through the overridden component."""
import importlib.util
import pathlib

from src.config import AppConfig
from src.engines.scoring_engine import ScoringEngine

_spec = importlib.util.spec_from_file_location(
    "audit_scoring_constants",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit_scoring_constants.py",
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _cand(**over):
    base = {
        "side": "LONG",
        "btc_regime": "neutral",
        "price_trend": "up",
        "volume_data": {"ratio": 1.6},
        "oi_data": {"status": "UNKNOWN", "signal": "neutral"},
        "funding_penalty": 0,
        "risk_data": {"risk_reward_ratios": {"rr_tp": 2.0}},
    }
    base.update(over)
    return base


def _cases(cfg):
    return [
        # full-aligned: 20+10+15+15+15+5+5+10 = 95 (cap not reached)
        _cand(btc_regime="bullish", volume_data={"ratio": 2.5},
              oi_data={"status": "CONFIRMED", "signal": "bullish"}),
        # neutral/mismatch/partial-volume/unknown-OI/funding-penalised: 10+10+7+10+0+0+5+10 = 52
        _cand(side="SHORT", price_trend="up", funding_penalty=15),
        # opposed regime/below-min volume/REJECTED OI/rr below both thresholds: 0+10+7+0-7+5+5+0 = 20
        _cand(btc_regime="bearish", price_trend="down", volume_data={"ratio": 0.5},
              oi_data={"status": "REJECTED", "signal": "bearish"},
              risk_data={"risk_reward_ratios": {"rr_tp": 1.5}}),
        # confirmed-opposite OI + partial funding penalty 3 -> max(0, 5-3)=2:
        # 10+10+15+15+7+2+5+10 = 74
        _cand(volume_data={"ratio": 2.0}, oi_data={"status": "CONFIRMED", "signal": "bearish"},
              funding_penalty=3),
        # momentum 'neutral' trend (neither up nor down) -> mismatch half
        _cand(price_trend="neutral"),
    ]


def _assert_matches_engine(cfg):
    eng = ScoringEngine(cfg)
    for cand in _cases(cfg):
        bd = audit.component_breakdown(cand, cfg)
        assert min(100, sum(bd.values())) == eng.score_candidate(cand, cfg), cand


def test_breakdown_matches_engine_default_config():
    _assert_matches_engine(AppConfig())


def test_breakdown_matches_engine_with_modified_thresholds():
    cfg = AppConfig()
    cfg.risk.min_rr = 1.5          # enables the rr partial branch (rr_tp 1.5 -> 7pts)
    cfg.volume.strong_ratio = 1.2  # moves the volume strong boundary
    _assert_matches_engine(cfg)


def test_breakdown_matches_engine_with_inflated_weights():
    cfg = AppConfig()
    cfg.scoring.regime_weight = 200  # forces the min(100, score) cap
    _assert_matches_engine(cfg)
    assert ScoringEngine(cfg).score_candidate(
        _cand(btc_regime="bullish"), cfg) == 100


def test_known_totals():
    cfg = AppConfig()
    eng = ScoringEngine(cfg)
    for cand, expected in zip(_cases(cfg), [95, 52, 20, 74, 57]):
        assert eng.score_candidate(cand, cfg) == expected, (cand, expected)


def test_perturbation_shifts_only_target_component():
    cfg = AppConfig()
    cand = _cand(btc_regime="bullish", volume_data={"ratio": 2.5},
                 oi_data={"status": "CONFIRMED", "signal": "bullish"})
    base = audit.component_breakdown(cand, cfg)
    overrides = audit.build_perturbations()["rm_structure_const (15pt component, constant 10 in code)"]
    pert = dict(base)
    for k, fn in overrides.items():
        pert[k] = fn(cand, pert[k])
    assert sum(pert.values()) == sum(base.values()) - base["structure"]
    assert {k: v for k, v in pert.items() if k != "structure"} == \
           {k: v for k, v in base.items() if k != "structure"}


def test_partition_bounds_cover_timeline_disjointly():
    n = 1000
    bounds = {p: audit.partition_bounds(n, p) for p in ("train", "val", "test")}
    # chronological, contiguous, no overlap: 60/20/20
    assert bounds["train"] == (0, 600)
    assert bounds["val"] == (600, 800)
    assert bounds["test"] == (800, 1000)
    assert sum(hi - lo for lo, hi in bounds.values()) == n
    for p, (lo, hi) in bounds.items():
        assert 0 <= lo < hi <= n, p
    # train keeps original artifact names; val/test get suffixed
    assert audit.audit_paths("train") == (audit.CANDIDATES_JSON, audit.TABLES_MD)
    cj, tm = audit.audit_paths("val")
    assert cj.endswith("_val.json") and tm.endswith("_val.md")
    cj, tm = audit.audit_paths("test")
    assert cj.endswith("_test.json") and tm.endswith("_test.md")
