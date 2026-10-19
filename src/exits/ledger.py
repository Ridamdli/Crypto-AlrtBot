"""ExitDecisionLedger — auditable, restart-safe persistence.

Stored as data_store/exit_decisions.json (same JSON-ledger convention as
outcomes.json / paper_portfolio.json / invo_shares.json):

{
  "version": 1,
  "decisions": [SupervisorDecision.to_dict(), ...],   # capped, newest last
  "plans":     {signal_id: ExitPlan.to_dict()},       # active plan per signal
  "events":    {signal_id: [overlay events]},         # reduce/close overlays
  "invalidated": [signal_ids],                        # pending setups killed
  "last_eval": {signal_id: {"data_ts": ms, "at": ms}} # freshness/monotonicity
}

Writes are atomic (tmp file + os.replace). The ledger is reloaded from disk
on construction, so a process restart loses nothing but in-memory caches.
If the file is corrupt or unwritable, the ledger degrades to an empty
in-memory store and reports unhealthy — the monitor then refuses to apply
changes (fail safe) while still allowing record-only decisions.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

from src.exits.models import ExitPlan, SupervisorDecision
from src.utils.logger import get_logger

logger = get_logger(__name__)

LEDGER_VERSION = 1


class ExitDecisionLedger:
    def __init__(self, storage_dir: str = "data_store",
                 ledger_name: str = "exit_decisions.json",
                 max_decisions: int = 2000):
        self.storage_dir = storage_dir
        self.ledger_path = os.path.join(storage_dir, ledger_name)
        self.tmp_path = self.ledger_path + ".tmp"
        self.max_decisions = max_decisions
        self.healthy = True
        self._state = self._load()

    # ── load / save ───────────────────────────────────────────────
    def _blank(self) -> dict[str, Any]:
        return {"version": LEDGER_VERSION, "decisions": [], "plans": {},
                "events": {}, "invalidated": [], "last_eval": {},
                # Durable idempotency keys, independent of the capped
                # decisions audit list (see record()): evicting an old
                # decision must never resurrect it as "unseen".
                "seen_keys": []}

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("version") != LEDGER_VERSION:
                logger.warning("[Exits] Ledger version mismatch/empty — starting blank.")
                return self._blank()
            for k in ("decisions", "plans", "events", "invalidated", "last_eval"):
                if k not in data or not isinstance(data[k], (list, dict)):
                    data[k] = [] if k in ("decisions", "invalidated") else {}
            # seen_keys is additive-only: legacy files predate it, so
            # backfill from the loaded decisions once (no rewrite needed —
            # the union is persisted on the next record()).
            if not isinstance(data.get("seen_keys"), list):
                data["seen_keys"] = []
            for d in data["decisions"]:
                if isinstance(d, dict) and d.get("decision_key"):
                    if d["decision_key"] not in data["seen_keys"]:
                        data["seen_keys"].append(d["decision_key"])
            return data
        except FileNotFoundError:
            return self._blank()
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[Exits] Ledger unreadable ({e}) — in-memory only, apply disabled.")
            self.healthy = False
            return self._blank()

    def _save(self) -> bool:
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            with open(self.tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(self.tmp_path, self.ledger_path)
            return True
        except OSError as e:
            logger.error(f"[Exits] Ledger write failed ({e}) — in-memory only, apply disabled.")
            self.healthy = False
            return False

    # ── queries ───────────────────────────────────────────────────
    def has_key(self, decision_key: str) -> bool:
        if decision_key in self._state.get("seen_keys", []):
            return True
        return any(d.get("decision_key") == decision_key
                   for d in self._state["decisions"])

    def seen_keys(self) -> frozenset:
        keys = set(self._state.get("seen_keys", []) or [])
        keys.update(d.get("decision_key", "")
                    for d in self._state["decisions"] if d.get("decision_key"))
        keys.discard("")
        return frozenset(keys)

    def active_plan(self, signal: dict[str, Any]) -> ExitPlan:
        rec = self._state["plans"].get(signal["signal_id"])
        if rec:
            try:
                return ExitPlan.from_dict(rec)
            except (TypeError, ValueError, KeyError):
                pass
        return ExitPlan.genesis(signal)

    def is_invalidated(self, signal_id: str) -> bool:
        return signal_id in self._state["invalidated"]

    def last_eval(self, signal_id: str) -> dict[str, int]:
        rec = self._state["last_eval"].get(signal_id, {})
        return {"data_ts": int(rec.get("data_ts", 0)), "at": int(rec.get("at", 0))}

    def decisions_for(self, signal_id: str) -> list[dict[str, Any]]:
        return [d for d in self._state["decisions"] if d.get("signal_id") == signal_id]

    def active_plans(self) -> list[dict[str, Any]]:
        """All stored (applied) exit plans as plain dicts, for sync readers."""
        return [dict(p) for p in self._state["plans"].values()
                if isinstance(p, dict) and p.get("signal_id")]

    def close_signal_ids(self) -> list[str]:
        """Signals with an applied supervisor-close event."""
        out = []
        for sid, evs in (self._state.get("events") or {}).items():
            if any(isinstance(e, dict) and e.get("kind") == "close" for e in evs or []):
                out.append(sid)
        return out

    # ── record ────────────────────────────────────────────────────
    def record(self, decision: SupervisorDecision, apply: bool) -> bool:
        """Append the decision; optionally apply plan/events/invalidation.

        apply=False (monitor-only): the decision is recorded for audit, the
        active plan is untouched. apply=True additionally stores plan_after,
        overlay events, and invalidation flags — all still paper-side only.
        Either way the row carries `applied` and (only when applied) an
        `applied_at` ISO timestamp, so advisory vs applied is auditable.
        Returns True if the ledger write succeeded.
        """
        decision.applied = apply
        decision.applied_at = datetime.now(timezone.utc).isoformat() if apply else None
        self._state["decisions"].append(decision.to_dict())
        self._state["decisions"] = self._state["decisions"][-self.max_decisions:]
        # Idempotency outlives the capped audit list: retain every recorded
        # key (applied or not — monitor suppression and guard duplicate
        # checks consult the union, exactly as before eviction).
        if decision.decision_key and decision.decision_key not in self._state.get("seen_keys", []):
            self._state.setdefault("seen_keys", []).append(decision.decision_key)
        if apply:
            if decision.plan_after:
                self._state["plans"][decision.signal_id] = decision.plan_after
            for ev in decision.events_applied:
                self._state["events"].setdefault(decision.signal_id, []).append({
                    **ev, "decision_key": decision.decision_key,
                    "data_ts_ms": decision.data_ts_ms,
                    "decided_at_ms": decision.decided_at_ms,
                })
            if decision.action == "INVALIDATE_PENDING_SETUP":
                if decision.signal_id not in self._state["invalidated"]:
                    self._state["invalidated"].append(decision.signal_id)
        self._state["last_eval"][decision.signal_id] = {
            "data_ts": decision.data_ts_ms, "at": decision.decided_at_ms}
        return self._save()

    def mark_evaluated(self, signal_id: str, data_ts_ms: int, at_ms: int) -> None:
        self._state["last_eval"][signal_id] = {"data_ts": data_ts_ms, "at": at_ms}
        self._save()

    def outcome_link(self, signal_id: str, outcome: dict[str, Any]) -> None:
        """Attach the eventual trade outcome to past decisions (audit trail)."""
        for d in self._state["decisions"]:
            if d.get("signal_id") == signal_id:
                d["linked_outcome"] = {
                    "status": outcome.get("status"),
                    "net_realized_r": outcome.get("net_realized_r"),
                    "final": bool(outcome.get("final")),
                }
        self._save()

    def get_events(self, signal_id: str) -> list[dict[str, Any]]:
        return list(self._state["events"].get(signal_id, []))

    def summary(self) -> dict[str, Any]:
        return {"healthy": self.healthy, "path": self.ledger_path,
                "decisions": len(self._state["decisions"]),
                "plans": len(self._state["plans"]),
                "invalidated": len(self._state["invalidated"])}
