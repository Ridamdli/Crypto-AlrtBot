"""Sim-reservation mirror: LOCAL, OBSERVATIONAL-ONLY tracking of Invo sim funds.

Authoritative source: the Invo server (remainingSim + investments list).
This ledger never blocks sharing, never fabricates rejections, and never
assumes an insufficient-funds schema (none has been observed). Its jobs:

- mirror every local share/close/expiry as a reservation lifecycle,
- reconcile against remote snapshots and REPORT divergence (never enforce),
- represent unknown/stale remote state explicitly instead of zeroing books.

Accounting identity (derived from verified server behavior):
  open debits entrySim 1:1; close credits principal +/- P&L minus fees.
  Local mirror: reserved_total = sum(unreleased reservations).
  There is NO `available + reserved = initial` invariant locally: realized
  P&L, fees, and app-side actions move the server bank independently. The
  reconcile() call reports bank drift for operators instead of enforcing it.

Threading: an in-process lock guards read-modify-write so concurrent
scheduler threads cannot lose updates. Across processes, atomic file
publish (tmp + os.replace, same as ExitDecisionLedger) guarantees readers
never see a TORN file — but it does NOT serialize writers: two processes
reserving concurrently can still lose one update (last-writer-wins), and
nothing here excludes a sibling scheduler loop. Same best-effort
standing as volume_lock.py: overlap is made small and observable
(reconcile reports divergence), never claimed to be prevented.
"""

import json as _json
import os as _os
import threading as _threading
import time as _time
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

LEDGER_NAME = "sim_reservations.json"
SIM_RESOLVED_TTL_MS = 30 * 24 * 3600 * 1000

# Resolved records (released/gone) older than SIM_RESOLVED_TTL_MS are
# pruned. Reserved records are NEVER pruned for age — an old open
# reservation is evidence, not garbage. Mirrors the tracker record TTL.


