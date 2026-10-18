#!/usr/bin/env python
"""Phase 03-B-diagnose: offline exit-decision diagnosis (local replay only).

Replays the candidate exit stack exactly like scripts/eval_exits_v1.py (same
scope constants, same determinism) while RECORDING every ExitSupervisor.decide
call, then classifies each decision against subsequent price action:

  CORRECT   adopted action — or rejected exit proposal — followed by a >= 0.25R
            move AGAINST the position within 24 bars (the decision/retention
            was right).
  PREMATURE adopted action followed by a >= 0.25R move IN FAVOR within 24 bars
            (the intervention cost edge).
  MISSED    no action taken although an exit proposal (CLOSE / REDUCE /
            INVALIDATE) existed, and price worsened >= 0.25R within 24 bars.
  NEUTRAL   everything else (including no-proposal NO_CHANGE).

Each decision is additionally tagged with the supervisor rule that produced it
(R1..R5 / R4_guard_rejected) and the worker whose proposal drove it. Full
records land in reports/exit_decisions_diagnose.json (raw evidence), summary
tables in reports/exit_diagnosis_tables.md.

Scope: offline DB klines only (CachedFuturesClient + explicit symbol list — no
network), no production file, no ledger, no Railway. Production's
exit_decisions.json lives on the Railway volume and is NOT read here; this is
the harness-replayed decision stream, tagged as such.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from src.config import CONFIG  # noqa: E402
from src.data.cached_client import CachedFuturesClient  # noqa: E402
from src.exits.config import ExitConfig  # noqa: E402
from src.exits.harness import (  # noqa: E402
    CandidateEvaluator, arm_metrics, make_decide_fn, _sig_ts_ms,
)
from src.exits.models import ExitAction  # noqa: E402
from src.exits.supervisor import ExitSupervisor  # noqa: E402
from src.pipeline import SignalPipeline  # noqa: E402

# Scope constants — byte-identical to scripts/eval_exits_v1.py (parity check
# against reports/exit_eval_v2.json proves the replay is the accepted one).
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
START_ISO = "2026-07-07T04:00:00Z"
END_SETUP_ISO = "2026-10-04T06:00:00Z"
STEP_BARS_15M = 24
SETUP_CAP = 1000
CONTEXT_BARS = 60

HORIZON_BARS = 24          # 6h of 15m bars after the decision bar
THRESHOLD_R = 0.25         # |move| below this is NEUTRAL
DECISIONS_JSON = "reports/exit_decisions_diagnose.json"
TABLES_MD = "reports/exit_diagnosis_tables.md"
V2_JSON = "reports/exit_eval_v2.json"

NO_ACTION = frozenset({
    ExitAction.NO_CHANGE.value,
    ExitAction.FLAG_CONFLICT.value,
    ExitAction.INSUFFICIENT_DATA.value,
})
EXIT_PROPOSALS = frozenset({
    ExitAction.CLOSE_PAPER_POSITION.value,
    ExitAction.REDUCE_PAPER_POSITION.value,
    ExitAction.INVALIDATE_PENDING_SETUP.value,
})
# Rule 4 maps exactly these (worker, action) pairs; anything else in Rule 4
# would be unreachable — the dead-adoption matrix checks this.
RULE4_PAIRS = {
    ExitAction.TIGHTEN_STOP.value: "stop",
    ExitAction.ADJUST_TAKE_PROFITS.value: "target",
    ExitAction.REDUCE_PAPER_POSITION.value: "riskreward",
}


# ─────────────────────────────────────────────────────────────────────────────
# pure logic (unit-tested in tests/test_diagnose_exits.py)
# ─────────────────────────────────────────────────────────────────────────────
def signed_move_r(side: str, ref_close: float | None,
                  future_closes: list[float], risk: float) -> float | None:
    """Signed R move over the horizon, positive = favorable to the position."""
    if ref_close is None or risk <= 0 or not future_closes:
        return None
    end = future_closes[min(HORIZON_BARS, len(future_closes)) - 1]
    raw = (end - ref_close) / risk
    return raw if side == "LONG" else -raw


def classify(action: str, has_exit_proposal: bool, move_r: float | None) -> str:
    if move_r is None:
        return "UNSCORED"
    if action not in NO_ACTION:
        if move_r <= -THRESHOLD_R:
            return "CORRECT"
        if move_r >= THRESHOLD_R:
            return "PREMATURE"
        return "NEUTRAL"
    if has_exit_proposal:
        if move_r <= -THRESHOLD_R:
            return "MISSED"
        if move_r >= THRESHOLD_R:
            return "CORRECT"
    return "NEUTRAL"


def rule_of(action: str, rationale: str) -> str:
    """Which supervisor rule produced this decision (exhaustive; UNKNOWN caught
    as a self-check by the caller)."""
    if action == ExitAction.INSUFFICIENT_DATA.value or "market data unusable" in rationale:
        return "R1_data"
    if action == ExitAction.INVALIDATE_PENDING_SETUP.value:
        return "R2_safety"
    if action == ExitAction.CLOSE_PAPER_POSITION.value:
        if "market printed" in rationale:
            return "R2_safety"
        if "uncontradicted reversal" in rationale:
            return "R3_reversal"
        return "UNKNOWN"
    if action == ExitAction.FLAG_CONFLICT.value:
        return "R3_reversal"
    if action in RULE4_PAIRS:
        return "R4_adjust"
    if action == ExitAction.NO_CHANGE.value:
        if "RiskGuard rejected" in rationale:
            return "R4_guard_rejected"
        return "R5_default"
    return "UNKNOWN"


def adopted_worker(action: str, rationale: str, assessments: list[dict]) -> str | None:
    """Which worker's proposal drove an adopted action (None for no-action)."""
    # NO_CHANGE / INSUFFICIENT_DATA drive nothing; FLAG_CONFLICT is the
    # counter worker's adopted proposal, so attribution continues below.
    if action in (ExitAction.NO_CHANGE.value, ExitAction.INSUFFICIENT_DATA.value):
        return None
    m = re.search(r"proposed by (\w+)", rationale)
    if m:
        return m.group(1)
    if action == ExitAction.INVALIDATE_PENDING_SETUP.value or "market printed" in rationale:
        return "structure"
    if "uncontradicted reversal" in rationale or action == ExitAction.FLAG_CONFLICT.value:
        return "counter"
    return None


