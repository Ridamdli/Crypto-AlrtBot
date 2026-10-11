#!/usr/bin/env python
"""Bounded offline evaluation: baseline vs Exit-Orchestrator candidate (v1).

Scope (approved, bounded):
- Symbols: BTCUSDT, ETHUSDT (both AVAILABLE, full BTC-regime overlap).
- Setup window: 2026-09-14T00:00:00Z .. 2026-10-04T06:00:00Z, 6h steps
  (last ~3 weeks of DB history; generation ends 30h+ before the newest bar
  so every setup has a fair outcome window).
- Setups: production SignalPipeline on CachedFuturesClient (deterministic,
  identical to live generation). Cap 200 signals.
- Outcomes: DB 15m klines (160 future bars), 60 pre-signal context bars.
- Arms: untouched baseline evaluator vs candidate (full worker stack).
- Ablation: each of the 5 workers, one at a time.
- No tuning anywhere: all orchestrator thresholds are fixed a-priori
  rationale defaults. No datasets/reports modified; writes only
  reports/EXIT_EVAL_V1.md + reports/exit_eval_v1.json (new files).

Pre-registered verdict rule: adopt candidate management only if test-slice
expectancy delta is positive with bootstrap CI excluding zero; otherwise
retain baseline. Small-n results are reported as inconclusive, never as
profitability claims.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Silence per-scan chatter like the research runner does.
for _mod in ["src.pipeline", "src.engines.volume_engine", "src.engines.oi_engine",
             "src.engines.funding_engine", "src.engines.btc_regime",
             "src.engines.scoring_engine", "src.engines.tp_engine",
             "src.data.timeframe_manager", "src.data.ohlcv_fetcher",
             "src.data.futures_data", "src.exits.monitor"]:
    logging.getLogger(_mod).setLevel(logging.ERROR)

from src.config import CONFIG  # noqa: E402
from src.data.cached_client import CachedFuturesClient  # noqa: E402
from src.exits.config import ExitConfig  # noqa: E402
from src.exits.harness import compare_arms, make_decide_fn  # noqa: E402
from src.pipeline import SignalPipeline  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
START_ISO = "2026-07-07T04:00:00Z"
END_SETUP_ISO = "2026-10-04T06:00:00Z"
STEP_BARS_15M = 24          # 6h grid
SETUP_CAP = 1000
FUTURE_LIMIT = 160          # entry window 6 + holding 100 + margin
CONTEXT_BARS = 60
FUTURE_TIMEOUT_NOTE = "outcomes use only bars present in DB (fair window guaranteed by END_SETUP)"
WORKER_IDS = ["structure", "target", "stop", "counter", "riskreward"]


def _iso_to_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _klines_after(client: CachedFuturesClient, symbol: str, start_ts: int, limit: int):
    with client.store.get_connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='15m' AND timestamp > ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (symbol, start_ts, limit)).fetchall()
    return [{"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in rows]


def main() -> int:
    t0 = datetime.now(timezone.utc)
    print(f"[eval] start {t0.isoformat()} symbols={SYMBOLS}", flush=True)
    client = CachedFuturesClient()
    pipeline = SignalPipeline(config=CONFIG, client=client)

    start_ms = _iso_to_ms(START_ISO)
    end_ms = _iso_to_ms(END_SETUP_ISO)
    grid = list(range(start_ms, end_ms + 1, STEP_BARS_15M * 15 * 60 * 1000))
    print(f"[eval] {len(grid)} scan timestamps", flush=True)

    signals = []
    for i, ts in enumerate(grid):
        try:
            found = pipeline.run_pipeline(as_of_time=ts, symbols=SYMBOLS)
        except Exception as e:
            print(f"[eval] scan {i} failed: {e}", flush=True)
            continue
        for s in found or []:
            signals.append(s)
            if len(signals) >= SETUP_CAP:
                break
        if (i + 1) % 10 == 0:
            print(f"[eval] scans {i + 1}/{len(grid)} signals={len(signals)}", flush=True)
        if len(signals) >= SETUP_CAP:
            break
    print(f"[eval] collected {len(signals)} setups", flush=True)
    if not signals:
        print("[eval] no setups generated; nothing to compare.")
        return 2

    klines_map = {}
    for sym in SYMBOLS:
        klines_map[sym] = _klines_after(client, sym, start_ms - 7 * 24 * 3600 * 1000,
                                        100000)
        print(f"[eval] {sym}: {len(klines_map[sym])} klines loaded", flush=True)

    cfg = ExitConfig()

    def factory(disabled=frozenset()):
        return make_decide_fn(cfg, disabled=disabled)

    out = compare_arms(signals, klines_map, factory, cfg=cfg,
                       max_signals=SETUP_CAP, ablate_workers=WORKER_IDS)
    out["scope"] = {
        "symbols": SYMBOLS, "setup_window": [START_ISO, END_SETUP_ISO],
        "step_bars_15m": STEP_BARS_15M, "setup_cap": SETUP_CAP,
        "future_limit": FUTURE_LIMIT, "context_bars": CONTEXT_BARS,
        "note": FUTURE_TIMEOUT_NOTE,
        "no_tuning": True,
        "verdict_rule": "adopt candidate only if expectancy delta > 0 with 95% CI excluding zero",
    }
    out["ran_at"] = datetime.now(timezone.utc).isoformat()

    os.makedirs("reports", exist_ok=True)
    with open("reports/exit_eval_v2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    _write_markdown(out)
    b, c = out["baseline"], out["candidate"]
    print(f"[eval] baseline n={b['triggered']} exp={b['expectancy_net_r']} "
          f"candidate n={c['triggered']} exp={c['expectancy_net_r']} "
          f"delta={out['delta_expectancy']}", flush=True)
    print(f"[eval] done in {(datetime.now(timezone.utc) - t0).total_seconds():.0f}s", flush=True)
    return 0


def _write_markdown(out) -> None:
    b, c = out["baseline"], out["candidate"]
    lines = ["# Exit Orchestrator Evaluation v2 (bounded, offline; 8 symbols, full window)",
             "",
             f"Ran at: {out['ran_at']}",
             f"Scope: symbols={out['scope']['symbols']}, "
             f"window={out['scope']['setup_window']}, step={out['scope']['step_bars_15m']} bars, "
             f"cap={out['scope']['setup_cap']}",
             f"No tuning performed: {out['scope']['no_tuning']}",
             "",
             "## Arms (identical setups, klines, fees, slippage)",
             "",
             "| metric | baseline | candidate |",
             "|---|---|---|",
             f"| triggered | {b['triggered']} | {c['triggered']} |",
             f"| expectancy net R | {b['expectancy_net_r']} | {c['expectancy_net_r']} |",
             f"| total net R | {b['total_net_r']} | {c['total_net_r']} |",
             f"| profit factor | {b['profit_factor']} | {c['profit_factor']} |",
             f"| max DD (R) | {b['max_drawdown_r']} | {c['max_drawdown_r']} |",
             f"| win rate | {b['win_rate']} | {c['win_rate']} |",
             f"| avg win / loss | {b['avg_win_r']} / {b['avg_loss_r']} | "
             f"{c['avg_win_r']} / {c['avg_loss_r']} |",
             f"| stop freq | {b['stop_freq']} | {c['stop_freq']} |",
             f"| TP1/TP2/TP3 | {b['tp1_rate']}/{b['tp2_rate']}/{b['tp3_rate']} | "
             f"{c['tp1_rate']}/{c['tp2_rate']}/{c['tp3_rate']} |",
             f"| exposure mean bars | {b['exposure_mean_bars']} | {c['exposure_mean_bars']} |",
             f"| expectancy CI95 | {b['expectancy_ci95']} | {c['expectancy_ci95']} |",
             "",
             f"**Delta expectancy (candidate - baseline): {out['delta_expectancy']}**",
             "",
             "## Supervisor activity",
             "",
             f"Changed plans: {out['change_stats']['changed']}, "
             f"reduces: {out['change_stats']['reduces']}, "
             f"closes: {out['change_stats']['closes']}",
             "",
             "## Ablation (candidate expectancy without each worker)",
             "",
             "| ablated | expectancy net R | triggered |",
             "|---|---|---|"]
    for k, m in (out.get("ablation") or {}).items():
        lines.append(f"| {k} | {m['expectancy_net_r']} | {m['triggered']} |")
    lines += ["",
              "## Verdict rule",
              "",
              out["scope"]["verdict_rule"] + ".",
              "Small-n outcomes are inconclusive by policy, never profitability claims.",
              ""]
    with open("reports/EXIT_EVAL_V2.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
