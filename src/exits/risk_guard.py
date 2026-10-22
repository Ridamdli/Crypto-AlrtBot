"""RiskGuard — deterministic validation of every proposed plan change.

Mandatory invariants (spec §D):
1. Never widen a stop in a way that increases original planned risk.
2. Never increase position size, leverage, or the original risk budget.
3. Never move a stop in the direction that increases downside exposure.
4. LONG: stop adjustments must not lower the stop.
   SHORT: stop adjustments must not raise the stop.
5. Never silently increase exposure to recover losses (no size/leverage
   fields are even accepted in proposals — their presence is a rejection).
6. Changed targets/stops are re-evaluated with actual position size and the
   same fee/slippage assumptions as the outcome evaluator.
7. The existing risk engine's hard constraints are re-checked
   (geometry per SignalModel, min economics for adjusted TP1).
8. Price-side validity + finite/positive numerics + consistent units
   (all absolute prices, same quote currency as the signal).
9. Duplicate / stale / out-of-order decisions are rejected here via the
   idempotency key and data-timestamp monotonicity.
10. This subsystem cannot execute real-money orders: this module imports
    only stdlib, typing, and exits models. A unit test scans src/exits for
    forbidden imports (requests, broker, order, execute).
11. P2: a take profit may only move toward the current price. Widening is
    rejected; an unavailable mark fails closed.

A tighter stop raises premature-exit risk; that tradeoff is the Stop
worker's documented concern and the supervisor's rationale — RiskGuard only
certifies that the change is *safe* (risk never up), never that it is wise.
"""
from typing import Any

from src.exits import policy
from src.exits.config import ExitConfig
from src.exits.models import ExitAction, ExitPlan, PositionState, PositionView


