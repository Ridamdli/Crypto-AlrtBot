"""ExitSupervisor — one final proposed action from many assessments.

Decision doctrine (explicit, ordered; NOT majority, NOT highest-confidence,
NOT most-recent, NOT the setup generator):

1. DATA FIRST: if the evaluation basis is stale, thin, or missing, the only
   honest output is INSUFFICIENT_DATA and the plan is retained. No worker,
   however confident, overrides bad data.
2. SAFETY SECOND: genuine structural invalidation (completed close beyond the
   active stop, or a fairly-elapsed entry window on a pending setup) ends
   management: INVALIDATE_PENDING_SETUP for PENDING, CLOSE_PAPER_POSITION
   for OPEN/PARTIAL. This is the one case where a single worker's evidence
   is sufficient — because the evidence is the market printing through the
   plan's own invalidation level, not an opinion.
3. OPPOSITION ALONE NEVER EXITS: a counter-signal without invalidation keeps
   the plan. Genuine reversal evidence (break + deterioration) that the
   Structure worker contradicts (shared-indicator overlap or levels intact)
   becomes FLAG_CONFLICT with the plan retained — the disagreement itself is
   the finding, recorded for review.
4. ADJUSTMENTS LAST: TIGHTEN_STOP / ADJUST_TAKE_PROFITS / REDUCE_PAPER_POSITION
   apply only when (a) proposed by the owning worker, (b) the position state
   admits the action, (c) the stability gate passes (material move + cooldown
   elapsed), (d) RiskGuard validates, and (e) no higher-priority rule fired.
   State admission: TIGHTEN/ADJUST/REDUCE/CLOSE need OPEN-or-PARTIAL;
   INVALIDATE needs PENDING; REDUCE additionally prefers PARTIAL but admits
   OPEN (documented). CLOSE/INVALIDATE bypass the stability gate entirely —
   stability must never delay a safety exit.
5. DEFAULT: NO_CHANGE. The subsystem must be able to conclude that no
   adjustment is justified — and say why the alternatives were rejected.

Every decision carries `rationale` (why this action) and `rejected`
(why each materially different alternative was NOT taken).
"""
import hashlib
import json
from typing import Any, Dict, List, Optional

from src.exits import policy
from src.exits.config import ExitConfig, config_version
from src.exits.models import (
    EXITS_CODE_VERSION,
    ExitAction,
    ExitPlan,
    PositionState,
    PositionView,
    SupervisorDecision,
    WorkerAssessment,
)
from src.exits.risk_guard import RiskGuard
from src.exits.workers import WorkerCtx


