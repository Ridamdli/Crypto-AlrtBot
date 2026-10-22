"""W9 verification: P2 must not change any historical ADJUST decision.

Replays every production decision through the real RiskGuard with the P2
gate active. For each ADJUST_TAKE_PROFITS row it reconstructs the plan
target in force at the time, the proposed target, and the 15m close that
would serve as the mark, then asks the guard. A "flip" is a row that was
applied in production but is now rejected.

Expected: 0 flips, 0 widenings (all 31 historical edits pulled the target
toward the market).

Usage: python -m scripts.verify_p2_zero_change
"""
import bisect
import json

from src.data.historical_store import HistoricalStore
from src.exits.config import ExitConfig
from src.exits.models import ExitPlan, PositionState, PositionView
from src.exits.risk_guard import RiskGuard
from scripts.classify_production_exits import load_sleep

LEDGER = "prod_evidence/pariston_exit_decisions.json"
OUTCOMES = "prod_evidence/pariston_outcomes.json"
ACTION = "ADJUST_TAKE_PROFITS"


def main() -> int:
    decisions = json.load(open(LEDGER, encoding="utf-8"))["decisions"]
    outcomes = json.load(open(OUTCOMES, encoding="utf-8"))
    store = HistoricalStore()
    series = {s: load_sleep(store, s) for s in {v["symbol"] for v in outcomes.values()}}

    guard = RiskGuard(ExitConfig())
    last_tp: dict[str, float] = {}
    last_stop: dict[str, float] = {}
    rows = []
    for d in decisions:
        sid = d["signal_id"]
        sig = outcomes.get(sid)
        if sig is None:
            continue
        if d["action"] == ACTION:
            ts, closes = series[sig["symbol"]]
            i = bisect.bisect_right(ts, d["data_ts_ms"]) - 1
            mark = closes[i] if i >= 0 else None
            plan_tp = float(last_tp.get(sid, sig.get("tp1", sig.get("tp"))))
            plan_stop = float(last_stop.get(sid, sig["stop_loss"]))
            new_tp = float(d["plan_after"]["tp"])
            view = PositionView(
                sig, None, PositionState.OPEN,
                ExitPlan(sid, 0, plan_stop, plan_tp, 0), False)
            ok, reasons = guard.validate(
                view, ACTION, {"tp": new_tp}, mark=mark)
            rows.append({
                "sid": sid, "side": sig["side"], "mark": mark,
                "plan_tp": plan_tp, "new_tp": new_tp,
                "ok": ok, "reasons": reasons,
                "applied_in_prod": bool(d.get("plan_after")),
            })
        if d.get("plan_after"):
            last_tp[sid] = float(d["plan_after"]["tp"])
            last_stop[sid] = float(d["plan_after"]["stop_loss"])

    flips = [r for r in rows if r["applied_in_prod"] and not r["ok"]]
    widen = [r for r in rows if not r["ok"] and any(
        "moves away from mark" in x for x in r["reasons"])]
    missing_mark = [r for r in rows if not r["ok"] and any(
        "mark unavailable" in x for x in r["reasons"])]

    print(f"ADJUST rows replayed: {len(rows)}")
    print(f"accepted: {sum(1 for r in rows if r['ok'])}")
    print(f"rejected: {len(rows) - sum(1 for r in rows if r['ok'])}")
    print(f"widenings: {len(widen)}")
    print(f"missing mark: {len(missing_mark)}")
    print(f"flips (applied in prod, now rejected): {len(flips)}")
    for r in flips[:10]:
        print("  FLIP", r["sid"], r["side"], r["plan_tp"], "->", r["new_tp"],
              "mark", r["mark"], r["reasons"])

    assert len(flips) == 0, f"P2 changed {len(flips)} historical decisions"
    assert len(widen) == 0, f"{len(widen)} historical edits were widenings"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
