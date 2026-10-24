"""ExitDecisionLedger — auditable, restart-safe persistence.

Two backends, chosen at runtime by `is_pg_enabled()`:

JSON (DATABASE_URL unset) — data_store/exit_decisions.json, same convention as
outcomes.json / paper_portfolio.json / invo_shares.json:

{
  "version": 1,
  "decisions": [SupervisorDecision.to_dict(), ...],   # capped, newest last
  "plans":     {signal_id: ExitPlan.to_dict()},       # active plan per signal
  "events":    {signal_id: [overlay events]},         # reduce/close overlays
  "invalidated": [signal_ids],                        # pending setups killed
  "last_eval": {signal_id: {"data_ts": ms, "at": ms}} # freshness/monotonicity
}

Postgres (DATABASE_URL set) — the same six structures as rows in
exit_decisions / exit_plans / exit_events / invalidated_signals /
exit_last_eval / exit_seen_keys. record() writes them in ONE transaction, so
a failed write leaves no partial decision behind.

JSON writes are atomic (tmp file + os.replace). The ledger is reloaded from
disk on construction, so a process restart loses nothing but in-memory caches.
If the file is corrupt or unwritable, the ledger degrades to an empty
in-memory store and reports unhealthy — the monitor then refuses to apply
changes (fail safe) while still allowing record-only decisions. The Postgres
path reports unhealthy the same way when the database is unreachable.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from src.db import get_session_factory, is_pg_enabled
from src.db import models as db
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
        self._pg = is_pg_enabled()
        if self._pg:
            self._state = self._blank()
            self._pg_probe()
        else:
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

    # ── postgres helpers ──────────────────────────────────────────
    def _pg_session(self):
        return get_session_factory()()

    def _pg_probe(self) -> None:
        """Fail safe at construction, exactly like an unreadable JSON file."""
        try:
            with self._pg_session() as s:
                s.execute(text("SELECT 1"))
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"[Exits] Postgres unavailable ({e.__class__.__name__}) — "
                         "in-memory only, apply disabled.")
            self.healthy = False

    def _pg_failed(self, action: str, exc: Exception) -> None:
        logger.error(f"[Exits] Ledger {action} failed ({exc.__class__.__name__}: {exc}) — "
                     "apply disabled.")
        self.healthy = False

    # ── queries ───────────────────────────────────────────────────
    def has_key(self, decision_key: str) -> bool:
        if self._pg:
            try:
                with self._pg_session() as s:
                    if s.get(db.ExitSeenKey, decision_key) is not None:
                        return True
                    return s.execute(
                        select(db.ExitDecision.decision_key)
                        .where(db.ExitDecision.decision_key == decision_key)
                    ).first() is not None
            except SQLAlchemyError as e:
                self._pg_failed("has_key", e)
                return False
        if decision_key in self._state.get("seen_keys", []):
            return True
        return any(d.get("decision_key") == decision_key
                   for d in self._state["decisions"])

    def seen_keys(self) -> frozenset:
        if self._pg:
            try:
                with self._pg_session() as s:
                    keys = {r[0] for r in s.execute(select(db.ExitSeenKey.decision_key))}
                    keys.update(r[0] for r in s.execute(
                        select(db.ExitDecision.decision_key)))
            except SQLAlchemyError as e:
                self._pg_failed("seen_keys", e)
                keys = set()
            keys.discard("")
            keys.discard(None)
            return frozenset(keys)
        keys = set(self._state.get("seen_keys", []) or [])
        keys.update(d.get("decision_key", "")
                    for d in self._state["decisions"] if d.get("decision_key"))
        keys.discard("")
        return frozenset(keys)

    def active_plan(self, signal: dict[str, Any]) -> ExitPlan:
        if self._pg:
            try:
                with self._pg_session() as s:
                    row = s.get(db.ExitPlan, signal["signal_id"])
            except SQLAlchemyError as e:
                self._pg_failed("active_plan", e)
                row = None
            if row is not None and isinstance(row.plan, dict):
                try:
                    return ExitPlan.from_dict(row.plan)
                except (TypeError, ValueError, KeyError):
                    pass
            return ExitPlan.genesis(signal)
        rec = self._state["plans"].get(signal["signal_id"])
        if rec:
            try:
                return ExitPlan.from_dict(rec)
            except (TypeError, ValueError, KeyError):
                pass
        return ExitPlan.genesis(signal)

    def is_invalidated(self, signal_id: str) -> bool:
        if self._pg:
            try:
                with self._pg_session() as s:
                    return s.get(db.InvalidatedSignal, signal_id) is not None
            except SQLAlchemyError as e:
                self._pg_failed("is_invalidated", e)
                return False
        return signal_id in self._state["invalidated"]

    def last_eval(self, signal_id: str) -> dict[str, int]:
        if self._pg:
            try:
                with self._pg_session() as s:
                    row = s.get(db.ExitLastEval, signal_id)
            except SQLAlchemyError as e:
                self._pg_failed("last_eval", e)
                row = None
            return {"data_ts": int((row.data_ts if row else 0) or 0),
                    "at": int((row.at_ms if row else 0) or 0)}
        rec = self._state["last_eval"].get(signal_id, {})
        return {"data_ts": int(rec.get("data_ts", 0)), "at": int(rec.get("at", 0))}

    def decisions_for(self, signal_id: str) -> list[dict[str, Any]]:
        if self._pg:
            try:
                with self._pg_session() as s:
                    rows = s.execute(
                        select(db.ExitDecision.payload, db.ExitDecision.linked_outcome)
                        .where(db.ExitDecision.signal_id == signal_id)
                        .order_by(db.ExitDecision.id))
                    # payload is the row as recorded; outcome_link() is the one
                    # post-insert mutation, so overlay it like the JSON path's
                    # in-place dict update does.
                    return [{**p, "linked_outcome": lo} if lo is not None else dict(p)
                            for p, lo in rows]
            except SQLAlchemyError as e:
                self._pg_failed("decisions_for", e)
                return []
        return [d for d in self._state["decisions"] if d.get("signal_id") == signal_id]

    def active_plans(self) -> list[dict[str, Any]]:
        """All stored (applied) exit plans as plain dicts, for sync readers."""
        if self._pg:
            try:
                with self._pg_session() as s:
                    rows = [r[0] for r in s.execute(select(db.ExitPlan.plan))]
            except SQLAlchemyError as e:
                self._pg_failed("active_plans", e)
                return []
            return [dict(p) for p in rows
                    if isinstance(p, dict) and p.get("signal_id")]
        return [dict(p) for p in self._state["plans"].values()
                if isinstance(p, dict) and p.get("signal_id")]

    def close_signal_ids(self) -> list[str]:
        """Signals with an applied supervisor-close event."""
        if self._pg:
            try:
                with self._pg_session() as s:
                    return [r[0] for r in s.execute(
                        select(db.ExitEvent.signal_id)
                        .where(db.ExitEvent.kind == "close").distinct())]
            except SQLAlchemyError as e:
                self._pg_failed("close_signal_ids", e)
                return []
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
        if self._pg:
            return self._record_pg(decision, apply)
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

    def _record_pg(self, decision: SupervisorDecision, apply: bool) -> bool:
        """One transaction: decision + seen key + plan + events + invalidation
        + last_eval, then the 2,000-row cap. Any failure rolls back all of it."""
        payload = decision.to_dict()
        plan = decision.plan_after if apply else None
        try:
            with self._pg_session() as s, s.begin():
                s.add(db.ExitDecision(
                    decision_key=decision.decision_key,
                    signal_id=decision.signal_id,
                    action=decision.action,
                    data_ts_ms=decision.data_ts_ms,
                    decided_at_ms=decision.decided_at_ms,
                    code_version=decision.code_version,
                    config_version=decision.config_version,
                    rationale=decision.rationale,
                    assessments=payload.get("assessments"),
                    risk_guard=payload.get("risk_guard"),
                    rejected=payload.get("rejected"),
                    events_applied=payload.get("events_applied"),
                    plan_after=payload.get("plan_after"),
                    payload=payload,
                ))
                if (decision.decision_key
                        and s.get(db.ExitSeenKey, decision.decision_key) is None):
                    s.add(db.ExitSeenKey(
                        decision_key=decision.decision_key,
                        first_seen_at=datetime.now(timezone.utc)))
                if plan:
                    s.merge(db.ExitPlan(
                        signal_id=decision.signal_id,
                        basis_decision_key=plan.get("basis_decision_key"),
                        decided_at_ms=plan.get("decided_at_ms"),
                        version=plan.get("version"),
                        plan=plan,
                    ))
                if apply:
                    for ev in decision.events_applied:
                        s.add(db.ExitEvent(
                            signal_id=decision.signal_id,
                            kind=ev.get("kind"),
                            decision_key=decision.decision_key,
                            data_ts_ms=decision.data_ts_ms,
                            decided_at_ms=decision.decided_at_ms,
                            payload={**ev, "decision_key": decision.decision_key,
                                     "data_ts_ms": decision.data_ts_ms,
                                     "decided_at_ms": decision.decided_at_ms},
                        ))
                    if (decision.action == "INVALIDATE_PENDING_SETUP"
                            and s.get(db.InvalidatedSignal, decision.signal_id) is None):
                        s.add(db.InvalidatedSignal(signal_id=decision.signal_id))
                s.merge(db.ExitLastEval(
                    signal_id=decision.signal_id,
                    data_ts=decision.data_ts_ms,
                    at_ms=decision.decided_at_ms,
                ))
                # Same 2,000-row bound the JSON list applies on every write.
                s.flush()
                s.execute(
                    delete(db.ExitDecision).where(
                        ~db.ExitDecision.id.in_(
                            select(db.ExitDecision.id)
                            .order_by(db.ExitDecision.id.desc())
                            .limit(self.max_decisions)
                        )
                    )
                )
            return True
        except SQLAlchemyError as e:
            self._pg_failed("record", e)
            return False

    def mark_evaluated(self, signal_id: str, data_ts_ms: int, at_ms: int) -> None:
        if self._pg:
            try:
                with self._pg_session() as s, s.begin():
                    s.merge(db.ExitLastEval(signal_id=signal_id,
                                            data_ts=data_ts_ms, at_ms=at_ms))
            except SQLAlchemyError as e:
                self._pg_failed("mark_evaluated", e)
            return
        self._state["last_eval"][signal_id] = {"data_ts": data_ts_ms, "at": at_ms}
        self._save()

    def outcome_link(self, signal_id: str, outcome: dict[str, Any]) -> None:
        """Attach the eventual trade outcome to past decisions (audit trail)."""
        if self._pg:
            try:
                with self._pg_session() as s, s.begin():
                    s.execute(
                        update(db.ExitDecision)
                        .where(db.ExitDecision.signal_id == signal_id)
                        .values(linked_outcome={
                            "status": outcome.get("status"),
                            "net_realized_r": outcome.get("net_realized_r"),
                            "final": bool(outcome.get("final")),
                        })
                    )
            except SQLAlchemyError as e:
                self._pg_failed("outcome_link", e)
            return
        for d in self._state["decisions"]:
            if d.get("signal_id") == signal_id:
                d["linked_outcome"] = {
                    "status": outcome.get("status"),
                    "net_realized_r": outcome.get("net_realized_r"),
                    "final": bool(outcome.get("final")),
                }
        self._save()

    def get_events(self, signal_id: str) -> list[dict[str, Any]]:
        if self._pg:
            try:
                with self._pg_session() as s:
                    return [dict(r[0]) for r in s.execute(
                        select(db.ExitEvent.payload)
                        .where(db.ExitEvent.signal_id == signal_id)
                        .order_by(db.ExitEvent.id))]
            except SQLAlchemyError as e:
                self._pg_failed("get_events", e)
                return []
        return list(self._state["events"].get(signal_id, []))

    def summary(self) -> dict[str, Any]:
        if self._pg:
            counts = {"decisions": 0, "plans": 0, "invalidated": 0}
            try:
                with self._pg_session() as s:
                    for name, model in (("decisions", db.ExitDecision),
                                        ("plans", db.ExitPlan),
                                        ("invalidated", db.InvalidatedSignal)):
                        counts[name] = int(s.execute(
                            select(func.count()).select_from(model)).scalar_one())
            except SQLAlchemyError as e:
                self._pg_failed("summary", e)
            return {"healthy": self.healthy, "path": "postgres", **counts}
        return {"healthy": self.healthy, "path": self.ledger_path,
                "decisions": len(self._state["decisions"]),
                "plans": len(self._state["plans"]),
                "invalidated": len(self._state["invalidated"])}
