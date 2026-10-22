"""Phase 04A harness tests: correlation math, proxy bounds, determinism."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_component_proxy",
    Path(__file__).resolve().parents[1] / "scripts" / "evaluate_component_proxy.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


def test_pearson_known_values():
    assert abs(ev.pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-12
    assert abs(ev.pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-12
    assert ev.pearson([1, 1, 1], [1, 2, 3]) is None  # zero variance is undefined


def test_spearman_is_rank_based():
    # Perfectly monotone but non-linear: Pearson < 1, Spearman == 1.
    xs = [1, 2, 3, 4, 5]
    ys = [1, 8, 27, 64, 125]
    assert ev.pearson(xs, ys) < 1.0
    assert abs(ev.spearman(xs, ys) - 1.0) < 1e-12
    assert abs(ev.spearman(xs, list(reversed(ys))) + 1.0) < 1e-12


def test_percentile_rank_and_ties():
    assert ev.percentile_rank([1, 2, 3, 4]) == [0.0, 1 / 3, 2 / 3, 1.0]
    assert ev.percentile_rank([5, 5, 5]) == [0.5, 0.5, 0.5]
    assert ev.percentile_rank([7]) == [0.5]


def test_decisiveness_and_gate_flips():
    scores = [60, 62, 65, 67, 72]
    assert ev.rank_change_pct(scores, scores) == 0.0
    # Reversing 5 items leaves the middle one in place.
    assert ev.rank_change_pct(scores, list(reversed(scores))) == 80.0
    assert ev.gate_flip_pct([60, 59], [60, 59]) == 0.0
    assert ev.gate_flip_pct([60, 59], [58, 61]) == 100.0


def test_bootstrap_is_deterministic():
    xs = list(range(20))
    ys = [(x * 0.3 + (-1) ** x) for x in xs]
    a = ev.bootstrap_ci(xs, ys, ev.pearson, n=200, seed=11)
    b = ev.bootstrap_ci(xs, ys, ev.pearson, n=200, seed=11)
    assert a == b and a is not None and a[0] < a[1]


def test_verdict_rules():
    base = {"proxy_pearson": 0.0, "rank_change_pct": 0.0, "redundancy": None,
            "current_variance": True, "baseline_score_corr": 0.1,
            "removal": {"score_corr_without": 0.1}}
    assert ev.verdict({**base, "proxy_pearson": 0.12, "rank_change_pct": 12.0}) == "ADOPT"
    assert ev.verdict({**base, "proxy_pearson": 0.03,
                       "removal": {"score_corr_without": 0.12}}) == "DISCARD-COMPONENT"
    assert ev.verdict({**base, "proxy_pearson": 0.07}) == "KEEP-CURRENT"
    assert ev.verdict({**base, "proxy_pearson": 0.5, "redundancy": 0.95}) == "KEEP-CURRENT"
    assert ev.verdict({"verdict": "DATA-INSUFFICIENT"}) == "DATA-INSUFFICIENT"


def test_proxy_bounds_and_signs():
    rising = [100 * (1.002 ** i) for i in range(30)]
    falling = list(reversed(rising))
    _, long_pts = ev.structure_proxy(rising, "LONG")
    _, short_pts = ev.structure_proxy(rising, "SHORT")
    assert 0.0 <= short_pts < 7.5 < long_pts <= 15.0
    assert ev.structure_proxy(falling, "SHORT")[1] > 7.5
    assert ev.structure_proxy([1.0], "LONG") == (None, None)

    # OI proxy: rising OI with an up-trend rewards LONG, punishes SHORT.
    assert ev.oi_proxy(3.0, "up", "LONG")[1] > 7.5
    assert ev.oi_proxy(3.0, "up", "SHORT")[1] < 7.5
    assert 0.0 <= ev.oi_proxy(50.0, "up", "LONG")[1] <= 15.0

    # Current funding is binary on the *adverse* rate (side-adjusted by caller).
    assert ev.funding_points(0.0) == 5
    assert ev.funding_points(0.0099) == 5
    assert ev.funding_points(0.01) == 0
    assert ev.funding_points(0.02) == 0
    assert ev.funding_points(-0.02) == 5  # favourable funding earns no penalty


def _synthetic(tmp_path, n=140):
    t0 = 1_700_000_000_000
    ts15 = [t0 + i * 900_000 for i in range(n)]
    ts1h = [t0 + i * 3_600_000 for i in range(n)]
    closes = [100 + i * 0.05 for i in range(n)]
    vols = [10.0 + (i % 7) for i in range(n)]
    market = {
        "15m": {"AAAUSDT": {"ts": ts15, "close": closes, "volume": vols}},
        "1h": {"AAAUSDT": {"ts": ts1h, "close": closes, "volume": vols}},
        "4h": {},
    }
    stamp = datetime.fromtimestamp(ts15[-1] / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    spec = [("LONG", "neutral", 65), ("SHORT", "bearish", 72),
            ("LONG", "bearish", 62), ("SHORT", "neutral", 67)]
    signals = []
    for i in range(12):
        side, regime, conf = spec[i % 4]
        signals.append({
            "signal_id": f"SIG-{i}", "symbol": "AAAUSDT", "side": side,
            "strategy_id": "donchian_20", "btc_regime": regime,
            "signal_timestamp": stamp, "confidence": conf,
            "net_realized_r": ((i * 7) % 13 - 6) / 4.0,
            "status": "TP1_HIT", "entry": 100.0, "stop_loss": 99.0, "tp": 102.0,
        })
    funding = {"AAAUSDT": {"ts": ts15, "value": [0.00001] * n}}
    oi = {"AAAUSDT": {"ts": ts15, "value": [1000 + i * 10 for i in range(n)]}}
    (tmp_path / "funding_AAAUSDT.json").write_text(
        json.dumps({str(t): 0.00001 for t in ts15}), encoding="utf-8")
    return signals, market, funding, oi


def test_full_evaluation_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "CACHE", tmp_path)
    signals, market, funding, oi = _synthetic(tmp_path)

    rows_a = ev.component_rows(signals, market, funding, oi)
    first = json.dumps(ev.evaluate(rows_a, market), sort_keys=True)
    rows_b = ev.component_rows(signals, market, funding, oi)
    second = json.dumps(ev.evaluate(rows_b, market), sort_keys=True)

    assert len(rows_a) == 12
    assert first == second
    # Every component was scored, and the verdict vocabulary is respected.
    for name in ("structure", "liquidity", "funding", "oi", "regime"):
        assert "verdict" in json.loads(first)[name]
        assert json.loads(first)[name]["verdict"] in {
            "ADOPT", "KEEP-CURRENT", "DISCARD-COMPONENT", "DATA-INSUFFICIENT"}
