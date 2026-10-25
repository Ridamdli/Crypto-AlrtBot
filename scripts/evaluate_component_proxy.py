#!/usr/bin/env python
"""Phase 04A - offline evaluation of scoring-component proxies.

Replays the 204 archived entry signals, reconstructs each component's current
value, computes a measured proxy for the same component at signal time, and
reports whether the proxy discriminates forward R better than the current
constant / fixed weight.

Nothing here changes production behaviour: the scoring engine is imported
read-only for its constants, and every result is written under
``data_store/phase04a/`` (gitignored).

Usage
-----
    python scripts/evaluate_component_proxy.py --fetch   # one-off, network
    python scripts/evaluate_component_proxy.py           # offline, replayable

Evidence base for every number printed: 11 days (2026-10-08 -> 2026-10-18),
204 entry signals, one regime (bearish 157 / neutral 47), UNIUSDT-dominated
exit population, 27 signals still ACTIVE.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data_store" / "historical" / "historical_data.db"
OUTCOMES = ROOT / "prod_evidence" / "pariston_outcomes.json"
CACHE = ROOT / "data_store" / "phase04a"
OUT = CACHE / "results.json"
ROWS = CACHE / "signals.jsonl"

GATE = 60
INTERVALS = ("15m", "1h", "4h")

# Weights, copied from src/config.py ScoringConfig / FundingConfig.
W_REGIME = 20
W_STRUCTURE = 15
W_MOMENTUM = 15
W_VOLUME = 15
W_OI = 15
W_FUNDING = 5
W_LIQUIDITY = 5
W_RR = 10
FUNDING_EXTREME = 0.01      # 1% per 8h, both directions
FUNDING_PENALTY = 15
VOL_MIN_RATIO = 1.5
VOL_STRONG_RATIO = 2.0
OI_MIN_CHANGE_PCT = 2.0


# ---------------------------------------------------------------- statistics
def pearson(xs, ys):
    """Pearson r, or None when a series has no variance (undefined)."""
    n = min(len(xs), len(ys))
    if n < 3 or len(set(xs[:n])) < 2 or len(set(ys[:n])) < 2:
        return None
    mx, my = mean(xs[:n]), mean(ys[:n])
    num = sum((x - mx) * (y - my) for x, y in zip(xs[:n], ys[:n]))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs[:n]))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys[:n]))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def average_ranks(values):
    """1-based ranks with ties averaged (the standard Spearman prerequisite)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Spearman rho = Pearson on average ranks; None when undefined."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(average_ranks(xs), average_ranks(ys))