class SimLedger:
    """Observational mirror of sim-fund reservations. Never blocks."""

    def __init__(self, storage_dir: str = "data_store",
                 ledger_name: str = LEDGER_NAME):
        self.storage_dir = storage_dir
        self.ledger_path = _os.path.join(storage_dir, ledger_name)
        self.tmp_path = self.ledger_path + ".tmp"
        self._mu = _threading.Lock()
        self.healthy = True
        self._state = self._load()

    # ── persistence ─────────────────────────────────────────────
    def _blank(self) -> Dict[str, Any]:
        return {"version": 1, "reservations": {}, "baseline": None}

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if not isinstance(data, dict):
                return self._blank()
            data.setdefault("reservations", {})
            return data
        except FileNotFoundError:
            return self._blank()
        except (ValueError, OSError) as e:
            logger.error(f"[SimLedger] unreadable ({e}) — starting blank, flagged.")
            self.healthy = False
            return self._blank()

    def _save(self) -> bool:
        try:
            _os.makedirs(self.storage_dir, exist_ok=True)
            with open(self.tmp_path, "w", encoding="utf-8") as f:
                _json.dump(self._state, f, indent=2)
            _os.replace(self.tmp_path, self.ledger_path)
            return True
        except OSError as e:
            logger.error(f"[SimLedger] write failed ({e}) — in-memory only.")
            self.healthy = False
            return False

    # ── lifecycle ───────────────────────────────────────────────
    def reserve(self, signal_id: str, symbol: str, amount: float,
                note: str = "") -> Dict[str, Any]:
        """Record a share's fund commitment. Idempotent by signal_id.

        ALWAYS succeeds locally (advisory only). A duplicate returns the
        original record flagged — never double-counts.
        """
        with self._mu:
            existing = self._state["reservations"].get(signal_id)
            if isinstance(existing, dict) and existing.get("status") == "reserved":
                return {**existing, "duplicate": True}
            rec = {"signal_id": signal_id, "symbol": symbol,
                   "amount": float(amount), "status": "reserved",
                   "reserved_at": _time.time(), "note": note,
                   "duplicate": False}
            self._state["reservations"][signal_id] = rec
            if not self._save():
                rec = {**rec, "persisted": False}
            else:
                rec = {**rec, "persisted": True}
            return rec

    def release(self, signal_id: str, credit: Any = 0.0, fee: float = 0.0,
                note: str = "") -> Dict[str, Any]:
        """Release a reservation (close / expiry / rejected open).

        Idempotent: second release is a no-op returning released=False.
        Unknown ids are explicit (never silently credited).
        credit=None means "freed, amount unknown" (e.g. server-side close
        where only the fact of release is known): the reservation is freed
        but excluded from released_total rather than fabricated as zero.
        """
        with self._mu:
            rec = self._state["reservations"].get(signal_id)
            if not isinstance(rec, dict) or rec.get("status") != "reserved":
                reason = ("already-released" if isinstance(rec, dict)
                          and rec.get("status") in ("released", "gone")
                          else "unknown-reservation")
                return {"released": False, "reason": reason}
            try:
                credit_f = None if credit is None else float(credit)
            except (TypeError, ValueError):
                credit_f = None
            rec = {**rec, "status": "released", "credit": credit_f,
                   "fee": float(fee), "released_at": _time.time(),
                   "note": note or rec.get("note", "")}
            self._state["reservations"][signal_id] = rec
            self._save()
            return {"released": True, "record": rec}

    def mark_gone(self, signal_id: str, evidence: str = "") -> Dict[str, Any]:
        """Terminal: server says the position is gone. Frees the reservation
        without crediting (credit unknown) — reported, not assumed."""
        with self._mu:
            rec = self._state["reservations"].get(signal_id)
            if not isinstance(rec, dict) or rec.get("status") != "reserved":
                return {"status": rec.get("status", "unknown") if isinstance(rec, dict) else "unknown",
                        "changed": False}
            rec = {**rec, "status": "gone", "evidence": evidence,
                   "released_at": _time.time()}
            self._state["reservations"][signal_id] = rec
            self._save()
            return {"status": "gone", "changed": True}

    # ── views ───────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        """Pure totals over unreleased reservations. No remote data."""
        with self._mu:
            recs = [r for r in self._state["reservations"].values()
                    if isinstance(r, dict)]
        reserved = [r for r in recs if r.get("status") == "reserved"]
        released = [r for r in recs if r.get("status") == "released"]

        def _num(r, key):
            try:
                v = r.get(key, 0.0)
                return 0.0 if v is None else float(v)
            except (TypeError, ValueError):
                return 0.0

        return {
            "reserved_total": sum(_num(r, "amount") for r in reserved),
            "reserved_count": len(reserved),
            "released_total": sum(_num(r, "credit") for r in released),
            "fees_total": sum(_num(r, "fee") for r in released),
            "gone_count": sum(1 for r in recs if r.get("status") == "gone"),
            "healthy": self.healthy,
        }

    def advisory_available(self, remote_remaining: Optional[float],
                           next_amount: float = 0.0) -> Dict[str, Any]:
        """Advisory only: compare remote snapshot against local reservations.

        Returns a would_overdraft FLAG — callers must treat it as monitoring
        signal, never as permission. remote_remaining=None means unknown.
        """
        snap = self.snapshot()
        if remote_remaining is None:
            return {"remote_remaining": None, "reserved_total": snap["reserved_total"],
                    "implied_available": None, "would_overdraft": False,
                    "stale": True}
        implied = float(remote_remaining) - snap["reserved_total"]
        return {"remote_remaining": float(remote_remaining),
                "reserved_total": snap["reserved_total"],
                "implied_available": implied,
                "would_overdraft": implied < float(next_amount),
                "stale": False}

    def reconcile(self, remote_remaining: Optional[float],
                  remote_open: Optional[Dict[str, float]]) -> Dict[str, Any]:
        """Compare local mirror against an authoritative remote snapshot.

        - remote_open maps our signal_ids (or symbols) to open sim amounts.
        - bank_drift tracks change in (remaining + open) vs the previous
          reconcile call: P&L/fees/deposits drift it; reported, not enforced.
        - Unknown remote (None) leaves books untouched and says so.
        """
        with self._mu:
            if remote_remaining is None or remote_open is None:
                return {"status": "unknown",
                        "note": "remote snapshot unavailable or stale; local books untouched",
                        "reserved_total": self._snapshot_locked()["reserved_total"]}
            recs = self._state["reservations"]
            self._prune_resolved(recs)
            local_ids = {sid for sid, r in recs.items()
                         if isinstance(r, dict) and r.get("status") == "reserved"}
            remote_ids = set(remote_open.keys())
            local_only = sorted(local_ids - remote_ids)
            remote_only = sorted(remote_ids - local_ids)
            local_reserved = sum(float(recs[s].get("amount", 0.0)) for s in local_ids)
            remote_total = sum(float(remote_open[s]) for s in remote_ids)
            bank_total = float(remote_remaining) + remote_total
            prev = self._state.get("baseline")
            drift = 0.0 if prev is None else bank_total - float(prev.get("bank_total", bank_total))
            self._state["baseline"] = {"bank_total": bank_total, "at": _time.time()}
            self._save()
        status = "reconciled" if not local_only and diff_is_zero(
            local_reserved - remote_total) else "divergent"
        return {"status": status,
                "diff_reserved": local_reserved - remote_total,
                "bank_drift": drift,
                "local_only": local_only,
                "remote_only": remote_only,
                "reserved_total": local_reserved}

    @staticmethod
    def _prune_resolved(recs: Dict[str, Any],
                        ttl_ms: int = SIM_RESOLVED_TTL_MS,
                        now_ms: Optional[float] = None) -> int:
        """Drop released/gone records older than the TTL. Returns count.

        Safety rules (all load-bearing):
        - reserved records are NEVER touched, regardless of age;
        - records without a parseable released_at timestamp are kept;
        - only exact statuses "released"/"gone" are eligible.
        A pruned id that reappears later reads as "unknown-reservation"
        (safe: no double credit, no silent success).
        """
        now = _time.time() * 1000 if now_ms is None else now_ms
        doomed = []
        for sid, r in recs.items():
            if not isinstance(r, dict) or r.get("status") not in ("released", "gone"):
                continue
            # Ledger timestamps are seconds-epoch (time.time()); the TTL is
            # milliseconds. Convert before comparing — comparing raw would
            # treat every real stamp as ancient and prune live history.
            try:
                ts_s = float(r.get("released_at", 0) or 0)
            except (TypeError, ValueError):
                continue  # no trustworthy timestamp: keep
            if ts_s > 0 and now - ts_s * 1000 > ttl_ms:
                doomed.append(sid)
        for sid in doomed:
            del recs[sid]
        return len(doomed)

    def _snapshot_locked(self) -> Dict[str, Any]:
        recs = [r for r in self._state["reservations"].values()
                if isinstance(r, dict)]
        reserved = [r for r in recs if r.get("status") == "reserved"]
        return {"reserved_total": sum(float(r.get("amount", 0.0)) for r in reserved)}


def diff_is_zero(x: float, tol: float = 1e-9) -> bool:
    return abs(x) <= tol
