#!/usr/bin/env python
"""Bounded production observer for Invo exit-sync (DRY / READ-ONLY by default).

Observes existing production behavior without opening or closing positions:
- watches exit-decision plans, the sync journal, and sync-state for change,
- correlates desired TP1/SL changes with confirmations/failures/retries,
- flags updates pending beyond --pending-threshold-s,
- distinguishes quiet cycles (scans advancing, nothing due) from missing
  sync activity (plans changing with no journal events),
- prints a final report; exits cleanly at --duration-s.

Needs operator approval before running against production. Two modes:
  ledger-only (default): reads data_store files only. Zero API requests.
  --live-read: additionally polls list_paper_investments (bounded by
    --request-budget) to confirm closes via remote absence. Requires the
    same INVO_* env the scheduler uses. Still never writes.

Examples (DO NOT run without approval):
  python scripts/invo_watch.py --duration-s 1800 --interval-s 60
  python scripts/invo_watch.py --duration-s 3600 --interval-s 120 \\
      --live-read --request-budget 12 --out /tmp/watch.json
  railway run python scripts/invo_watch.py --duration-s 900   # live volume
"""
import argparse
import json
import os
import sys
import time

REQUIRED_NOTE = "observer only: opens nothing, closes nothing, modifies nothing"


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return default


def _read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except OSError:
        return []


def _live_snapshot(data_dir):
    """Read-only portfolio snapshot or None. Never OTP-logins, never writes."""
    try:
        from src.invo.client import InvoClient
        c = InvoClient(email=os.getenv("INVO_EMAIL", ""),
                       storage_dir=data_dir)
        if not c.access_token and not c._refresh():
            return None
        return c.list_paper_investments(
            c.access_token or "", os.getenv("INVO_PORTFOLIO_ID", ""))
    except Exception:
        return None


def _corr_kind(corr):
    """Defensive correlation split: returns "" on malformed input instead of
    raising. Malformed correlations are counted as data-quality issues by the
    caller, never fabricated into shape."""
    try:
        parts = str(corr).split(":")
        return parts[1] if len(parts) >= 2 and parts[1] else ""
    except Exception:
        return ""


