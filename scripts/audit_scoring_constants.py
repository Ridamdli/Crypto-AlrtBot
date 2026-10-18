#!/usr/bin/env python
"""
Phase 03-A: Scoring constants audit (replay-evidence based, proposal-only).

capture: one TRAIN replay pass under the CURRENT config, stashing every pre-gate
candidate (before the min_confidence filter) plus its simulated outcome and a
1h-trend structure proxy, -> reports/scoring_audit_candidates.json

analyze: loads that JSON and computes, per scoring component and per constant
perturbation, outcome correlations and gate-crossing sensitivity
-> reports/scoring_audit_tables.md  (narrative lives in SCORING_CONSTANTS_AUDIT.md)

No production constant is changed by this script. Replay is fully offline via
CachedFuturesClient + explicit symbol list (bypasses universe filter network call).
"""
import argparse
import copy
import json
import logging
import os
import time
from typing import Any

from src.config import AppConfig
from src.data.cached_client import CachedFuturesClient
from src.pipeline import SignalPipeline
from src.engines.scoring_engine import ScoringEngine
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.utils.logger import get_logger

logger = get_logger(__name__)

CANDIDATES_JSON = "reports/scoring_audit_candidates.json"
TABLES_MD = "reports/scoring_audit_tables.md"


def audit_paths(partition: str) -> tuple[str, str]:
    """Val/test captures get a suffix; train keeps the original filenames."""
    if partition == "train":
        return CANDIDATES_JSON, TABLES_MD
    return (CANDIDATES_JSON.replace(".json", f"_{partition}.json"),
            TABLES_MD.replace(".md", f"_{partition}.md"))


def partition_bounds(n: int, partition: str) -> tuple[int, int]:
    """Chronological 60/20/20 split (same TRAIN boundary as the confidence
    experiment); order is time, never shuffled."""
    cut1, cut2 = int(n * 0.6), int(n * 0.8)
    return {"train": (0, cut1), "val": (cut1, cut2),
            "test": (cut2, n)}[partition]

# BTCUSDT 4h klines in the replay DB start here; before this timestamp the BTC
# regime is structurally NEUTRAL for every candidate (no 4h data to judge).
BTC_4H_START_MS = 1783396800000

SILENCED = [
    "src.pipeline", "src.engines.volume_engine", "src.engines.oi_engine",
    "src.engines.funding_engine", "src.engines.btc_regime", "src.engines.scoring_engine",
    "src.engines.tp_engine", "src.engines.risk_engine",
    "src.data.timeframe_manager", "src.data.ohlcv_fetcher", "src.data.futures_data",
    "src.data.universe_filter",
]


def silence_logs() -> None:
    for mod in SILENCED:
        logging.getLogger(mod).setLevel(logging.ERROR)


def generate_timestamps(start_ts: int, end_ts: int, interval_ms: int = 900000) -> list[int]:
    ts = start_ts
    remainder = ts % interval_ms
    if remainder != 0:
        ts += (interval_ms - remainder)
    out = []
    while ts <= end_ts:
        out.append(ts)
        ts += interval_ms
    return out


