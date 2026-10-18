"""Invo exit-sync: propagate APPLIED orchestrator decisions to paper positions.

State-based and idempotent: compares the exit ledger's active plans against
the last-synced levels and only emits actions on real differences. A repeat
run with no new applied decisions emits zero actions.

Scope rules (per operator direction):
- TP + SL only. The investments API carries one priceTarget, built from the
  plan's single tp.
- CLOSE fires only on applied supervisor-close events (genuine invalidation).
- REDUCE / FLAG_CONFLICT / INVALIDATE_PENDING_SETUP have no
  investment-level representation: logged, never posted.

Nothing here touches real-money paths (no dex/* calls, no order flow).

Reliability model (see module docstring sections in code):
- SyncTracker: per-signal sync state machine (pending/in_flight/confirmed/
  retryable/permanent/already-gone) with bounded retries, deterministic
  backoff+jitter, plan-version staleness suppression, and restart recovery.
- Timeouts are AMBIGUOUS, never rejections: modify replays identical values
  (value-idempotent; the server's "empty userChanges" reply proves
  convergence); close reconciles via remote absence before reposting.
- Exactly-once is never claimed: delivery is at-least-once with
  value-idempotent operations, recorded as such in the audit trail.
"""
import hashlib as _hashlib
import json as _json
import os as _os
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests as _requests

SYNC_STATE_NAME = "invo_exit_sync.json"

TERMINAL_STATUSES = {"confirmed", "already-applied", "already-gone",
                     "permanent_failure"}

# Terminal sync records older than this are pruned (default 30 days).
# Pending / in-flight / retryable records are NEVER pruned for age —
# an old unresolved record is evidence, not garbage. Pruning only drops
# records whose outcome is final AND whose audit history lives in the
# journal; the sync-state synced_levels/closed lists are untouched.
TERMINAL_RECORD_TTL_MS = 30 * 24 * 3600 * 1000

# Absolute ceiling for any single retry delay, including server-requested
# ones. A Retry-After beyond this is honored only up to the ceiling, and
# the attempt budget still bounds total retries.
SYNC_RETRY_AFTER_CEIL_S = 86400


@dataclass(frozen=True)
class SyncRetryConfig:
    """Retry policy. All knobs code-defaulted; tuned by evidence, not hope."""
    max_attempts: int = 5
    backoff_base_s: int = 60
    backoff_cap_s: int = 3600
    # Attempts whose backoff window has NOT elapsed are reported (visible in
    # gate reasons and audit "suppressed" events) and skipped for now — then
    # re-executed once the window elapses, until the attempt budget runs out.
    # Nothing is silently dropped: every skip carries its reason.


def backoff_delay_s(attempt: int, base_s: int, cap_s: int, key: str = "") -> float:
    """Deterministic exponential backoff with hash jitter (no RNG).

    delay = min(base * 2^(attempt-1) + (sha1(key|attempt) % 30), cap).
    Deterministic so tests and replays agree exactly.
    """
    exp = float(base_s) * (2 ** max(0, attempt - 1))
    jitter = int(_hashlib.sha1(f"{key}|{attempt}".encode()).hexdigest(), 16) % 30
    return min(exp + jitter, float(cap_s))


def _action_id(sid: str, kind: str, plan_version: Any, levels: Any) -> str:
    raw = _json.dumps({"sid": sid, "kind": kind, "v": plan_version,
                       "levels": levels}, sort_keys=True, default=str)
    return _hashlib.sha256(raw.encode()).hexdigest()[:16]


