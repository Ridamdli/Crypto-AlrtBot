"""Read the mimic bot's event history. No secrets, no network.

Usage:
  python scripts/mimic_log.py [--data-dir mirror_data] [--level ERROR|WARN|INFO]
                              [--limit 30] [--summary] [--hours 24]
Examples:
  python scripts/mimic_log.py --summary            # health in one screen
  python scripts/mimic_log.py --level ERROR        # what needs eyes
  python scripts/mimic_log.py --level WARN --limit 20
On Railway: railway run --service pariston-mimic python scripts/mimic_log.py --summary
"""
import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")


def _fmt(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def main() -> None:
    ap = argparse.ArgumentParser(description="Mimic bot event history")
    ap.add_argument("--data-dir", default="mirror_data")
    ap.add_argument("--level", default="", help="ERROR|WARN|INFO")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()

    from mirror.eventlog import EventLog
    log = EventLog(args.data_dir)
    level = args.level.upper()
    if level not in ("", "ERROR", "WARN", "INFO"):
        raise SystemExit("level must be ERROR, WARN or INFO")

    if args.summary:
        import json
        s = log.summary(int(args.hours * 3600))
        print(f"window: last {args.hours:g}h | events: {s['total']} | "
              f"levels: {json.dumps(s['by_level'])}")
        top = sorted(s["by_event"].items(), key=lambda kv: -kv[1])[:12]
        for ev, n in top:
            print(f"  {n:5d}  {ev}")
        last = s.get("last_error")
        if last:
            print(f"last ERROR [{_fmt(last.get('ts', 0))}] {last.get('event')}: "
                  f"{str((last.get('detail') or {}).get('error', ''))[:160]}")
        else:
            print("last ERROR: none in window")
        return

    events = log.recent(level, args.limit)
    if not events:
        print("(no events)")
        return
    for e in events:
        d = e.get("detail") or {}
        extra = d.get("coin") or d.get("reason") or d.get("error") or ""
        if isinstance(extra, str) and len(extra) > 90:
            extra = extra[:90]
        print(f"[{_fmt(e.get('ts', 0))}] {e.get('level'):<5} "
              f"{e.get('event'):<14} {extra}")


if __name__ == "__main__":
    main()