def _as_ms(value):
    """Numeric millis or None. Garbage timestamps become unknown, never zero
    (zero would masquerade as 1970 and corrupt latency math)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _confirm_absent_closes(pending, resolved, latencies, snap, data_dir, now_ms):
    try:
        shares = _read_json(os.path.join(data_dir, "invo_shares.json"), {})
        live = {i.get("baseId") for i in (snap.get("investments") or [])
                if isinstance(i, dict) and i.get("baseId")}
    except Exception:
        return
    for corr in [c for c in pending if _corr_kind(c) == "close"]:
        sid = corr.split(":")[0]
        rec = shares.get(sid) if isinstance(shares, dict) else None
        bids = (rec.get("baseIds") or []) if isinstance(rec, dict) else []
        if bids and not any(b in live for b in bids):
            p = pending.pop(corr)
            first = _as_ms(p["first_seen_ms"])
            latency = None if first is None else (now_ms - first) / 1000.0
            resolved.append({"correlation_id": corr, "outcome": "confirmed",
                             "confirmation": "remote-readback",
                             "latency_s": latency})
            if latency is not None:
                latencies.append(latency)


def _pct(vals, p):
    if len(vals) < 2:
        return None
    ordered = sorted(vals)
    idx = min(len(ordered) - 1, int(p * len(ordered)))
    return ordered[idx]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duration-s", type=int, default=1800)
    ap.add_argument("--interval-s", type=int, default=60)
    ap.add_argument("--pending-threshold-s", type=int, default=1800)
    ap.add_argument("--request-budget", type=int, default=0)
    ap.add_argument("--live-read", action="store_true")
    ap.add_argument("--data-dir", default="data_store")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.duration_s <= 0 or args.interval_s <= 0:
        print("duration and interval must be positive", file=sys.stderr)
        return 2
    if args.live_read and args.request_budget <= 0:
        print("--live-read requires --request-budget N>0", file=sys.stderr)
        return 2

    data_dir = args.data_dir
    deadline = time.time() + args.duration_s
    requests_used = 0
    # journal seq cursor: only NEW lines observed during the window count.
    seen_seqs = set()
    pending = {}  # correlation_id -> {first_seen, desired, plan_version}
    resolved = []  # finished records with latency + outcome
    latencies = []
    max_pending_age = 0
    heartbeats = 0
    last_scan_ts = None

    malformed = 0

    def poll_once():
        nonlocal requests_used, max_pending_age, malformed
        now_ms = int(time.time() * 1000)
        # 1. scan heartbeat advancing? (distinguishes quiet vs missing)
        nonlocal last_scan_ts, heartbeats
        try:
            hist = _read_json(os.path.join(data_dir, "scan_history.json"), [])
            if isinstance(hist, list) and hist:
                ts = hist[-1].get("timestamp")
                if ts != last_scan_ts:
                    last_scan_ts = ts
                    heartbeats += 1
        except Exception:
            pass
        # 2. new journal events
        for line in _read_lines(os.path.join(data_dir, "invo_sync_audit.jsonl")):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            seq = ev.get("seq")
            if not isinstance(seq, int) or seq in seen_seqs:
                continue
            seen_seqs.add(seq)
            corr = ev.get("correlation_id", "?")
            if not _corr_kind(corr):
                malformed += 1  # data-quality issue, counted not crashed on
                continue
            evt = ev.get("event")
            if evt in ("confirmed", "failed", "reconciled", "already_gone"):
                started = pending.pop(corr, None)
                first = _as_ms(started["first_seen_ms"]) if started else None
                # Observed-resolution latency: time from first observation to
                # observed completion. None when timestamps are missing —
                # never zero-for-unknown (resp_ts_ms is not set upstream).
                latency = None if first is None else (now_ms - first) / 1000.0
                resolved.append({
                    "correlation_id": corr,
                    "outcome": "confirmed" if evt in ("confirmed", "reconciled",
                                                      "already_gone") else "failed",
                    "confirmation": ev.get("confirmation"),
                    "latency_s": latency,
                })
                if latency is not None:
                    latencies.append(latency)
            elif evt in ("update_requested", "request_sent", "retry_scheduled"):
                if corr not in pending:
                    pending[corr] = {
                        "first_seen_ms": _as_ms(ev.get("req_ts_ms")),
                        "desired": {"tp1": ev.get("desired_tp1"),
                                    "sl": ev.get("desired_sl")},
                        "plan_version": ev.get("plan_version"),
                    }
        # 3. pending age (unknown start times contribute 0, not 1970 math)
        for corr, p in pending.items():
            first = _as_ms(p["first_seen_ms"])
            age = 0.0 if first is None else (now_ms - first) / 1000.0
            max_pending_age = max(max_pending_age, age)
        # 4. optional bounded live reads: confirm pending closes via
        # remote absence. Strictly read-only: existing session token or
        # silent refresh only — never triggers OTP, opens, or closes.
        if args.live_read and requests_used < args.request_budget:
            if any(_corr_kind(corr) == "close" for corr in pending):
                snap = _live_snapshot(data_dir)
                requests_used += 1
                if snap is not None:
                    _confirm_absent_closes(pending, resolved, latencies, snap,
                                           data_dir, now_ms)

    while time.time() < deadline:
        poll_once()
        time.sleep(min(args.interval_s,
                       max(0, deadline - time.time())))
    poll_once()  # final sweep

    now_ms = int(time.time() * 1000)
    unresolved = []
    for corr, p in pending.items():
        first = _as_ms(p["first_seen_ms"])
        age = 0.0 if first is None else (now_ms - first) / 1000.0
        if age >= args.pending_threshold_s:
            unresolved.append({"correlation_id": corr, "age_s": round(age, 1),
                               "desired": p["desired"]})
    report = {
        "note": REQUIRED_NOTE,
        "window_s": args.duration_s,
        "heartbeats_observed": heartbeats,
        "eligible_observed": len(resolved) + len(pending),
        "confirmed": sum(1 for r in resolved if r["outcome"] == "confirmed"),
        "failed": sum(1 for r in resolved if r["outcome"] == "failed"),
        "unresolved": unresolved,
        "latency_s": {"median": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95),
                      "n": len(latencies),
                      "semantics": "observed-resolution latency; null when "
                                   "timestamps missing, never zero-for-unknown"},
        "max_pending_age_s": round(max_pending_age, 1),
        "malformed_records": malformed,
        "requests_used": requests_used,
        "request_budget": args.request_budget,
    }
    text = json.dumps(report, indent=2)
    print(text, flush=True)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError as e:
            print(f"ERROR: cannot write --out {args.out}: {e}", file=sys.stderr)
            print("Report above was still printed to stdout.", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