def classify_sync_error(exc: BaseException) -> dict[str, Any]:
    """Map an execution failure to a retry policy. Pure function.

    Kinds: timeout (ambiguous, retryable), rate_limited (honor Retry-After),
    server-error (retryable), auth (surface, no in-loop retry), permanent
    (validation — stop), already-applied (converged — terminal success),
    already-gone (terminal), unknown (retryable ONLY within budget).
    """
    from src.invo.client import InvoApiError, InvoAuthError, err_is_gone
    if isinstance(exc, InvoAuthError):
        return {"retryable": False, "kind": "auth", "retry_after_s": None}
    if isinstance(exc, (_requests.exceptions.Timeout,
                        _requests.exceptions.ConnectionError)):
        return {"retryable": True, "kind": "timeout", "retry_after_s": None}
    if isinstance(exc, _requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            retry_after: int | None = None
            try:
                headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                retry_after = int(headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            return {"retryable": True, "kind": "rate_limited",
                    "retry_after_s": retry_after}
        if isinstance(status, int) and 500 <= status <= 599:
            return {"retryable": True, "kind": "server-error", "retry_after_s": None}
        return {"retryable": False, "kind": "client-error", "retry_after_s": None}
    if isinstance(exc, InvoApiError):
        # Transport status is structural evidence; body TEXT is not (except
        # shapes verified live below). Unknown text is retryable ONLY within
        # the attempt budget — bounded, never indefinite.
        body = getattr(exc, "body", "") or ""
        if err_is_gone(body):
            return {"retryable": False, "kind": "already-gone", "retry_after_s": None}
        squashed = str(body).lower().replace(" ", "")
        if "emptyuserchanges" in squashed:
            # Observed live: reposting identical TP/SL draws this. The server
            # attests desired == actual: converged, terminal success.
            return {"retryable": False, "kind": "already-applied",
                    "retry_after_s": None}
        status = getattr(exc, "status", 200)
        if status == 429:
            return {"retryable": True, "kind": "rate_limited", "retry_after_s": None}
        if isinstance(status, int) and 500 <= status <= 599:
            return {"retryable": True, "kind": "server-error", "retry_after_s": None}
        if isinstance(status, int) and 400 <= status <= 499:
            return {"retryable": False, "kind": "client-error", "retry_after_s": None}
        return {"retryable": True, "kind": "unknown", "retry_after_s": None}
    return {"retryable": True, "kind": "unknown", "retry_after_s": None}


def load_sync_state(storage_dir: str = "data_store") -> dict[str, Any]:
    """{synced_levels: {signal_id: {stop_loss, tp}}, closed: [signal_ids]}."""
    path = _os.path.join(storage_dir, SYNC_STATE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
            if isinstance(data, dict):
                data.setdefault("synced_levels", {})
                data.setdefault("closed", [])
                return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"synced_levels": {}, "closed": []}


def save_sync_state(storage_dir: str, state: dict[str, Any]) -> None:
    """Atomic publish (tmp + os.replace): readers never see a torn file.

    A crash mid-write leaves the previous valid state in place; the tmp
    file is removed best-effort. Mirrors ExitDecisionLedger._save.
    """
    _os.makedirs(storage_dir, exist_ok=True)
    path = _os.path.join(storage_dir, SYNC_STATE_NAME)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(state, f, indent=2)
        _os.replace(tmp_path, path)
    except OSError:
        try:
            _os.unlink(tmp_path)
        except OSError:
            pass


def _plan_tp(plan: dict[str, Any]) -> Any:
    """The plan's single TP, tolerant of legacy tp1-keyed rows."""
    return plan.get("tp", plan.get("tp1"))


def _same_levels(stored: Any, cur: dict[str, Any]) -> bool:
    """Level equality across the tp/tp1 key rename (one corrective sync at
    most after the rename, then the stored row carries the new key)."""
    if not isinstance(stored, dict):
        return False
    return (stored.get("stop_loss") == cur.get("stop_loss")
            and stored.get("tp", stored.get("tp1")) == cur.get("tp"))


def compute_sync_actions(
    plans: list[dict[str, Any]],
    close_signal_ids: list[str],
    share_ledger: dict[str, Any],
    sync_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pure function: applied exit-ledger state -> Invo actions.

    plans: active ExitPlan dicts (only version > 0 can differ from create).
    close_signal_ids: signals with an applied supervisor-close event.
    share_ledger: signal_id -> {baseIds: [...]} from the share path.
    Returns [{kind: modify|close, signal_id, base_ids, price_target?, stop_loss?}].
    """
    actions: list[dict[str, Any]] = []
    synced_levels = sync_state.get("synced_levels", {}) or {}
    closed = set(sync_state.get("closed", []) or [])

    if not isinstance(share_ledger, dict):
        return actions
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        sid = plan.get("signal_id", "?")
        if sid in closed:
            continue  # reconciled as gone: no modifies, no closes
        try:
            version = int(plan.get("version", 0) or 0)
        except (TypeError, ValueError):
            continue
        if version <= 0:
            continue  # genesis == create-time levels; nothing new to send
        rec = share_ledger.get(sid) or {}
        if not isinstance(rec, dict):
            continue  # legacy/malformed share record: no safe target
        base_ids = [b for b in (rec.get("baseIds") or []) if b]
        if not base_ids:
            continue  # no live investment to target (test/manual shares)
        # TP + SL only by construction: the plan carries one tp.
        cur = {"stop_loss": plan.get("stop_loss"), "tp": _plan_tp(plan)}
        if not _same_levels(synced_levels.get(sid), cur):
            actions.append({
                "kind": "modify",
                "signal_id": sid,
                "base_ids": base_ids,
                "price_target": _plan_tp(plan),
                "stop_loss": plan.get("stop_loss"),
                "plan_version": version,
            })

    if isinstance(close_signal_ids, (list, tuple)):
        for sid in close_signal_ids:
            if sid in closed:
                continue
            rec = share_ledger.get(sid) or {}
            if not isinstance(rec, dict):
                continue
            base_ids = [b for b in (rec.get("baseIds") or []) if b]
            if not base_ids:
                continue
            actions.append({"kind": "close", "signal_id": sid, "base_ids": base_ids})

    return actions


def summarize_sync(plans: list[dict[str, Any]],
                   share_ledger: dict[str, Any],
                   sync_state: dict[str, Any]) -> dict[str, Any]:
    """Observability split for one cycle: what is in-sync, pending, or
    untargetable. A quiet cycle logs this INSTEAD of silence, so monitoring
    can distinguish 'nothing to do' from 'sync missing'.

    - in_sync: applied plan whose levels already match synced state.
    - unsynced: applied plan differing from synced state (will emit).
    - missing_base: applied plan with no baseIds (nothing to target).
    - closed_skipped: signals reconciled as gone.
    """
    synced_levels = sync_state.get("synced_levels", {}) or {}
    closed = set(sync_state.get("closed", []) or [])
    out = {"in_sync": [], "unsynced": [], "missing_base": [],
           "closed_skipped": []}
    if not isinstance(share_ledger, dict):
        return out
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        sid = plan.get("signal_id", "?")
        if sid in closed:
            out["closed_skipped"].append(sid)
            continue
        try:
            version = int(plan.get("version", 0) or 0)
        except (TypeError, ValueError):
            continue
        if version <= 0:
            continue
        rec = share_ledger.get(sid) or {}
        base_ids = [b for b in ((rec.get("baseIds") or [])
                                if isinstance(rec, dict) else []) if b]
        if not base_ids:
            out["missing_base"].append(sid)
            continue
        cur = {"stop_loss": plan.get("stop_loss"), "tp": _plan_tp(plan)}
        if _same_levels(synced_levels.get(sid), cur):
            out["in_sync"].append(sid)
        else:
            out["unsynced"].append(sid)
    return out


class SyncTracker:
    """Per-signal sync state machine over a shared state dict.

    Records live under state["records"][f"{sid}:{kind}"]:
      {status, attempts, plan_version, levels, next_retry_at_ms,
       last_error, updated_at_ms}
    Status vocabulary: pending | in_flight | retryable | confirmed |
    already-applied | already-gone | permanent_failure.

    The SAME dict should be handed to every tracker instance in a process
    (and reloaded from disk across restarts) so attempts/budgets survive.
    Persistence itself stays the caller's job via save_fn (default no-op);
    every mutation flushes and records persisted/audit_gap flags instead of
    raising — an audit/persistence failure must never corrupt accounting.
    """

    def __init__(self, state: dict[str, Any] | None = None,
                 now_ms_fn: Callable[[], int] | None = None,
                 cfg: SyncRetryConfig | None = None,
                 save_fn: Callable[[dict[str, Any]], None] | None = None):
        self.state = state if state is not None else {}
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault("records", {})
        self._now_ms_fn = now_ms_fn or (lambda: int(_time.time() * 1000))
        self.cfg = cfg or SyncRetryConfig()
        self._save_fn = save_fn or (lambda s: True)
        self.persisted = True
        self.audit_gap = False

    # ── internals ───────────────────────────────────────────────
    def _now(self) -> int:
        try:
            return int(self._now_ms_fn())
        except Exception:
            return 0

    def _flush(self) -> None:
        try:
            self._save_fn(self.state)
            self.persisted = True
        except Exception:
            self.persisted = False
            self.audit_gap = True

    def _rec(self, sid: str, kind: str) -> dict[str, Any]:
        recs = self.state.setdefault("records", {})
        key = f"{sid}:{kind}"
        rec = recs.get(key)
        if not isinstance(rec, dict):
            rec = {"status": "pending", "attempts": 0, "plan_version": None,
                   "levels": None, "next_retry_at_ms": 0, "last_error": "",
                   "updated_at_ms": 0}
            recs[key] = rec
        return rec

    # ── gating ──────────────────────────────────────────────────
    def gate(self, sid: str, kind: str,
             plan_version: Any = None,
             levels: Any = None,
             force_after_backoff: bool = False) -> dict[str, Any]:
        """Decide whether an action may execute now. Pure w.r.t. attempts
        (never mutates): execution bookkeeping belongs to note_attempt.

        Order is deliberate: staleness is the most informative block (an
        older plan must never silently replace newer confirmed state, and
        the operator should see WHY), convergence second, terminal third —
        but a NEWER plan version or DIFFERENT levels escape a terminal
        record, because that is new work, not a replay.
        """
        rec = self._rec(sid, kind)
        if (plan_version is not None and rec.get("plan_version") is not None
                and plan_version < rec["plan_version"]):
            return {"proceed": False, "reason": "stale-plan-version"}
        if (levels is not None and rec.get("status") in ("confirmed", "already-applied")
                and rec.get("levels") == levels):
            return {"proceed": False, "reason": "already-in-sync"}
        if rec.get("status") in TERMINAL_STATUSES:
            newer = (plan_version is not None and rec.get("plan_version") is not None
                     and plan_version > rec["plan_version"])
            changed = (levels is not None and rec.get("levels") is not None
                       and levels != rec["levels"])
            if not (newer or changed):
                return {"proceed": False,
                        "reason": f"terminal-{rec.get('status')}"}
        if rec.get("attempts", 0) >= self.cfg.max_attempts:
            return {"proceed": False, "reason": "budget-exhausted"}
        nxt = rec.get("next_retry_at_ms") or 0
        if nxt and self._now() < nxt and not force_after_backoff:
            return {"proceed": False, "reason": "backoff-pending"}
        return {"proceed": True, "reason": "due",
                "attempt": rec.get("attempts", 0) + 1,
                "idempotency_key": _action_id(sid, kind, plan_version, levels),
                "levels": levels}

    # ── bookkeeping ─────────────────────────────────────────────
    def note_attempt(self, sid: str, kind: str) -> dict[str, Any]:
        rec = self._rec(sid, kind)
        rec["attempts"] = int(rec.get("attempts", 0) or 0) + 1
        rec["status"] = "in_flight"
        rec["updated_at_ms"] = self._now()
        self._flush()
        return {"status": rec["status"], "attempts": rec["attempts"]}

    def note_result(self, sid: str, kind: str, outcome: str,
                    evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """outcome in confirmed|gone|retryable|permanent|already-applied."""
        ev = dict(evidence or {})
        rec = self._rec(sid, kind)
        mapping = {"confirmed": "confirmed", "gone": "already-gone",
                   "retryable": "retryable", "permanent": "permanent_failure",
                   "already-applied": "already-applied"}
        if outcome not in mapping:
            raise ValueError(f"unknown sync outcome: {outcome!r}")
        rec["status"] = mapping[outcome]
        if mapping[outcome] in ("confirmed", "already-applied"):
            # Success resets the attempt counter: the budget meters chronic
            # failure, not lifetime activity.
            rec["attempts"] = 0
        if "plan_version" in ev:
            rec["plan_version"] = ev["plan_version"]
        if "levels" in ev:
            rec["levels"] = ev["levels"]
        rec["last_error"] = str(ev.get("error", ""))
        if outcome == "retryable":
            # Server-provided Retry-After wins over computed backoff (it is
            # the authoritative demand), subject to the absolute ceiling so
            # a malformed giant value cannot park a position forever. The
            # attempt budget still bounds total retries either way.
            hint = ev.get("retry_after_s")
            if isinstance(hint, bool) or not isinstance(hint, (int, float)) or not (hint > 0):
                hint = None
            if hint is not None:
                delay_s = min(float(hint), float(SYNC_RETRY_AFTER_CEIL_S))
            else:
                delay_s = backoff_delay_s(rec.get("attempts", 1),
                                          self.cfg.backoff_base_s,
                                          self.cfg.backoff_cap_s,
                                          f"{sid}:{kind}")
            rec["next_retry_at_ms"] = self._now() + int(delay_s * 1000)
            rec["last_retry_after_s"] = hint
        else:
            rec["next_retry_at_ms"] = 0
        rec["updated_at_ms"] = self._now()
        self._flush()
        return {"status": rec["status"], "attempts": rec.get("attempts", 0)}

    def record(self, sid: str, kind: str) -> dict[str, Any]:
        """Report view: in_flight (crashed mid-attempt) reads as retryable."""
        rec = self._rec(sid, kind)
        status = rec.get("status", "pending")
        if status == "in_flight":
            status = "retryable"
        return {"status": status, "attempts": rec.get("attempts", 0),
                "plan_version": rec.get("plan_version"),
                "next_retry_at_ms": rec.get("next_retry_at_ms", 0)}

    def prune(self, now_ms: int | None = None,
              ttl_ms: int = TERMINAL_RECORD_TTL_MS) -> int:
        """Drop terminal records older than ttl_ms. Returns pruned count.

        Only final outcomes (confirmed / already-applied / already-gone /
        permanent_failure) older than the TTL are removed. Unresolved
        records are kept regardless of age — their staleness is itself
        signal. Flushes persistence; never raises.
        """
        if now_ms is None:
            now_ms = self._now()
        recs = self.state.get("records")
        if not isinstance(recs, dict):
            return 0
        doomed = [k for k, r in recs.items()
                  if isinstance(r, dict)
                  and r.get("status") in TERMINAL_STATUSES
                  and isinstance(r.get("updated_at_ms"), (int, float))
                  and now_ms - r["updated_at_ms"] > ttl_ms]
        for k in doomed:
            del recs[k]
        if doomed:
            self._flush()
        return len(doomed)