def classify_record(rec: dict, future_closes: list[float], risk: float) -> dict:
    """Attach move_r / class / rule / adopted_by to one recorded decision."""
    move = signed_move_r(rec["side"], rec["ref_close"], future_closes, risk)
    has_exit = any(a.get("proposed_action") in EXIT_PROPOSALS
                   for a in rec["assessments"])
    rec["move_r"] = None if move is None else round(move, 4)
    rec["has_exit_proposal"] = has_exit
    rec["class"] = classify(rec["action"], has_exit, move)
    rec["rule"] = rule_of(rec["action"], rec["rationale"])
    rec["adopted_by"] = adopted_worker(rec["action"], rec["rationale"],
                                       rec["assessments"])
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# recording + replay
# ─────────────────────────────────────────────────────────────────────────────
def _install_recorder(records: list[dict]):
    """Patch ExitSupervisor.decide for the duration of the run (class-level;
    restored by the returned uninstall fn). Records post-decision state only —
    the original decide() result is returned untouched."""
    orig = ExitSupervisor.decide

    def recording(self, view, assessments, ctx, now_ms, seen_keys=frozenset(),
                  last_data_ts_ms=0, last_change_ts_ms=0):
        dec = orig(self, view, assessments, ctx, now_ms, seen_keys=seen_keys,
                   last_data_ts_ms=last_data_ts_ms,
                   last_change_ts_ms=last_change_ts_ms)
        sig = ctx.signal
        ref_bar = ctx.completed[-1] if ctx.completed else None
        records.append({
            "signal_id": view.signal_id,
            "symbol": sig.get("symbol"),
            "side": str(sig.get("side", "")).upper(),
            "state": view.state.value,
            "signal_regime": sig.get("btc_regime"),
            "signal_ts": str(sig.get("timestamp")),
            "entry": float(sig["entry"]),
            "stop_loss": float(sig["stop_loss"]),
            "plan_version": view.plan.version,
            "ref_ts": int(float(ref_bar["timestamp"])) if ref_bar else None,
            "ref_close": float(ref_bar["close"]) if ref_bar else None,
            "decided_at_ms": dec.decided_at_ms,
            "data_ts_ms": dec.data_ts_ms,
            "action": dec.action,
            "rationale": dec.rationale,
            "decision_key": dec.decision_key,
            "risk_guard": dec.risk_guard,
            "rejected": dec.rejected,
            "has_plan_after": dec.plan_after is not None,
            "events_applied": dec.events_applied,
            "assessments": [
                {"worker_id": a.worker_id, "verdict": a.verdict,
                 "proposed_action": a.proposed_action,
                 "evidence_quality": a.evidence_quality}
                for a in assessments
            ],
        })
        return dec

    ExitSupervisor.decide = recording
    return lambda: setattr(ExitSupervisor, "decide", orig)