def decision_key(
    signal_id: str, plan_version: int, data_ts_ms: int, action: str, params: Dict[str, Any]
) -> str:
    """Idempotency key: identical inputs always collide (dedupe), distinct
    evaluations never do."""
    raw = json.dumps(
        {
            "signal_id": signal_id,
            "plan_version": plan_version,
            "data_ts_ms": data_ts_ms,
            "action": action,
            "params": params,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ExitSupervisor:
    SUPERVISOR_VERSION = "1.0.0"

    def __init__(self, cfg: Optional[ExitConfig] = None):
        self.cfg = cfg or ExitConfig()
        self.guard = RiskGuard(self.cfg)

    # ── main entry ──────────────────────────────────────────────────
    def decide(
        self,
        view: PositionView,
        assessments: List[WorkerAssessment],
        ctx: WorkerCtx,
        now_ms: int,
        seen_keys: frozenset = frozenset(),
        last_data_ts_ms: int = 0,
        last_change_ts_ms: int = 0,
    ) -> SupervisorDecision:
        by_id = {a.worker_id: a for a in assessments}
        struct = by_id.get("structure")
        target = by_id.get("target")
        stop = by_id.get("stop")
        counter = by_id.get("counter")
        rr = by_id.get("riskreward")

        # Rule 1 — data first.
        if not ctx.fresh:
            return self._finish(
                view, assessments, ctx, now_ms,
                ExitAction.INSUFFICIENT_DATA.value,
                f"Retaining plan: market data unusable ({ctx.freshness_reason}). "
                "No worker verdict is acted on without a fresh basis.",
                rejected=[
                    {"alternative": "any adjustment", "reason": "no fresh evidence to justify a change"},
                ],
            )

        invalidated = self._invalidation_evidence(struct)
        # Rule 2 — safety second.
        if invalidated is not None:
            if view.state == PositionState.PENDING:
                return self._finish(
                    view, assessments, ctx, now_ms,
                    ExitAction.INVALIDATE_PENDING_SETUP.value,
                    f"Invalidating pending setup: {invalidated}. "
                    "A setup whose invalidation level printed (or whose entry window "
                    "elapsed fairly) has no thesis left to manage.",
                    rejected=[
                        {"alternative": "NO_CHANGE", "reason": "would keep managing a dead setup"},
                        {"alternative": "any TP/SL tweak", "reason": "tuning exits on an invalid thesis is noise"},
                    ],
                )
            if view.state in (PositionState.OPEN, PositionState.PARTIAL):
                return self._finish(
                    view, assessments, ctx, now_ms,
                    ExitAction.CLOSE_PAPER_POSITION.value,
                    f"Closing paper position: {invalidated}. The market printed "
                    "through the plan's own invalidation level on completed candles.",
                    rejected=[
                        {"alternative": "NO_CHANGE", "reason": "would hold exposure whose stop premise failed"},
                        {"alternative": "TIGHTEN_STOP", "reason": "a tighter stop cannot repair a broken thesis"},
                    ],
                    events=[{"kind": "close", "basis": "invalidation"}],
                )

        # Rule 3 — opposition alone never exits.
        reversal = counter is not None and counter.verdict == "GENUINE_REVERSAL"
        if reversal:
            struct_intact = struct is not None and struct.verdict == "THESIS_INTACT"
            overlap = self._shared_indicators(counter, struct)
            if struct_intact or overlap:
                return self._finish(
                    view, assessments, ctx, now_ms,
                    ExitAction.FLAG_CONFLICT.value,
                    "Flagging conflict: counter-signal reversal evidence exists, but the "
                    f"structure worker holds the thesis intact (shared indicators: {overlap or 'none declared'}). "
                    "Opposition without invalidation never closes a position; the "
                    "disagreement is recorded for review and the plan is retained.",
                    rejected=[
                        {"alternative": "CLOSE_PAPER_POSITION", "reason": "no invalidation printed; closing on opinion alone is prohibited"},
                        {"alternative": "NO_CHANGE (silent)", "reason": "the disagreement itself is material and must be flagged"},
                    ],
                )
            # Reversal evidence stands uncontradicted: adopt the counter worker's
            # own (conservative) proposal — FLAG, or CLOSE only if it showed damage.
            if counter.proposed_action == ExitAction.CLOSE_PAPER_POSITION.value and view.state in (
                PositionState.OPEN, PositionState.PARTIAL,
            ):
                return self._finish(
                    view, assessments, ctx, now_ms,
                    ExitAction.CLOSE_PAPER_POSITION.value,
                    "Closing paper position: uncontradicted reversal evidence with "
                    "material damage. No worker defends the thesis.",
                    rejected=[
                        {"alternative": "NO_CHANGE", "reason": "every assessment agrees the thesis failed"},
                    ],
                    events=[{"kind": "close", "basis": "uncontradicted reversal"}],
                )
            return self._finish(
                view, assessments, ctx, now_ms,
                ExitAction.FLAG_CONFLICT.value,
                "Flagging conflict: uncontradicted reversal evidence below the damage "
                "threshold. Plan retained; conflict recorded.",
                rejected=[
                    {"alternative": "CLOSE_PAPER_POSITION", "reason": "damage below 0.75R; closing would be prediction, not management"},
                ],
            )

        # Rule 4 — adjustments last, each gated on state + stability + RiskGuard.
        skipped: List[Dict[str, str]] = []
        for worker, action in (
            (stop, ExitAction.TIGHTEN_STOP.value),
            (target, ExitAction.ADJUST_TAKE_PROFITS.value),
            (rr, ExitAction.REDUCE_PAPER_POSITION.value),
        ):
            if worker is None or worker.proposed_action != action:
                continue
            if not self._state_admits(view.state, action):
                continue
            proposal = dict(worker.proposal or {})
            stable, why = self._stability_check(view, ctx, now_ms, action,
                                                proposal, last_change_ts_ms)
            if not stable:
                skipped.append({"alternative": action, "reason": why})
                continue
            params_for_key = {k: v for k, v in proposal.items() if not k.startswith("_")}
            key = decision_key(view.signal_id, view.plan.version,
                               ctx.data_ts_ms, action, params_for_key)
            proposal["_decision_key"] = key
            ok, reasons = self.guard.validate(
                view, action, proposal,
                seen_keys=seen_keys, last_data_ts_ms=last_data_ts_ms,
                data_ts_ms=ctx.data_ts_ms,
            )
            if ok:
                return self._apply_adjustment(view, assessments, ctx, now_ms, worker, action, proposal, reasons)
            # Proposed but rejected by RiskGuard: record loudly, keep plan.
            d = self._finish(
                view, assessments, ctx, now_ms,
                ExitAction.NO_CHANGE.value,
                f"Retaining plan: {worker.worker_id} proposed {action} but RiskGuard "
                f"rejected it ({'; '.join(reasons)}). A rejected adjustment is "
                "information, not an action.",
                rejected=[
                    {"alternative": action, "reason": "RiskGuard: " + "; ".join(reasons)},
                ],
                params=proposal,
            )
            d.risk_guard = {"ok": False, "reasons": reasons, "action": action}
            return d

        # Rule 5 — default: no change, with explicit rejections.
        rejected = self._no_change_rejections(view, by_id)
        if skipped:
            rationale = (
                self._no_change_rationale(view, by_id)
                + " Stability gate held back: "
                + "; ".join(f"{s['alternative']} ({s['reason']})" for s in skipped)
                + "."
            )
            rejected = skipped + rejected
        else:
            rationale = self._no_change_rationale(view, by_id)
        return self._finish(
            view, assessments, ctx, now_ms,
            ExitAction.NO_CHANGE.value,
            rationale,
            rejected=rejected,
        )

    # ── helpers ─────────────────────────────────────────────────────
    def _stability_check(
        self,
        view: PositionView,
        ctx: WorkerCtx,
        now_ms: int,
        action: str,
        proposal: Dict[str, Any],
        last_change_ts_ms: int,
    ) -> "tuple[bool, str]":
        """Plan-stability gate: material move + cooldown elapsed.

        Returns (True, reason-to-proceed) or (False, why-held-back).
        Applies to TIGHTEN_STOP / ADJUST_TAKE_PROFITS / REDUCE_PAPER_POSITION
        only — CLOSE and INVALIDATE bypass via earlier rules and never reach
        here. REDUCE needs no ATR test (fraction already bounded); it is
        gated on cooldown alone, which is what stopped the every-bar
        halve-again pathology.
        """
        cfg = self.cfg
        if action in (ExitAction.TIGHTEN_STOP.value,
                      ExitAction.ADJUST_TAKE_PROFITS.value,
                      ExitAction.REDUCE_PAPER_POSITION.value):
            # Any applied intervention starts the clock — including REDUCE,
            # which leaves plan levels (and versions) untouched. Gating on
            # plan version alone would exempt all event-only repeats.
            if last_change_ts_ms:
                elapsed_s = (now_ms - last_change_ts_ms) / 1000.0
                if elapsed_s < cfg.min_adjust_interval_s:
                    return False, (
                        f"cooling down (last intervention {elapsed_s:.0f}s ago "
                        f"< {cfg.min_adjust_interval_s}s)")
        if action in (ExitAction.TIGHTEN_STOP.value,
                      ExitAction.ADJUST_TAKE_PROFITS.value):
            atr = policy.atr_wilder(ctx.completed, cfg.atr_period)
            if atr is None or atr <= 0:
                return False, "cannot verify materiality without ATR"
            if action == ExitAction.TIGHTEN_STOP.value:
                try:
                    move = abs(float(proposal["stop_loss"]) - view.plan.stop_loss)
                except (TypeError, ValueError, KeyError):
                    return False, "proposal.stop_loss missing/non-numeric"
                floor = cfg.min_stop_move_atr * atr
                if move < floor:
                    return False, (
                        f"immaterial stop move {move:.6g} < {floor:.6g} "
                        f"({cfg.min_stop_move_atr} ATR)")
                return True, f"stop move {move:.6g} clears {floor:.6g}"
            # ADJUST: measure what would actually change after the fill-aware
            # merge (an already-hit TP is history, not a change).
            merged = self._merged_tp(view, ctx, proposal)
            move = abs(merged - view.plan.tp)
            floor = cfg.min_tp_move_atr * atr
            if move < floor:
                return False, (
                    f"immaterial TP move {move:.6g} < {floor:.6g} "
                    f"({cfg.min_tp_move_atr} ATR)")
            return True, f"TP move {move:.6g} clears {floor:.6g}"
        return True, "cooldown elapsed (reduce gated on cadence only)"

    @staticmethod
    def _merged_tp(view: PositionView, ctx: WorkerCtx,
                   proposal: Dict[str, Any]) -> float:
        """Fill-aware TP merge shared by the stability gate and application.

        An already-hit TP keeps its traded level; otherwise the proposal
        value applies.
        """
        plan = view.plan
        if bool((ctx.outcome or {}).get("tp1_hit")):
            return plan.tp
        return float(proposal["tp"])

    @staticmethod
    def _invalidation_evidence(struct: Optional[WorkerAssessment]) -> Optional[str]:
        if struct is None or struct.verdict != "THESIS_INVALIDATED":
            return None
        ev = struct.evidence or {}
        if ev.get("stop_breached"):
            return (
                f"completed close {ev.get('last_close')} printed beyond active "
                f"stop {ev.get('active_stop')}"
            )
        if ev.get("entry_window_elapsed"):
            return "entry window elapsed with sufficient observed bars and no trigger"
        return "thesis invalidated"

    @staticmethod
    def _shared_indicators(
        counter: Optional[WorkerAssessment], struct: Optional[WorkerAssessment]
    ) -> List[str]:
        if counter is None or struct is None:
            return []
        return sorted(set(counter.indicators or []) & set(struct.indicators or []))

    @staticmethod
    def _state_admits(state: PositionState, action: str) -> bool:
        if action in (
            ExitAction.TIGHTEN_STOP.value,
            ExitAction.ADJUST_TAKE_PROFITS.value,
            ExitAction.REDUCE_PAPER_POSITION.value,
            ExitAction.CLOSE_PAPER_POSITION.value,
        ):
            return state in (PositionState.OPEN, PositionState.PARTIAL)
        if action == ExitAction.INVALIDATE_PENDING_SETUP.value:
            return state == PositionState.PENDING
        return True

    def _apply_adjustment(
        self,
        view: PositionView,
        assessments: List[WorkerAssessment],
        ctx: WorkerCtx,
        now_ms: int,
        worker: WorkerAssessment,
        action: str,
        proposal: Dict[str, Any],
        guard_reasons: List[str],
    ) -> SupervisorDecision:
        plan = view.plan
        nxt = plan.version + 1
        if action == ExitAction.TIGHTEN_STOP.value:
            new_plan = ExitPlan(view.signal_id, nxt, float(proposal["stop_loss"]),
                                plan.tp, now_ms, "")
        elif action == ExitAction.ADJUST_TAKE_PROFITS.value:
            # Merge, never rewrite history (see _merged_tp).
            m = self._merged_tp(view, ctx, proposal)
            new_plan = ExitPlan(view.signal_id, nxt, plan.stop_loss,
                                m, now_ms, "")
        else:  # REDUCE_PAPER_POSITION — plan levels unchanged; exposure event recorded
            new_plan = ExitPlan(view.signal_id, nxt, plan.stop_loss,
                                plan.tp, now_ms, "")
        key = decision_key(view.signal_id, plan.version, ctx.data_ts_ms, action, proposal)
        new_plan = ExitPlan(new_plan.signal_id, new_plan.version, new_plan.stop_loss,
                            new_plan.tp,
                            new_plan.decided_at_ms, key)
        events: List[Dict[str, Any]] = []
        if action == ExitAction.REDUCE_PAPER_POSITION.value:
            events.append({"kind": "reduce",
                           "fraction": float(proposal.get("fraction", self.cfg.reduce_fraction)),
                           "basis": "remaining-rr degraded"})
        d = self._finish(
            view, assessments, ctx, now_ms, action,
            f"Applying {action} proposed by {worker.worker_id} "
            f"(evidence: {json.dumps(worker.evidence, default=str)[:300]}). "
            f"RiskGuard passed ({'; '.join(guard_reasons) or 'all invariants hold'}).",
            rejected=[
                {"alternative": "NO_CHANGE", "reason": "fresh evidence supports the adjustment and guardrails pass"},
            ],
            plan_after=new_plan.to_dict(),
            events=events,
            params=proposal,
        )
        d.risk_guard = {"ok": True, "reasons": guard_reasons, "action": action}
        return d

    def _no_change_rationale(
        self, view: PositionView, by_id: Dict[str, WorkerAssessment]
    ) -> str:
        bits = []
        for wid in ("structure", "target", "stop", "counter", "riskreward"):
            w = by_id.get(wid)
            if w is not None:
                bits.append(f"{wid}={w.verdict}")
        return (
            f"Retaining plan v{view.plan.version} for {view.state.value} position: "
            + "; ".join(bits) + ". No worker produced a state-admissible, "
            "guard-passing reason to intervene. Doing nothing is the decision."
        )

    def _no_change_rejections(
        self, view: PositionView, by_id: Dict[str, WorkerAssessment]
    ) -> List[Dict[str, str]]:
        out = []
        for wid, w in by_id.items():
            if w.proposed_action and w.proposed_action not in (
                ExitAction.NO_CHANGE.value, None,
            ):
                out.append({
                    "alternative": w.proposed_action,
                    "reason": f"proposed by {wid} but not admissible "
                              f"({w.verdict}; state={view.state.value}) or superseded",
                })
        if not out:
            out.append({"alternative": "any intervention",
                        "reason": "no worker proposed an action"})
        return out

    def _finish(
        self,
        view: PositionView,
        assessments: List[WorkerAssessment],
        ctx: WorkerCtx,
        now_ms: int,
        action: str,
        rationale: str,
        rejected: Optional[List[Dict[str, str]]] = None,
        plan_after: Optional[Dict[str, Any]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> SupervisorDecision:
        clean_params = {k: v for k, v in (params or {}).items()
                        if not str(k).startswith("_")}
        key = decision_key(view.signal_id, view.plan.version, ctx.data_ts_ms,
                           action, clean_params)
        if plan_after is not None and not plan_after.get("basis_decision_key"):
            plan_after = dict(plan_after, basis_decision_key=key)
        return SupervisorDecision(
            signal_id=view.signal_id,
            action=action,
            rationale=rationale,
            rejected=rejected or [],
            assessments=[a.to_dict() for a in assessments],
            risk_guard={},
            decision_key=key,
            plan_after=plan_after,
            events_applied=events or [],
            config_version=config_version(self.cfg),
            code_version=EXITS_CODE_VERSION,
            decided_at_ms=now_ms,
            data_ts_ms=ctx.data_ts_ms,
        )