def fetch_future_klines(client: CachedFuturesClient, symbol: str, start_ts: int, limit: int = 100) -> list[dict]:
    with client.store.get_connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='15m' AND timestamp > ? ORDER BY timestamp ASC LIMIT ?",
            (symbol, start_ts, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_1h_proxy_trend(client: CachedFuturesClient, symbol: str, end_ts: int, lookback: int = 21) -> str | None:
    """Structure proxy candidate: net 1h close change over `lookback` bars."""
    with client.store.get_connection() as conn:
        rows = conn.execute(
            "SELECT close FROM klines WHERE symbol=? AND interval='1h' AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (symbol, end_ts, lookback),
        ).fetchall()
    closes = [float(r["close"]) for r in rows][::-1]
    if len(closes) < lookback:
        return None
    return "up" if closes[-1] > closes[0] else ("down" if closes[-1] < closes[0] else "flat")


def component_breakdown(cand: dict[str, Any], config: AppConfig) -> dict[str, int]:
    """Per-component point breakdown, mirroring ScoringEngine.score_candidate exactly."""
    sc = config.scoring
    side = cand.get("side", "LONG")
    regime = cand.get("btc_regime", "neutral")

    if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
        regime_pts = sc.regime_weight
    elif regime == "neutral":
        regime_pts = sc.regime_weight // 2
    else:
        regime_pts = 0

    structure_pts = int(sc.structure_weight * 0.7)

    price_trend = cand.get("price_trend", "")
    if (side == "LONG" and price_trend == "up") or (side == "SHORT" and price_trend == "down"):
        momentum_pts = sc.momentum_weight
    else:
        momentum_pts = sc.momentum_weight // 2

    ratio = cand.get("volume_data", {}).get("ratio", 0)
    if ratio >= config.volume.strong_ratio:
        volume_pts = sc.volume_weight
    elif ratio >= config.volume.min_ratio:
        volume_pts = int(sc.volume_weight * 0.7)
    else:
        volume_pts = 0

    oi_data = cand.get("oi_data", {})
    oi_status = oi_data.get("status", "UNKNOWN")
    oi_signal = oi_data.get("signal", "neutral")
    if oi_status == "CONFIRMED":
        if (side == "LONG" and oi_signal == "bullish") or (side == "SHORT" and oi_signal == "bearish"):
            oi_pts = sc.oi_weight
        else:
            oi_pts = sc.oi_weight // 2
    elif oi_status == "REJECTED":
        oi_pts = -(sc.oi_weight // 2)
    else:
        oi_pts = 0

    funding_pts = max(0, sc.funding_weight - cand.get("funding_penalty", 0))
    liquidity_pts = sc.liquidity_weight

    rr = cand.get("risk_data", {}).get("risk_reward_ratios", {}).get("rr_tp", 0)
    if rr >= 2.0:
        rr_pts = sc.rr_weight
    elif rr >= config.risk.min_rr:
        rr_pts = int(sc.rr_weight * 0.7)
    else:
        rr_pts = 0

    return {
        "regime": regime_pts, "structure": structure_pts, "momentum": momentum_pts,
        "volume": volume_pts, "oi": oi_pts, "funding": funding_pts,
        "liquidity": liquidity_pts, "rr": rr_pts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# capture
# ─────────────────────────────────────────────────────────────────────────────
def capture(stride: int, limit_ts: int | None, partition: str = "train") -> None:
    silence_logs()
    os.makedirs("reports", exist_ok=True)

    config = AppConfig()
    config.oi.mode = "optional"
    config.validate()
    client = CachedFuturesClient()

    from src.data.timeframe_manager import TimeframeManager

    def fast_fetch_multi(self, symbol: str, limit: int = 100, end_time=None):
        return {
            tf: self.fetcher.fetch_standardized_klines(symbol, tf, limit, end_time=end_time)
            for tf in self.timeframes
        }

    TimeframeManager.fetch_multi_timeframe = fast_fetch_multi

    pipeline = SignalPipeline(config=config, client=client)
    engine = pipeline.scoring_engine

    captured: list[tuple[int, list[dict[str, Any]]]] = []
    # Candidate dicts carry an ISO "timestamp" string; SQLite compares it as TEXT
    # against the INTEGER ms column (always > / always <), silently returning the
    # wrong rows. All DB lookups must use the int-ms as_of ts, tracked here.
    current_ts: dict[str, int] = {}

    orig_rank = ScoringEngine.rank_candidates

    def wrapped_rank(self, candidates, top_n=None, config=None):
        out = orig_rank(self, candidates, top_n, config)
        if candidates:
            captured.append((current_ts["ts"], copy.deepcopy(candidates)))
        return out

    ScoringEngine.rank_candidates = wrapped_rank
    try:
        with client.store.get_connection() as conn:
            g_start, g_end = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM klines WHERE interval='15m'"
            ).fetchone()
            db_symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM klines ORDER BY symbol")]
        symbols = [s for s in db_symbols if pipeline.symbol_registry.is_tradable(s)]

        scan_end = g_end - (3 * 24 * 60 * 60 * 1000)
        all_ts = generate_timestamps(g_start, scan_end)
        lo, hi = partition_bounds(len(all_ts), partition)
        train_ts = all_ts[lo:hi][::stride]
        if limit_ts:
            train_ts = train_ts[:limit_ts]

        meta = {
            "config_hash": config.compute_hash(),
            "min_confidence": config.signals.min_confidence,
            "window_train": [train_ts[0], train_ts[-1]],
            "partition": partition,
            "n_timestamps": len(train_ts),
            "stride": stride,
            "symbols": symbols,
            "btc_4h_start_ms": BTC_4H_START_MS,
        }
        logger.info(f"Capture: {len(train_ts)} timestamps x {len(symbols)} symbols, config {meta['config_hash']}")

        evaluator = SignalOutcomeEvaluator()
        t0 = time.time()
        for i, ts in enumerate(train_ts):
            current_ts["ts"] = ts
            pipeline.run_pipeline(as_of_time=ts, symbols=symbols)
            if i % 200 == 0:
                print(f"  [{i}/{len(train_ts)}] {time.time()-t0:.0f}s, captured batches={len(captured)}", flush=True)
    finally:
        ScoringEngine.rank_candidates = orig_rank

    # flatten + evaluate outcomes
    records = []
    n_assert_fail = 0
    n_empty_future = 0
    for ts, batch in captured:
        for cand in batch:
            breakdown = component_breakdown(cand, config)
            total = min(100, sum(breakdown.values()))
            if total != cand["confidence"]:
                n_assert_fail += 1
                if n_assert_fail <= 3:
                    print(f"SELF-CHECK FAIL: breakdown={total} != score_candidate={cand['confidence']} {cand['symbol']}")
            future = fetch_future_klines(client, cand["symbol"], ts, limit=100)
            if len(future) < 100:
                n_empty_future += 1
            outcome = evaluator.evaluate_signal(cand, future)
            proxy = fetch_1h_proxy_trend(client, cand["symbol"], ts)
            proxy_aligned = None
            if proxy in ("up", "down"):
                proxy_aligned = (cand["side"] == "LONG" and proxy == "up") or (
                    cand["side"] == "SHORT" and proxy == "down"
                )
            records.append({
                "timestamp": ts,
                "symbol": cand["symbol"],
                "side": cand["side"],
                "strategy_id": cand["strategy_id"],
                "confidence": cand["confidence"],
                "components": breakdown,
                "btc_regime": cand.get("btc_regime"),
                "price_trend": cand.get("price_trend"),
                "volume_ratio": cand.get("volume_data", {}).get("ratio"),
                "funding_penalty": cand.get("funding_penalty", 0),
                "rr_tp": cand.get("risk_data", {}).get("risk_reward_ratios", {}).get("rr_tp"),
                "oi_status": cand.get("oi_data", {}).get("status", "UNKNOWN"),
                "proxy_1h_aligned": proxy_aligned,
                "outcome": {
                    "entry_triggered": outcome["entry_triggered"],
                    "first_event": outcome["first_event"],
                    "net_realized_r": outcome["net_realized_r"],
                },
            })

    if n_assert_fail:
        raise SystemExit(f"ABORT: {n_assert_fail} breakdown self-check failures — audit numbers would be invalid")
    if n_empty_future:
        # scan_end is 3d before data end, so every candidate should see >=100 future bars
        raise SystemExit(f"ABORT: {n_empty_future} candidates had <100 future klines — outcome eval invalid")
    meta["n_candidates"] = len(records)
    with open(audit_paths(partition)[0], "w") as f:
        json.dump({"meta": meta, "candidates": records}, f)
    print(f"captured {len(records)} candidates -> {audit_paths(partition)[0]}")


# ─────────────────────────────────────────────────────────────────────────────
# analyze
# ─────────────────────────────────────────────────────────────────────────────
def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sxy / (sxx * syy) ** 0.5


def set_stats(cands: list[dict]) -> dict:
    if not cands:
        return {"n": 0, "expected_r": None, "trigger_rate": None, "win_rate_triggered": None}
    n = len(cands)
    exp_r = sum(c["outcome"]["net_realized_r"] for c in cands) / n
    trig = [c for c in cands if c["outcome"]["entry_triggered"]]
    wins = [c for c in trig if c["outcome"]["net_realized_r"] > 0]
    return {
        "n": n,
        "expected_r": round(exp_r, 4),
        "trigger_rate": round(len(trig) / n, 3),
        "win_rate_triggered": round(len(wins) / len(trig), 3) if trig else None,
    }


COMPONENTS = ["regime", "structure", "momentum", "volume", "oi", "funding", "liquidity", "rr"]

# perturbation name -> {component: replacement value fn(cand, base_comp_value)}
def build_perturbations() -> dict[str, dict]:
    return {
        "rm_structure_const (15pt component, constant 10 in code)": {
            "structure": lambda c, v: 0,
        },
        "structure -> measured 1h-trend proxy (15 if aligned else 0)": {
            "structure": lambda c, v: 15 if c.get("proxy_1h_aligned") else 0,
        },
        "rm_momentum_mismatch_half (mismatch 7 -> 0)": {
            "momentum": lambda c, v: v if v == 15 else 0,
        },
        "rm_volume_strong_bonus (strong/partial both -> 10)": {
            "volume": lambda c, v: 10,
        },
        "rm_oi_component (-> 0, currently constant 0 in this evidence base)": {
            "oi": lambda c, v: 0,
        },
        "funding_clamp_removed (5 - penalty, allows -10)": {
            "funding": lambda c, v: 5 - c.get("funding_penalty", 0),
        },
        "funding_symmetric (+5 / -5)": {
            "funding": lambda c, v: 5 if c.get("funding_penalty", 0) == 0 else -5,
        },
        "rm_funding_component (-> 0)": {
            "funding": lambda c, v: 0,
        },
        "rm_liquidity_const (5pt component)": {
            "liquidity": lambda c, v: 0,
        },
        "rm_rr_const (10pt component, rr_tp always >= 2.0 post-gate)": {
            "rr": lambda c, v: 0,
        },
        "regime_neutral_zero (neutral 10 -> 0)": {
            "regime": lambda c, v: 0 if v == 10 else v,
        },
        "regime_neutral_full (neutral 10 -> 20)": {
            "regime": lambda c, v: 20 if v == 10 else v,
        },
    }


def analyze(partition: str = "train") -> None:
    candidates_json, tables_md = audit_paths(partition)
    with open(candidates_json) as f:
        data = json.load(f)
    cands = data["candidates"]
    meta = data["meta"]
    gate = meta["min_confidence"]

    lines: list[str] = []
    lines.append("# Scoring constants audit — computed tables (generated by scripts/audit_scoring_constants.py)\n")
    lines.append(f"- config_hash: `{meta['config_hash']}`  | candidates: **{len(cands)}**  "
                 f"| timestamps: {meta['n_timestamps']} (stride {meta['stride']})  "
                 f"| symbols: {len(meta['symbols'])}  | gate: {gate}\n")
    lines.append(f"- {partition.upper()} window: {meta['window_train']}  | BTC 4h (regime) available from ms {meta['btc_4h_start_ms']}\n")

    def comp_table(subset: list[dict], title: str) -> None:
        lines.append(f"\n## Component value -> outcome: {title}\n")
        lines.append("| component | value | n | expected_r | win_rate (triggered) | trigger_rate |")
        lines.append("|---|---|---|---|---|---|")
        for comp in COMPONENTS:
            buckets: dict[Any, list[dict]] = {}
            for c in subset:
                buckets.setdefault(c["components"][comp], []).append(c)
            for val in sorted(buckets):
                st = set_stats(buckets[val])
                wr = st["win_rate_triggered"]
                lines.append(f"| {comp} | {val} | {st['n']} | {st['expected_r']} | "
                             f"{wr if wr is not None else '-'} | {st['trigger_rate']} |")

    comp_table(cands, "ALL candidates")
    post = [c for c in cands if c["timestamp"] >= BTC_4H_START_MS]
    comp_table(post, f"candidates with BTC 4h data available (ts >= {BTC_4H_START_MS}, n={len(post)})")

    # confidence vs outcome
    lines.append("\n## Confidence vs outcome\n")
    confs = [c["confidence"] for c in cands]
    rs = [c["outcome"]["net_realized_r"] for c in cands]
    lines.append(f"- pearson(confidence, net_realized_r) = **{pearson(confs, rs):.4f}** (n={len(cands)})\n")
    lines.append("| confidence | n | expected_r | win_rate (triggered) |")
    lines.append("|---|---|---|---|")
    buckets: dict[int, list[dict]] = {}
    for c in cands:
        buckets.setdefault(c["confidence"], []).append(c)
    for conf in sorted(buckets):
        st = set_stats(buckets[conf])
        lines.append(f"| {conf} | {st['n']} | {st['expected_r']} | {st['win_rate_triggered']} |")

    # gate sweep
    lines.append("\n## min_confidence gate sweep (baseline components)\n")
    lines.append("| threshold | selected n | expected_r | win_rate (triggered) |")
    lines.append("|---|---|---|---|")
    for th in [50, 55, 60, 65, 70, 75, 80]:
        sel = [c for c in cands if c["confidence"] >= th]
        st = set_stats(sel)
        lines.append(f"| {th} | {st['n']} | {st['expected_r']} | {st['win_rate_triggered']} |")

    # perturbation sensitivity
    baseline_sel = [c for c in cands if c["confidence"] >= gate]
    base_exp = set_stats(baseline_sel)["expected_r"]
    lines.append("\n## Perturbation sensitivity (gate = %d, baseline selected n = %d, baseline expected_r = %s)\n"
                 % (gate, len(baseline_sel), base_exp))
    lines.append("| perturbation | selected n | Δselected | admitted Δ: n / expected_r | "
                 "dropped Δ: n / expected_r | selected expected_r |")
    lines.append("|---|---|---|---|---|---|")
    for name, overrides in build_perturbations().items():
        sel, admitted, dropped = [], [], []
        for c in cands:
            comp = dict(c["components"])
            for k, fn in overrides.items():
                comp[k] = fn(c, comp[k])
            new_total = min(100, sum(comp.values()))
            was_sel = c["confidence"] >= gate
            is_sel = new_total >= gate
            if is_sel:
                sel.append(c)
            if is_sel and not was_sel:
                admitted.append(c)
            if was_sel and not is_sel:
                dropped.append(c)
        st = set_stats(sel)
        sa, sd = set_stats(admitted), set_stats(dropped)
        delta = st["n"] - len(baseline_sel)
        lines.append(
            f"| {name} | {st['n']} | {delta:+d} | {sa['n']} / {sa['expected_r']} | "
            f"{sd['n']} / {sd['expected_r']} | {st['expected_r']} |"
        )

    # structural facts / dead-branch proofs
    lines.append("\n## Structural facts in this evidence base\n")
    below_vol = sum(1 for c in cands if (c["volume_ratio"] or 0) < 1.5)
    rr_partial = sum(1 for c in cands if (c["rr_tp"] or 0) < 2.0)
    oi_counter: dict[str, int] = {}
    for c in cands:
        oi_counter[c["oi_status"]] = oi_counter.get(c["oi_status"], 0) + 1
    pen_counter: dict[str, int] = {}
    for c in cands:
        pen_counter[str(c["funding_penalty"])] = pen_counter.get(str(c["funding_penalty"]), 0) + 1
    max_total = max(sum(c["components"].values()) for c in cands)
    lines.append(f"- volume 0-credit branch (ratio < min_ratio 1.5): **{below_vol}** candidates "
                 "(0 => branch unreachable upstream of scoring)\n")
    lines.append(f"- rr partial branch (rr_tp < 2.0): **{rr_partial}** candidates\n")
    lines.append(f"- OI status distribution: {oi_counter}\n")
    lines.append(f"- funding_penalty distribution: {pen_counter}\n")
    lines.append(f"- max attainable score observed: {max_total} (code caps at 100)\n")

    with open(tables_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {tables_md}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scoring constants audit")
    parser.add_argument("phase", choices=["capture", "analyze"])
    parser.add_argument("--stride", type=int, default=1, help="keep every Nth TRAIN timestamp")
    parser.add_argument("--limit-ts", type=int, default=None, help="cap timestamp count (debug)")
    parser.add_argument("--partition", choices=["train", "val", "test"], default="train",
                        help="chronological 60/20/20 slice (capture + analyze)")
    args = parser.parse_args()
    if args.phase == "capture":
        capture(args.stride, args.limit_ts, args.partition)
    else:
        analyze(args.partition)


if __name__ == "__main__":
    main()