def _iso_to_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _klines_after(client, symbol: str, start_ts: int, limit: int):
    with client.store.get_connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='15m' AND timestamp > ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (symbol, start_ts, limit)).fetchall()
    return [{"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in rows]


def _generate_signals(pipeline: SignalPipeline) -> list[dict]:
    start_ms = _iso_to_ms(START_ISO)
    end_ms = _iso_to_ms(END_SETUP_ISO)
    grid = list(range(start_ms, end_ms + 1, STEP_BARS_15M * 15 * 60 * 1000))
    signals: list[dict] = []
    for i, ts in enumerate(grid):
        try:
            found = pipeline.run_pipeline(as_of_time=ts, symbols=SYMBOLS)
        except Exception as e:  # noqa: BLE001 — scan failures skip, as in eval v1
            print(f"  scan {i} failed: {e}", flush=True)
            continue
        for s in found or []:
            signals.append(s)
            if len(signals) >= SETUP_CAP:
                break
        if (i + 1) % 50 == 0:
            print(f"  scans {i + 1}/{len(grid)} signals={len(signals)}", flush=True)
        if len(signals) >= SETUP_CAP:
            break
    return signals


def main() -> int:
    # Per-scan chatter silenced here (not at import: this module is imported
    # by tests, which must not inherit log-level changes).
    for _mod in ["src.pipeline", "src.engines.volume_engine", "src.engines.oi_engine",
                 "src.engines.funding_engine", "src.engines.btc_regime",
                 "src.engines.scoring_engine", "src.engines.tp_engine",
                 "src.data.timeframe_manager", "src.data.ohlcv_fetcher",
                 "src.data.futures_data", "src.exits.monitor"]:
        logging.getLogger(_mod).setLevel(logging.ERROR)
    t0 = datetime.now(timezone.utc)
    print(f"[diagnose] start {t0.isoformat()}", flush=True)
    client = CachedFuturesClient()
    pipeline = SignalPipeline(config=CONFIG, client=client)

    signals = _generate_signals(pipeline)
    print(f"[diagnose] signals={len(signals)}", flush=True)
    if not signals:
        print("[diagnose] no signals generated; aborting")
        return 2

    klines_map: dict[str, list[dict]] = {}
    start_ms = _iso_to_ms(START_ISO)
    for sym in SYMBOLS:
        klines_map[sym] = _klines_after(client, sym, start_ms - 7 * 24 * 3600 * 1000, 100000)
        print(f"  {sym}: {len(klines_map[sym])} klines", flush=True)

    # ── candidate arm only, with the supervisor decision recorder installed ──
    cfg = ExitConfig()
    records: list[dict] = []
    uninstall = _install_recorder(records)
    cand_rows = []
    t1 = time.time()
    try:
        cand_ev = CandidateEvaluator()
        for sig in signals:
            full = sorted(klines_map.get(sig.get("symbol", ""), []),
                          key=lambda k: float(k.get("timestamp", 0)))
            sig_ts = _sig_ts_ms(sig)
            kl = [k for k in full if float(k.get("timestamp", 0)) > sig_ts]
            ctx_bars = [k for k in full if float(k.get("timestamp", 0)) <= sig_ts][-CONTEXT_BARS:]
            cand_rows.append(cand_ev.evaluate_signal(
                sig, kl, make_decide_fn(cfg), context_klines=ctx_bars))
    finally:
        uninstall()
    print(f"[diagnose] candidate replay {time.time()-t1:.0f}s, "
          f"decisions={len(records)}", flush=True)

    # ── parity vs the accepted eval v2 (soft check — reported, not fatal) ──
    parity: dict = {"checked": False}
    if os.path.exists(V2_JSON):
        v2 = json.load(open(V2_JSON, encoding="utf-8"))
        cand_m = arm_metrics(cand_rows, cfg.bootstrap_seed, cfg.bootstrap_iters)
        _, reduces, closes = _change_stats(cand_rows)
        parity = {
            "checked": True,
            "signals_match": len(signals) == v2["n_signals"],
            "signals": len(signals), "v2_signals": v2["n_signals"],
            "expectancy_match":
                abs(cand_m["expectancy_net_r"] - v2["candidate"]["expectancy_net_r"]) < 1e-9,
            "expectancy": cand_m["expectancy_net_r"],
            "v2_expectancy": v2["candidate"]["expectancy_net_r"],
            "change_stats_match": {"changed": _changed(cand_rows),
                                   "reduces": reduces, "closes": closes} == v2["change_stats"],
            "change_stats": {"changed": _changed(cand_rows),
                             "reduces": reduces, "closes": closes},
            "v2_change_stats": v2["change_stats"],
        }
        print(f"[diagnose] parity vs exit_eval_v2: {parity}", flush=True)

    # ── classify against future price action ──
    closes_by_symbol: dict[str, tuple[list[int], list[float]]] = {}
    for sym, rows in klines_map.items():
        ordered = sorted(rows, key=lambda k: float(k["timestamp"]))
        closes_by_symbol[sym] = ([int(float(k["timestamp"])) for k in ordered],
                                 [float(k["close"]) for k in ordered])
    import bisect
    unscored = 0
    for rec in records:
        ts_list, close_list = closes_by_symbol.get(rec["symbol"], ([], []))
        i = bisect.bisect_right(ts_list, rec["ref_ts"]) if rec["ref_ts"] else 0
        future = close_list[i:i + HORIZON_BARS]
        # Same risk distance CandidateEvaluator uses: entry vs genesis stop.
        risk = abs(rec["entry"] - rec["stop_loss"])
        classify_record(rec, future, risk)
        if rec["class"] == "UNSCORED":
            unscored += 1

    unknown = [r for r in records if r["rule"] == "UNKNOWN"]
    if unknown:
        print(f"[diagnose] WARNING: {len(unknown)} decisions unmapped by rule_of "
              f"(first: {unknown[0]['action']} / {unknown[0]['rationale'][:80]})", flush=True)
    if unscored:
        print(f"[diagnose] WARNING: {unscored} UNSCORED decisions (no future bars)", flush=True)

    # ── risk distance: entry vs genesis stop (same as CandidateEvaluator) ──
    # (computed during classification loop via rec["_risk"] set below)
    meta = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "source": "offline_candidate_harness (scripts/eval_exits_v1.py scope, "
                  "recorded via ExitSupervisor.decide patch)",
        "surface_note": "local replay — production exit_decisions.json on the "
                        "Railway volume was NOT read (no Railway interaction); "
                        "replay decisions are simulated paper-applied regardless "
                        "of EXITS_APPLY (harness ignores the env var)",
        "scope": {"symbols": SYMBOLS, "window": [START_ISO, END_SETUP_ISO],
                  "step_bars_15m": STEP_BARS_15M, "cap": SETUP_CAP,
                  "context_bars": CONTEXT_BARS},
        "thresholds": {"horizon_bars": HORIZON_BARS, "threshold_r": THRESHOLD_R},
        "n_signals": len(signals),
        "n_decisions": len(records),
        "parity_vs_exit_eval_v2": parity,
        "unmapped_rules": len(unknown),
        "unscored": unscored,
    }
    os.makedirs("reports", exist_ok=True)
    with open(DECISIONS_JSON, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "decisions": records}, f, indent=1, default=str)
    print(f"[diagnose] wrote {DECISIONS_JSON}", flush=True)
    _write_tables(meta, records)
    print(f"[diagnose] done in {(datetime.now(timezone.utc) - t0).total_seconds():.0f}s",
          flush=True)
    return 0


