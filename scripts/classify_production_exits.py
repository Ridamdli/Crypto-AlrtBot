"""Bar-level classification of the production exit ledger.

Same methodology as the replay diagnosis (scripts/diagnose_exits.py): for each
decision, take the 15m close of the decision bar as the reference, look 24 bars
(6h) ahead, convert the move to signed R using the signal's entry->stop risk,
then classify CORRECT / PREMATURE / MISSED / NEUTRAL.

Usage: python -m scripts.classify_production_exits
"""
import bisect
import collections
import json
import statistics

from src.data.historical_store import HistoricalStore
from scripts.diagnose_exits import (EXIT_PROPOSALS, HORIZON_BARS, classify,
                                    rule_of, signed_move_r)

LEDGER = "prod_evidence/pariston_exit_decisions.json"
OUTCOMES = "prod_evidence/pariston_outcomes.json"
OUT_JSON = "reports/production_bar_level.json"
OUT_MD = "reports/production_bar_level.md"


def load_sleep(store, symbol):
    with store.get_connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, close FROM klines WHERE symbol=? AND "
            "interval='15m' ORDER BY timestamp", (symbol,)).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def table(title, rows, head):
    lines = [f"### {title}", "", "| " + " | ".join(head) + " |",
             "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines) + "\n"


def main():
    ledger = json.load(open(LEDGER, encoding="utf-8"))
    outcomes = json.load(open(OUTCOMES, encoding="utf-8"))
    decisions = ledger["decisions"]
    store = HistoricalStore()

    series = {s: load_sleep(store, s) for s in {v["symbol"] for v in outcomes.values()}}

    recs, skipped, censored, mismatches = [], 0, 0, 0
    for d in decisions:
        sig = outcomes.get(d["signal_id"])
        if not sig:
            skipped += 1
            continue
        ts, closes = series[sig["symbol"]]
        i = bisect.bisect_right(ts, d["data_ts_ms"]) - 1
        if i < 0:
            skipped += 1
            continue
        ref_close = closes[i]
        future = closes[i + 1:i + 1 + HORIZON_BARS]
        if len(future) < HORIZON_BARS:
            censored += 1
        risk = abs(float(sig["entry"]) - float(sig["stop_loss"]))
        move = signed_move_r(sig["side"], ref_close, future, risk)
        has_exit = any(a.get("proposed_action") in EXIT_PROPOSALS
                       for a in d["assessments"])
        last_close = next((a["evidence"].get("last_close")
                           for a in d["assessments"]
                           if a["worker_id"] == "structure"
                           and a.get("evidence")), None)
        if last_close is not None and abs(last_close - ref_close) > 1e-9:
            mismatches += 1
        recs.append({
            "signal_id": d["signal_id"], "action": d["action"],
            "rule": rule_of(d["action"], d["rationale"]),
            "symbol": sig["symbol"], "side": sig["side"],
            "move_r": None if move is None else round(move, 4),
            "class": classify(d["action"], has_exit, move),
            "has_exit_proposal": has_exit,
            "applied": bool(d.get("events_applied")),
            "linked": d.get("linked_outcome"),
        })

    scored = [r for r in recs if r["move_r"] is not None]
    print(f"decisions={len(decisions)} scored={len(scored)} "
          f"skipped={skipped} censored(<{HORIZON_BARS} bars)={censored} "
          f"ref_close_mismatch={mismatches}")
    assert mismatches <= max(5, len(scored) // 100), (
        f"bar alignment failed: {mismatches} mismatches")

    classes = ["CORRECT", "PREMATURE", "MISSED", "NEUTRAL"]
    md = ["# Production bar-level classification", "",
          f"Decisions: {len(decisions)}  |  scored: {len(scored)}  |  "
          f"skipped (no signal/bar): {skipped}  |  censored: {censored}  |  "
          f"ref-close mismatches: {mismatches}", ""]

    overall = collections.Counter(r["class"] for r in scored)
    md.append(table("Overall", [[c, overall[c], pct(overall[c], len(scored))]
                                for c in classes], ["class", "n", "share"]))
    print("overall", dict(overall))

    by_action = []
    for a in sorted({r["action"] for r in scored}):
        sub = [r for r in scored if r["action"] == a]
        row = [a, len(sub)] + [sum(1 for r in sub if r["class"] == c)
                               for c in classes]
        row.append(f"{statistics.mean([r['move_r'] for r in sub]):+.3f}")
        by_action.append(row)
    md.append(table("By action", by_action,
                    ["action", "n"] + [c.lower() for c in classes] + ["mean move R"]))
    print("by_action", [list(map(str, r)) for r in by_action])

    by_rule = []
    for a in sorted({r["rule"] for r in scored}):
        sub = [r for r in scored if r["rule"] == a]
        row = [a, len(sub)] + [sum(1 for r in sub if r["class"] == c)
                               for c in classes]
        by_rule.append(row)
    md.append(table("By supervisor rule", by_rule,
                    ["rule", "n"] + [c.lower() for c in classes]))

    applied = [r for r in scored if r["applied"]]
    not_applied = [r for r in scored if not r["applied"]]
    rows = []
    for label, sub in (("applied", applied), ("not applied", not_applied)):
        rows.append([label, len(sub)] + [sum(1 for r in sub if r["class"] == c)
                                         for c in classes])
    md.append(table("Applied vs not applied", rows,
                    ["subset", "n"] + [c.lower() for c in classes]))
    print("applied", dict(collections.Counter(r["class"] for r in applied)),
          "not_applied", dict(collections.Counter(r["class"] for r in not_applied)))

    # Per-signal rollup: 97 signals, some with many decisions.
    per_sig = collections.defaultdict(collections.Counter)
    for r in scored:
        per_sig[r["signal_id"]][r["class"]] += 1
    missed_signals = [s for s, c in per_sig.items() if c["MISSED"]]
    premature_signals = [s for s, c in per_sig.items() if c["PREMATURE"]]
    md.append(table("Per-signal exposure", [
        ["signals scored", len(per_sig)],
        ["signals with >=1 MISSED", len(missed_signals)],
        ["signals with >=1 PREMATURE", len(premature_signals)],
        ["signals with >=1 CORRECT", sum(1 for c in per_sig.values() if c["CORRECT"])],
    ], ["metric", "value"]))

    # Did the exits we actually took help? linked_outcome is the real result.
    linked = [r for r in scored if r["linked"]]
    by_linked = collections.Counter(
        (r["action"], r["linked"].get("status")) for r in linked)
    md.append(table("Linked real outcomes for scored decisions",
                    [[a, s, n] for (a, s), n in sorted(by_linked.items())],
                    ["action", "linked status", "n"]))

    moves = [r["move_r"] for r in scored]
    md.append(table("Move distribution", [
        ["mean", f"{statistics.mean(moves):+.3f}R"],
        ["median", f"{statistics.median(moves):+.3f}R"],
        ["min", f"{min(moves):+.3f}R"],
        ["max", f"{max(moves):+.3f}R"],
    ], ["stat", "value"]))

    open(OUT_JSON, "w", encoding="utf-8").write(
        json.dumps({"summary": {"decisions": len(decisions), "scored": len(scored),
                                "skipped": skipped, "censored": censored,
                                "mismatches": mismatches},
                    "overall": dict(overall), "records": recs}, indent=1))
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
    print("wrote", OUT_JSON, OUT_MD)


if __name__ == "__main__":
    main()