def percentile_rank(values):
    """Map each value to [0, 1] by average rank. Ties share a percentile."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    ranks = average_ranks(values)
    return [(r - 1.0) / (n - 1.0) for r in ranks]


def bootstrap_ci(xs, ys, stat, n=1000, seed=7, alpha=0.05):
    """Percentile bootstrap CI for a paired statistic. Deterministic by seed."""
    pairs = list(zip(xs, ys))
    if len(pairs) < 5:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        v = stat([p[0] for p in sample], [p[1] for p in sample])
        if v is not None:
            vals.append(v)
    if len(vals) < n * 0.5:
        return None
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return [round(lo, 4), round(hi, 4)]


def rank_change_pct(old_scores, new_scores):
    """Share of signals whose rank position moves by at least one place."""
    if not old_scores:
        return 0.0
    old = average_ranks([-s for s in old_scores])
    new = average_ranks([-s for s in new_scores])
    moved = sum(1 for a, b in zip(old, new) if abs(a - b) >= 1.0)
    return round(100.0 * moved / len(old), 2)


def gate_flip_pct(old_scores, new_scores, gate=GATE):
    if not old_scores:
        return 0.0
    flips = sum(1 for a, b in zip(old_scores, new_scores) if (a >= gate) != (b >= gate))
    return round(100.0 * flips / len(old_scores), 2)


def quintile_means(values, outcomes, k=5):
    """R-weighted view: mean forward R per proxy quintile (Q1 lowest)."""
    n = len(values)
    if n < k * 2:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    buckets = [[] for _ in range(k)]
    for pos, idx in enumerate(order):
        buckets[min(k - 1, pos * k // n)].append(outcomes[idx])
    return [{"bucket": f"Q{i + 1}", "n": len(b), "mean_r": round(mean(b), 4)}
            for i, b in enumerate(buckets)]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def tanh_map(value, scale, lo, hi):
    """Squash a signed value into [lo, hi] around the midpoint."""
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    return mid + half * math.tanh(value / scale)


# ---------------------------------------------------------------- market data
def _ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def load_market(db=DB):
    """{interval: {symbol: {'ts': [...], 'close': [...], 'volume': [...]}}}"""
    market = {iv: {} for iv in INTERVALS}
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT symbol, interval, timestamp, close, volume FROM klines "
            "ORDER BY symbol, interval, timestamp"
        )
        for symbol, interval, ts, close, volume in rows:
            if interval not in market:
                continue
            bucket = market[interval].setdefault(
                symbol, {"ts": [], "close": [], "volume": []})
            bucket["ts"].append(ts)
            bucket["close"].append(float(close))
            bucket["volume"].append(float(volume))
    finally:
        conn.close()
    return market


def window(market, interval, symbol, end_ms, count):
    """Last ``count`` bars with open time <= end_ms."""
    bucket = market.get(interval, {}).get(symbol)
    if not bucket:
        return None
    i = bisect.bisect_right(bucket["ts"], end_ms)
    if i < count:
        return None
    return {
        "ts": bucket["ts"][i - count:i],
        "close": bucket["close"][i - count:i],
        "volume": bucket["volume"][i - count:i],
    }


def load_series(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = sorted((int(k), float(v)) for k, v in data.items())
    return {"ts": [t for t, _ in items], "value": [v for _, v in items]}


def last_at(series, end_ms):
    if not series or not series["ts"]:
        return None
    i = bisect.bisect_right(series["ts"], end_ms)
    return series["value"][i - 1] if i else None


def oi_window(series, end_ms, count=10):
    if not series or not series["ts"]:
        return []
    i = bisect.bisect_right(series["ts"], end_ms)
    return series["value"][max(0, i - count):i]


# ---------------------------------------------------------------- components
def price_trend(closes, lookback=10):
    if len(closes) < lookback + 1:
        return "neutral"
    if closes[-1] > closes[-(lookback + 1)]:
        return "up"
    if closes[-1] < closes[-(lookback + 1)]:
        return "down"
    return "neutral"


def volume_ratio(volumes, window_len=20):
    if len(volumes) < window_len + 1:
        return 0.0
    base = mean(volumes[-(window_len + 1):-1])
    return volumes[-1] / base if base else 0.0


def score_parts(regime, side, trend, ratio, rr, oi_points, funding_points):
    """Mirror of ScoringEngine.score_candidate for the parts we replay."""
    s = 0
    if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
        s += W_REGIME
    elif regime == "neutral":
        s += W_REGIME // 2
    s += int(W_STRUCTURE * 0.7)
    if (side == "LONG" and trend == "up") or (side == "SHORT" and trend == "down"):
        s += W_MOMENTUM
    else:
        s += W_MOMENTUM // 2
    if ratio >= VOL_STRONG_RATIO:
        s += W_VOLUME
    elif ratio >= VOL_MIN_RATIO:
        s += int(W_VOLUME * 0.7)
    s += oi_points
    s += funding_points
    s += W_LIQUIDITY
    if rr >= 2.0:
        s += W_RR
    elif rr >= 1.5:
        s += int(W_RR * 0.7)
    return min(100, s)


def funding_points(adverse_rate):
    penalty = FUNDING_PENALTY if adverse_rate >= FUNDING_EXTREME else 0
    return max(0, W_FUNDING - penalty)


def oi_points_from(change_pct, trend, side):
    status, signal = "UNKNOWN", "neutral"
    if change_pct >= OI_MIN_CHANGE_PCT:
        if trend == "up":
            signal, status = "bullish", "CONFIRMED"
        elif trend == "down":
            signal, status = "bearish", "CONFIRMED"
    elif change_pct <= -OI_MIN_CHANGE_PCT:
        status = "REJECTED"
    if status == "CONFIRMED":
        if (side == "LONG" and signal == "bullish") or (side == "SHORT" and signal == "bearish"):
            return W_OI
        return W_OI // 2
    if status == "REJECTED":
        return -(W_OI // 2)
    return 0


def regime_points(regime, side):
    if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
        return W_REGIME
    if regime == "neutral":
        return W_REGIME // 2
    return 0


def structure_proxy(closes_1h, side, scale=0.002):
    """Signed least-squares slope of log 1h closes over 24h, mapped to 0..15.

    ``scale`` = 0.2% per hour saturates the 0..15 range; that is ~5%/day.
    """
    if not closes_1h or len(closes_1h) < 25:
        return None, None
    ys = [math.log(c) for c in closes_1h[-24:]]
    n = len(ys)
    xs = list(range(n))
    mx = mean(xs)
    my = mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    sign = 1.0 if side == "LONG" else -1.0
    aligned = slope * sign
    return aligned, tanh_map(aligned, scale, 0.0, float(W_STRUCTURE))


def oi_proxy(change_pct, trend, side):
    """Continuous OI-change proxy, signed for the trade side, mapped to 0..15."""
    if trend == "neutral" or change_pct is None:
        return 0.0, tanh_map(0.0, 2.0, 0.0, float(W_OI))
    aligned = (side == "LONG" and trend == "up") or (side == "SHORT" and trend == "down")
    signed = change_pct if aligned else -change_pct
    return signed, tanh_map(signed, 2.0, 0.0, float(W_OI))


# ---------------------------------------------------------------- fetch
def fetch_caches(symbols_oi, symbols_funding, start_ms, end_ms):
    """Populate the two public-history caches. Existing clients, public endpoints."""
    from src.data.binance_client import BinanceFuturesClient
    import time

    CACHE.mkdir(parents=True, exist_ok=True)
    client = BinanceFuturesClient()

    for symbol in symbols_funding:
        path = CACHE / f"funding_{symbol}.json"
        if path.exists():
            continue
        rows = client.get_funding_rate(symbol, limit=1000, end_time=end_ms)
        series = {int(r["fundingTime"]): float(r["fundingRate"]) for r in rows}
        path.write_text(json.dumps(series, sort_keys=True), encoding="utf-8")
        time.sleep(0.2)

    for symbol in symbols_oi:
        path = CACHE / f"oi_{symbol}.json"
        if path.exists():
            continue
        series = {}
        cursor = end_ms
        while True:
            rows = client.get_open_interest_hist(
                symbol, period="15m", limit=500, end_time=cursor)
            if not rows:
                break
            for r in rows:
                series[int(r["timestamp"])] = float(r["sumOpenInterestValue"])
            earliest = min(int(r["timestamp"]) for r in rows)
            if earliest <= start_ms or len(rows) < 2:
                break
            cursor = earliest - 1
            time.sleep(0.2)
        path.write_text(json.dumps(series, sort_keys=True), encoding="utf-8")
        time.sleep(0.2)


def fetch_klines_cache(symbols, interval, start_ms, end_ms):
    """Fill 1h history the local DB is missing (DOT/LINK have 15m only)."""
    from src.data.binance_client import BinanceFuturesClient
    import time

    CACHE.mkdir(parents=True, exist_ok=True)
    client = BinanceFuturesClient()
    for symbol in symbols:
        path = CACHE / f"klines_{interval}_{symbol}.json"
        if path.exists():
            continue
        bars = client.get_klines(symbol, interval, limit=500, end_time=end_ms)
        data = {int(b[0]): [float(b[4]), float(b[5])] for b in bars}
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        time.sleep(0.2)


# ---------------------------------------------------------------- evaluation
def component_rows(signals, market, funding, oi):
    rows = []
    for sig in signals:
        end = _ms(sig["signal_timestamp"])
        side = sig["side"]
        regime = sig["btc_regime"]
        bars15 = window(market, "15m", sig["symbol"], end, 100)
        if not bars15:
            continue
        trend = price_trend(bars15["close"])
        ratio = volume_ratio(bars15["volume"])
        tp = sig.get("tp", sig.get("tp1"))
        rr = round(abs(tp - sig["entry"]) / abs(sig["entry"] - sig["stop_loss"]), 2)

        # Current funding / OI values, reconstructed at signal time.
        rate = last_at(funding.get(sig["symbol"]), end)
        adverse = None if rate is None else (rate if side == "LONG" else -rate)
        cur_funding = funding_points(adverse) if adverse is not None else None
        oi_hist = oi_window(oi.get(sig["symbol"]), end)
        oi_change = None
        if len(oi_hist) >= 2 and oi_hist[0]:
            oi_change = (oi_hist[-1] - oi_hist[0]) / oi_hist[0] * 100.0
        cur_oi = oi_points_from(oi_change, trend, side) if oi_change is not None else None

        cur_regime = regime_points(regime, side)
        cur_structure = int(W_STRUCTURE * 0.7)
        cur_liquidity = W_LIQUIDITY

        # Score sanity: full replay vs recorded. Reported, not used for deltas.
        replay = None
        if None not in (cur_funding, cur_oi):
            replay = score_parts(regime, side, trend, ratio, rr, cur_oi, cur_funding)

        rows.append({
            "signal_id": sig["signal_id"],
            "symbol": sig["symbol"],
            "side": side,
            "strategy_id": sig["strategy_id"],
            "btc_regime": regime,
            "signal_timestamp": sig["signal_timestamp"],
            "confidence": sig["confidence"],
            "net_r": sig["net_realized_r"],
            "settled": sig.get("status") != "ACTIVE",
            "status": sig.get("status"),
            "price_trend": trend,
            "volume_ratio": round(ratio, 4),
            "rr": rr,
            "oi_change_pct": None if oi_change is None else round(oi_change, 4),
            "funding_rate": rate,
            "funding_adverse": adverse,
            "current": {
                "structure": cur_structure,
                "liquidity": cur_liquidity,
                "funding": cur_funding,
                "oi": cur_oi,
                "regime": cur_regime,
            },
            "replay_score": replay,
        })
    return rows


def merged_market(market):
    """Overlay any cached klines for symbols the historical DB lacks."""
    for path in sorted(CACHE.glob("klines_*_*.json")):
        _, interval, symbol = path.stem.split("_", 2)
        if interval not in market:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = sorted((int(k), v) for k, v in raw.items())
        bucket = market[interval].setdefault(symbol, {"ts": [], "close": [], "volume": []})
        existing = dict(zip(bucket["ts"], zip(bucket["close"], bucket["volume"])))
        for ts, (close, volume) in items:
            existing.setdefault(ts, (float(close), float(volume)))
        ts_sorted = sorted(existing)
        bucket["ts"] = ts_sorted
        bucket["close"] = [existing[t][0] for t in ts_sorted]
        bucket["volume"] = [existing[t][1] for t in ts_sorted]
    return market


def _stat_block(rows, proxy_key, proxy_points_key, current_key):
    """Correlations, decisiveness, gate flips and R-weighted quintiles."""
    usable = [r for r in rows if r[proxy_key] is not None and r["net_r"] is not None]
    if len(usable) < 10:
        return {"n": len(usable), "verdict": "DATA-INSUFFICIENT"}
    comp = [r[proxy_key] for r in usable]
    out = [r["net_r"] for r in usable]
    cur_pts = [r["current"][current_key] for r in usable]
    proxy_pts = [r[proxy_points_key] for r in usable]
    base = [r["confidence"] for r in usable]
    new = [clamp(r["confidence"] - r["current"][current_key] + r[proxy_points_key], 0, 100)
           for r in usable]
    removed = [clamp(r["confidence"] - r["current"][current_key], 0, 100) for r in usable]
    settled = [i for i, r in enumerate(usable) if r["settled"]]

    cur_corr = pearson(cur_pts, out) if len(set(cur_pts)) > 1 else None
    proxy_corr = pearson(comp, out)
    block = {
        "n": len(usable),
        "n_settled": len(settled),
        "proxy_pearson": round(proxy_corr, 4) if proxy_corr is not None else None,
        "proxy_pearson_ci": bootstrap_ci(comp, out, pearson),
        "proxy_spearman": round(spearman(comp, out), 4) if spearman(comp, out) is not None else None,
        "proxy_spearman_ci": bootstrap_ci(comp, out, spearman),
        "current_pearson": round(cur_corr, 4) if cur_corr is not None else None,
        "current_variance": len(set(cur_pts)) > 1,
        "redundancy": (round(pearson(cur_pts, proxy_pts), 4)
                       if len(set(cur_pts)) > 1 else None),
        "baseline_score_corr": (round(pearson(base, out), 4)
                                if pearson(base, out) is not None else None),
        "proxy_score_corr": (round(pearson(new, out), 4)
                             if pearson(new, out) is not None else None),
        "rank_change_pct": rank_change_pct(base, new),
        "gate_flip_pct": gate_flip_pct(base, new),
        "mean_delta": round(mean([b - a for a, b in zip(base, new)]), 2),
        "mean_abs_delta": round(mean([abs(a - b) for a, b in zip(base, new)]), 2),
        "delta_ge_2_pct": round(100.0 * sum(
            1 for a, b in zip(base, new) if abs(a - b) >= 2) / len(base), 2),
        "quintiles": quintile_means(comp, out),
        "removal": {
            "score_corr_without": (round(pearson(removed, out), 4)
                                   if pearson(removed, out) is not None else None),
            "rank_change_pct": rank_change_pct(base, removed),
            "gate_flip_pct": gate_flip_pct(base, removed),
        },
    }
    if len(settled) >= 10:
        s_base = [base[i] for i in settled]
        s_new = [new[i] for i in settled]
        s_out = [out[i] for i in settled]
        c = pearson(s_new, s_out)
        b = pearson(s_base, s_out)
        p = pearson([comp[i] for i in settled], s_out)
        block["settled"] = {
            "baseline_score_corr": round(b, 4) if b is not None else None,
            "proxy_score_corr": round(c, 4) if c is not None else None,
            "proxy_pearson": round(p, 4) if p is not None else None,
            "rank_change_pct": rank_change_pct(s_base, s_new),
        }
    return block


def verdict(block):
    if block.get("verdict") == "DATA-INSUFFICIENT":
        return "DATA-INSUFFICIENT"
    corr = block["proxy_pearson"]
    if corr is None:
        return "DATA-INSUFFICIENT"
    if block["redundancy"] is not None and abs(block["redundancy"]) > 0.9:
        return "KEEP-CURRENT"  # no new information beyond the existing component
    if corr >= 0.10 and block["rank_change_pct"] >= 10.0:
        return "ADOPT"
    # Discard only when the component varies, the proxy shows nothing, and
    # removing the component does not lower the score's own correlation.
    removal_corr = block["removal"]["score_corr_without"]
    if (block["current_variance"] and corr < 0.05
            and removal_corr is not None
            and block["baseline_score_corr"] is not None
            and removal_corr >= block["baseline_score_corr"]):
        return "DISCARD-COMPONENT"
    return "KEEP-CURRENT"


def evaluate(rows, market):
    """Evaluate the five components independently."""
    results = {}

    # 1. Structure: constant int(15*0.7) -> 1h log-slope alignment.
    prox = [structure_proxy(
        window(market, "1h", r["symbol"], _ms(r["signal_timestamp"]), 100)["close"]
        if window(market, "1h", r["symbol"], _ms(r["signal_timestamp"]), 100) else None,
        r["side"]) for r in rows]
    for r, (aligned, pts) in zip(rows, prox):
        r["structure_aligned"] = aligned
        r["structure_points"] = pts
    results["structure"] = _stat_block(rows, "structure_aligned", "structure_points", "structure")

    # 2. Liquidity: constant 5 -> dollar-volume percentile in the universe.
    universe_vols = {}
    for r in rows:
        ts = _ms(r["signal_timestamp"])
        key = ts
        if key not in universe_vols:
            vols = []
            for symbol in market["15m"]:
                b = window(market, "15m", symbol, ts, 1)
                if b and b["volume"][0]:
                    vols.append(b["close"][0] * b["volume"][0])
            universe_vols[key] = sorted(vols)
        b = window(market, "15m", r["symbol"], ts, 1)
        dv = b["close"][0] * b["volume"][0] if b else 0.0
        pool = universe_vols[key]
        below = sum(1 for v in pool if v < dv)
        equal = sum(1 for v in pool if v == dv)
        pct = (below + 0.5 * equal) / len(pool) if pool else 0.5
        r["liquidity_pct"] = pct
        r["liquidity_points"] = W_LIQUIDITY * pct
    results["liquidity"] = _stat_block(rows, "liquidity_pct", "liquidity_points", "liquidity")

    # 3. Funding: binary +5 / 0 -> continuous adverse-rate percentile.
    # Universe percentile per timestamp over every symbol with a cached rate.
    rates = {}
    for symbol in market["15m"]:
        path = CACHE / f"funding_{symbol}.json"
        if path.exists():
            rates[symbol] = load_series(path)
    for r in rows:
        ts = _ms(r["signal_timestamp"])
        adv = r["funding_adverse"]
        if adv is None:
            r["funding_pct"] = None
            r["funding_points_proxy"] = None
            continue
        pool = []
        for symbol, series in rates.items():
            v = last_at(series, ts)
            if v is None:
                continue
            pool.append(v if r["side"] == "LONG" else -v)
        below = sum(1 for v in pool if v < adv)
        equal = sum(1 for v in pool if v == adv)
        pct = (below + 0.5 * equal) / len(pool) if pool else 0.5
        r["funding_pct"] = pct
        r["funding_points_proxy"] = W_FUNDING - 2.0 * W_FUNDING * pct
    results["funding"] = _stat_block(rows, "funding_pct", "funding_points_proxy", "funding")

    # 4. Open interest: categorical 15/7/0/-7 -> continuous signed OI change.
    for r in rows:
        signed, pts = oi_proxy(r["oi_change_pct"], r["price_trend"], r["side"])
        r["oi_signed"] = signed if r["oi_change_pct"] is not None else None
        r["oi_points_proxy"] = pts if r["oi_change_pct"] is not None else None
    results["oi"] = _stat_block(rows, "oi_signed", "oi_points_proxy", "oi")
    # Option B: does dropping the OI term improve the score's own correlation?
    drop = [r for r in rows if r["current"]["oi"] is not None]
    results["oi"]["drop_component"] = {
        "n": len(drop),
        "score_corr_with_oi": round(pearson(
            [r["confidence"] for r in drop], [r["net_r"] for r in drop]), 4),
        "score_corr_without_oi": round(pearson(
            [r["confidence"] - r["current"]["oi"] for r in drop],
            [r["net_r"] for r in drop]), 4),
        "gate_flip_pct": gate_flip_pct(
            [r["confidence"] for r in drop],
            [r["confidence"] - r["current"]["oi"] for r in drop]),
    }

    # 5. Regime: fixed 20/10/0 -> variants that de-emphasise neutral.
    for r in rows:
        r["regime_points_proxy"] = r["current"]["regime"]
        r["regime_points_neutral5"] = (W_REGIME if r["current"]["regime"] == W_REGIME
                                       else (5 if r["btc_regime"] == "neutral" else 0))
    results["regime"] = _stat_block(rows, "regime_points_proxy", "regime_points_proxy", "regime")
    results["regime"]["variant_neutral5"] = _stat_block(
        rows, "regime_points_neutral5", "regime_points_neutral5", "regime")
    results["regime"]["note"] = (
        "No bullish signals in sample: only aligned-vs-neutral is testable; "
        "regime-conditional weights are not identifiable from one regime.")

    for name, block in results.items():
        block["verdict"] = verdict(block)
        if name == "regime":
            block["variant_neutral5"]["verdict"] = verdict(block["variant_neutral5"])
    return results


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="populate the OI/funding caches")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    raw = json.loads(OUTCOMES.read_text(encoding="utf-8"))
    signals = sorted(raw.values(), key=lambda r: r["signal_timestamp"])
    symbols = sorted({s["symbol"] for s in signals})
    start = min(_ms(s["signal_timestamp"]) for s in signals) - 4 * 3600 * 1000
    end = max(_ms(s["signal_timestamp"]) for s in signals) + 3600 * 1000

    if args.fetch:
        all_symbols = sorted({s for iv in INTERVALS for s in load_market().get(iv, {})})
        fetch_caches(symbols, all_symbols, start, end)
        base = load_market()
        fetch_klines_cache(
            [s for s in symbols if s not in base.get("1h", {})], "1h", start, end)

    market = merged_market(load_market())
    funding = {}
    for symbol in market["15m"]:
        path = CACHE / f"funding_{symbol}.json"
        if path.exists():
            funding[symbol] = load_series(path)
    oi = {}
    for symbol in symbols:
        path = CACHE / f"oi_{symbol}.json"
        if path.exists():
            oi[symbol] = load_series(path)

    rows = component_rows(signals, market, funding, oi)
    results = evaluate(rows, market)

    CACHE.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    with ROWS.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print(f"signals={len(rows)} settled={sum(1 for r in rows if r['settled'])}")
    for name, block in results.items():
        print(f"\n[{name}] n={block['n']} verdict={block['verdict']}")
        for k in ("proxy_pearson", "proxy_pearson_ci", "proxy_spearman",
                  "current_pearson", "baseline_score_corr", "proxy_score_corr",
                  "rank_change_pct", "gate_flip_pct", "mean_delta"):
            print(f"  {k}: {block.get(k)}")
        print("  removal:", block.get("removal"))
        print("  settled:", block.get("settled"))
        if "variant_neutral5" in block:
            v = block["variant_neutral5"]
            print("  variant_neutral5:", {k: v.get(k) for k in (
                "proxy_pearson", "rank_change_pct", "gate_flip_pct",
                "proxy_score_corr", "verdict")})
        if block.get("quintiles"):
            print("  quintiles:", block["quintiles"])


if __name__ == "__main__":
    main()