def _changed(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("plan_changes"))


def _change_stats(rows: list[dict]):
    changed = _changed(rows)
    reduces = sum(1 for r in rows for e in r.get("supervisor_events", [])
                  if e.get("type") == "reduce")
    closes = sum(1 for r in rows for e in r.get("supervisor_events", [])
                 if e.get("type") == "close")
    return changed, reduces, closes


def _write_tables(meta: dict, records: list[dict]) -> None:
    from collections import Counter, defaultdict
    lines = ["# Exit decision diagnosis — computed tables (scripts/diagnose_exits.py)\n"]
    lines.append(f"- ran_at: {meta['ran_at']}  | source: {meta['source']}")
    lines.append(f"- signals: {meta['n_signals']}  | decisions: {meta['n_decisions']}  "
                 f"| horizon: {meta['thresholds']['horizon_bars']} bars  "
                 f"| threshold: {meta['thresholds']['threshold_r']}R")
    lines.append(f"- parity vs exit_eval_v2: `{json.dumps(meta['parity_vs_exit_eval_v2'])}`")
    lines.append(f"- unmapped rules: {meta['unmapped_rules']}  | unscored: {meta['unscored']}\n")

    def matrix(row_key, col_key, cols=None, title=""):
        lines.append(f"\n## {title}\n")
        rc = defaultdict(Counter)
        for r in records:
            rc[r[row_key]][r[col_key]] += 1
        colset = cols or sorted({c for cnt in rc.values() for c in cnt})
        lines.append(f"| {row_key} | " + " | ".join(colset) + " | total |")
        lines.append("|" + "---|" * (len(colset) + 2))
        for row in sorted(rc):
            total = sum(rc[row].values())
            lines.append(f"| {row} | " + " | ".join(str(rc[row][c]) for c in colset)
                         + f" | {total} |")
        total = len(records)
        lines.append(f"| **total** | " + " | ".join(
            str(sum(rc[r][c] for r in rc)) for c in colset) + f" | {total} |")

    lines.append("\n## Decisions by supervisor rule x action\n")
    rc = defaultdict(Counter)
    for r in records:
        rc[r["rule"]][r["action"]] += 1
    actions = sorted({a for cnt in rc.values() for a in cnt})
    lines.append("| rule | " + " | ".join(actions) + " | total |")
    lines.append("|" + "---|" * (len(actions) + 2))
    for rule in sorted(rc):
        lines.append(f"| {rule} | " + " | ".join(str(rc[rule][a]) for a in actions)
                     + f" | {sum(rc[rule].values())} |")

    matrix("action", "class",
           cols=["CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED"],
           title="Action x class")
    matrix("rule", "class",
           cols=["CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED"],
           title="Rule x class")
    matrix("signal_regime", "class",
           cols=["CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED"],
           title="Signal-time BTC regime x class")
    matrix("symbol", "class",
           cols=["CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED"],
           title="Symbol x class")
    matrix("state", "class",
           cols=["CORRECT", "PREMATURE", "MISSED", "NEUTRAL", "UNSCORED"],
           title="Position state x class")

    # worker proposal matrix
    lines.append("\n## Worker proposal matrix (proposed / adopted-as-basis / not adopted)\n")
    lines.append("| worker | action | proposed | adopted | not_adopted | guard_rejected |")
    lines.append("|---|---|---|---|---|---|")
    by_worker: dict = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # [prop, adopt, reject]
    for r in records:
        for a in r["assessments"]:
            if not a.get("proposed_action"):
                continue
            slot = by_worker[a["worker_id"]][a["proposed_action"]]
            slot[0] += 1
            if r["adopted_by"] == a["worker_id"] and r["action"] == a["proposed_action"]:
                slot[1] += 1
            else:
                slot[2] += 1
    for wid in sorted(by_worker):
        for act in sorted(by_worker[wid]):
            p, ad, na = by_worker[wid][act]
            gr = sum(1 for r in records
                     if isinstance(r.get("risk_guard"), dict)
                     and r["risk_guard"].get("ok") is False
                     and r["risk_guard"].get("action") == act
                     and any(a["worker_id"] == wid and a["proposed_action"] == act
                             for a in r["assessments"]))
            lines.append(f"| {wid} | {act} | {p} | {ad} | {na} | {gr} |")

    # dead-adoption proof line + MISSED detail
    rr_close = sum(1 for r in records
                   if any(a["worker_id"] == "riskreward"
                          and a["proposed_action"] == ExitAction.CLOSE_PAPER_POSITION.value
                          for a in r["assessments"]))
    rr_close_adopted = sum(1 for r in records if r["adopted_by"] == "riskreward"
                           and r["action"] == ExitAction.CLOSE_PAPER_POSITION.value)
    lines.append(f"\n- RR_DEAD reachability: riskreward proposed CLOSE {rr_close} times, "
                 f"adopted as basis {rr_close_adopted} times (expected 0 — no rule maps it)\n")

    missed = sorted((r for r in records if r["class"] == "MISSED"),
                    key=lambda r: r["move_r"] if r["move_r"] is not None else 0)
    lines.append("\n## MISSED decisions (worst move first, up to 25)\n")
    lines.append("| signal_id | symbol | side | regime | proposal(s) | move_r | rule |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in missed[:25]:
        props = ",".join(sorted({a["proposed_action"].replace("_PAPER_POSITION", "")
                                 .replace("INVALIDATE_PENDING_SETUP", "INVALIDATE")
                                 for a in r["assessments"]
                                 if a.get("proposed_action") in EXIT_PROPOSALS}))
        lines.append(f"| {r['signal_id']} | {r['symbol']} | {r['side']} | "
                     f"{r['signal_regime']} | {props} | {r['move_r']} | {r['rule']} |")
    lines.append(f"\nTotal MISSED: {len(missed)}  | "
                 f"CORRECT: {sum(1 for r in records if r['class'] == 'CORRECT')}  | "
                 f"PREMATURE: {sum(1 for r in records if r['class'] == 'PREMATURE')}  | "
                 f"NEUTRAL: {sum(1 for r in records if r['class'] == 'NEUTRAL')}\n")

    with open(TABLES_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {TABLES_MD}")


if __name__ == "__main__":
    import sys
    sys.exit(main())