class RiskGuard:
    def __init__(self, cfg: ExitConfig):
        self.cfg = cfg

    def validate(
        self,
        view: PositionView,
        action: str,
        proposal: dict[str, Any],
        seen_keys: frozenset = frozenset(),
        last_data_ts_ms: int = 0,
        data_ts_ms: int = 0,
        mark: float | None = None,
    ) -> tuple[bool, list[str]]:
        """Returns (ok, reasons). Reasons explain acceptance or every rejection."""
        reasons: list[str] = []
        ok, why = self._check(proposal, view, action, seen_keys, last_data_ts_ms, data_ts_ms, mark)
        reasons.extend(why)
        return ok, reasons

    # ── internals ───────────────────────────────────────────────────
    def _check(
        self,
        proposal: dict[str, Any],
        view: PositionView,
        action: str,
        seen_keys: frozenset,
        last_data_ts_ms: int,
        data_ts_ms: int,
        mark: float | None = None,
    ) -> tuple[bool, list[str]]:
        sig = view.signal
        plan = view.plan
        side = str(sig.get("side", "")).upper()
        try:
            entry = float(sig["entry"])
        except (TypeError, ValueError, KeyError):
            return False, ["signal entry missing/non-numeric"]

        # Invariant 2/5/10: proposals carry prices only. Any sizing, leverage,
        # risk-budget, or execution field is an instant rejection.
        forbidden = {"size", "quantity", "leverage", "risk_amount",
                     "risk_budget", "margin", "order", "execute", "notional"}
        hit = forbidden & set(proposal)
        if hit:
            return False, [f"proposal carries forbidden exposure fields: {sorted(hit)}"]

        # Invariant 9: idempotency + ordering.
        key = str(proposal.get("_decision_key", ""))
        if key and key in seen_keys:
            return False, ["duplicate decision (idempotency key already applied)"]
        if data_ts_ms and last_data_ts_ms and data_ts_ms < last_data_ts_ms:
            return False, ["stale decision (older market data than last applied)"]

        if action == ExitAction.TIGHTEN_STOP.value:
            return self._check_tighten(proposal, view, side, entry, plan)
        if action == ExitAction.ADJUST_TAKE_PROFITS.value:
            return self._check_targets(proposal, view, side, entry, plan, mark)
        if action in (ExitAction.REDUCE_PAPER_POSITION.value,
                      ExitAction.CLOSE_PAPER_POSITION.value,
                      ExitAction.INVALIDATE_PENDING_SETUP.value,
                      ExitAction.FLAG_CONFLICT.value,
                      ExitAction.NO_CHANGE.value,
                      ExitAction.INSUFFICIENT_DATA.value):
            return self._check_non_plan(action, proposal, view)
        return False, [f"unknown action {action}"]

    def _check_tighten(
        self, proposal: dict[str, Any], view: PositionView,
        side: str, entry: float, plan: ExitPlan,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if view.state not in (PositionState.OPEN, PositionState.PARTIAL):
            return False, [f"TIGHTEN_STOP inadmissible for {view.state.value} (no exposure to protect)"]
        try:
            new_sl = float(proposal["stop_loss"])
        except (TypeError, ValueError, KeyError):
            return False, ["proposal.stop_loss missing/non-numeric"]
        # Invariant 8: finite positive absolute price.
        if not (new_sl > 0 and new_sl == new_sl and abs(new_sl) != float("inf")):
            return False, ["proposal.stop_loss not a finite positive price"]
        orig_sl = plan.stop_loss
        # Invariants 1/3/4: never widen vs the ORIGINAL plan, never move the
        # wrong way. LONG: orig_sl <= new_sl <= entry. SHORT mirrored.
        if side == "LONG":
            if new_sl < orig_sl:
                return False, [f"stop {new_sl} widens original stop {orig_sl} (LONG may not lower stops)"]
            if new_sl > entry:
                return False, [f"stop {new_sl} crossed entry {entry} (would invert the trade)"]
        elif side == "SHORT":
            if new_sl > orig_sl:
                return False, [f"stop {new_sl} widens original stop {orig_sl} (SHORT may not raise stops)"]
            if new_sl < entry:
                return False, [f"stop {new_sl} crossed entry {entry} (would invert the trade)"]
        else:
            return False, [f"unknown side {side}"]
        if new_sl == plan.stop_loss:
            return False, ["stop unchanged (no-op, prevents duplicate churn)"]
        reasons.append(f"stop {orig_sl} -> {new_sl}: risk distance never wider than original")
        return True, reasons

    def _check_targets(
        self, proposal: dict[str, Any], view: PositionView,
        side: str, entry: float, plan: ExitPlan,
        mark: float | None = None,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if view.state not in (PositionState.OPEN, PositionState.PARTIAL):
            return False, [f"ADJUST_TAKE_PROFITS inadmissible for {view.state.value}"]
        try:
            tp = float(proposal["tp"])
        except (TypeError, ValueError, KeyError):
            return False, ["proposal tp missing/non-numeric"]
        if not (tp > 0 and tp == tp and abs(tp) != float("inf")):
            return False, ["target must be a finite positive price"]
        # Invariant 7/8: direction geometry (single TP on the profit side).
        if side == "LONG":
            if not (entry < tp):
                return False, ["LONG target must satisfy entry < tp"]
        elif side == "SHORT":
            if not (entry > tp):
                return False, ["SHORT target must satisfy entry > tp"]
        else:
            return False, [f"unknown side {side}"]
        # P2: a target may only move toward the current price. Widening
        # re-opens a capped win and raises the move needed to book the same
        # R. Fail closed when the mark is unavailable.
        if mark is None or not (mark > 0 and mark == mark and abs(mark) != float("inf")):
            return False, ["TP_WIDEN_REJECTED: mark unavailable (fail closed)"]
        s = 1.0 if side == "LONG" else -1.0
        if s * (plan.tp - mark) < 0 or s * (tp - mark) < 0:
            return False, [
                f"TP_WIDEN_REJECTED: target {tp} is on the loss side of mark {mark}"]
        if abs(tp - mark) > abs(plan.tp - mark):
            return False, [
                f"TP_WIDEN_REJECTED: target {plan.tp} -> {tp} moves away from mark {mark}"]
        # Invariant 6/7: re-check economics with real costs, measured from
        # ENTRY (a target's worth is entry-framed; ref-price framing is what
        # workers report for remaining potential, not the acceptance gate).
        gross_from_entry = policy.gross_rr(entry, plan.stop_loss, tp)
        net_from_entry = round(gross_from_entry - policy.fee_cost_r(
            entry, abs(entry - plan.stop_loss), self.cfg), 3)
        if gross_from_entry < self.cfg.min_adjusted_tp_rr or net_from_entry <= 0:
            return False, [
                f"adjusted tp economics fail (gross {gross_from_entry}R < "
                f"{self.cfg.min_adjusted_tp_rr}R or net {net_from_entry}R <= 0)"
            ]
        if tp == plan.tp:
            return False, ["target unchanged (no-op)"]
        reasons.append(f"target keeps gross {gross_from_entry}R / net {net_from_entry}R at tp")
        return True, reasons

    @staticmethod
    def _check_non_plan(
        action: str, proposal: dict[str, Any], view: PositionView
    ) -> tuple[bool, list[str]]:
        if action == ExitAction.REDUCE_PAPER_POSITION.value:
            if view.state not in (PositionState.OPEN, PositionState.PARTIAL):
                return False, [f"REDUCE inadmissible for {view.state.value}"]
            try:
                frac = float(proposal.get("fraction", 0.5))
            except (TypeError, ValueError):
                return False, ["reduce fraction non-numeric"]
            if not 0.0 < frac < 1.0:
                return False, ["reduce fraction must be in (0, 1) — never full, never zero"]
            return True, [f"paper exposure reduced by {frac:.0%}; levels unchanged"]
        if action == ExitAction.CLOSE_PAPER_POSITION.value:
            if view.state not in (PositionState.OPEN, PositionState.PARTIAL):
                return False, [f"CLOSE inadmissible for {view.state.value}"]
            return True, ["paper position marked closed; levels unchanged"]
        if action == ExitAction.INVALIDATE_PENDING_SETUP.value:
            if view.state != PositionState.PENDING:
                return False, [f"INVALIDATE inadmissible for {view.state.value} (already triggered)"]
            return True, ["pending setup invalidated before entry; no exposure existed"]
        return True, [f"{action} carries no plan mutation"]
