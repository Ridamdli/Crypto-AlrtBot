"""ExitWorkers — specialized deterministic evaluators.

Each worker implements assess(ctx) -> WorkerAssessment. Workers PROPOSE;
they never modify trades, ledgers, or plans.

Shared context (WorkerCtx): signal dict, active ExitPlan, PositionView state,
completed klines (ONLY completed candles — enforced by the monitor), now_ms,
config, and optional extras (opposing candidates, outcome record).

Dependency disclosure (double-counting prevention): Structure, Stop, Target
and Counter-Signal workers all derive from the same primitives (completed
closes, ATR-14, 20-bar swings). They are NOT independent evidence sources.
Every assessment tags `indicators` with the primitive ids it used
("close", "atr14", "swing20", "oppbreak20", "volume"); the supervisor treats
same-indicator agreement as ONE observation, not corroboration.

Missing-data policy (explicit, per spec): a worker missing a REQUIRED input
(fresh closes, ATR when needed) ABSTAINS with evidence_quality=0.0 and a
stated uncertainty. Missing OPTIONAL inputs (OI — which the live pipeline
does not feed to supervision at all) are documented in the uncertainty note
and discount quality by 0.8x; they are never treated as neutral evidence.
"""
from typing import Any, Dict, List, Optional

from src.exits import policy
from src.exits.config import ExitConfig
from src.exits.models import ExitAction, PositionState, WorkerAssessment


class WorkerCtx:
    """Inputs for one evaluation. `completed` must contain completed candles only."""

    def __init__(
        self,
        signal: Dict[str, Any],
        plan_stop: float,
        plan_tps: List[float],
        state: PositionState,
        completed: List[Dict[str, Any]],
        now_ms: int,
        cfg: ExitConfig,
        data_ts_ms: int = 0,
        outcome: Optional[Dict[str, Any]] = None,
        fresh: bool = True,
        freshness_reason: str = "ok",
    ):
        self.signal = signal
        self.plan_stop = plan_stop
        self.plan_tps = plan_tps
        self.state = state
        self.completed = completed
        self.now_ms = now_ms
        self.cfg = cfg
        self.data_ts_ms = data_ts_ms
        self.outcome = outcome or {}
        self.fresh = fresh
        self.freshness_reason = freshness_reason

    @property
    def side(self) -> str:
        return str(self.signal.get("side", "")).upper()

    @property
    def entry(self) -> float:
        return float(self.signal["entry"])

    @property
    def last_close(self) -> Optional[float]:
        if not self.completed:
            return None
        try:
            return float(self.completed[-1]["close"])
        except (TypeError, ValueError, KeyError):
            return None


def _base(worker_id: str, version: str, ctx: WorkerCtx) -> WorkerAssessment:
    return WorkerAssessment(
        worker_id=worker_id,
        worker_version=version,
        assessed_at_ms=ctx.now_ms,
        data_ts_ms=ctx.data_ts_ms,
        verdict="ABSTAIN",
    )


def _quality(ctx: WorkerCtx, thin_ok: bool = False) -> float:
    """Evidence-quality discount: 1.0 fresh+complete, scaled down otherwise."""
    q = 1.0
    if not ctx.fresh:
        q *= 0.5
    if len(ctx.completed) < ctx.cfg.min_bars and not thin_ok:
        q *= 0.7
    q *= 0.8  # OI is unavailable to supervision (documented, not neutral)
    return round(q, 3)


class StructureWorker:
    """Verdict on whether the directional thesis and invalidation hold.

    Rules:
    - Completed close beyond the ACTIVE stop -> THESIS_INVALIDATED.
    - Pending setup whose entry window elapsed with enough observed bars
      (mirrors evaluator max_entry_bars=6) and entry never triggered
      -> THESIS_INVALIDATED (nothing to manage).
    - Otherwise counts adverse closes (weak_trend rule) -> THESIS_WEAKENED
      or THESIS_INTACT. Weakening alone never proposes an exit.
    """

    ID = "structure"
    VERSION = "1.0.0"

    def assess(self, ctx: WorkerCtx) -> WorkerAssessment:
        a = _base(self.ID, self.VERSION, ctx)
        a.indicators = ["close", "swing20"]
        if not ctx.fresh or ctx.last_close is None:
            a.uncertainty = f"abstain: {ctx.freshness_reason}"
            return a
        side, entry = ctx.side, ctx.entry
        sl = ctx.plan_stop
        breached = policy.stop_breached(ctx.last_close, side, sl)
        a.evidence = {
            "last_close": ctx.last_close,
            "active_stop": sl,
            "stop_breached": breached,
        }
        # Pending setups that never triggered inside the entry window are done.
        if ctx.state == PositionState.PENDING and self._window_elapsed(ctx):
            a.verdict = "THESIS_INVALIDATED"
            a.evidence["entry_window_elapsed"] = True
            a.proposed_action = ExitAction.INVALIDATE_PENDING_SETUP.value
            a.evidence_quality = _quality(ctx)
            return a
        if breached:
            a.verdict = "THESIS_INVALIDATED"
            if ctx.state in (PositionState.OPEN, PositionState.PARTIAL):
                a.proposed_action = ExitAction.CLOSE_PAPER_POSITION.value
                a.proposal = {"reason": "completed close beyond active stop"}
            elif ctx.state == PositionState.PENDING:
                a.proposed_action = ExitAction.INVALIDATE_PENDING_SETUP.value
            a.evidence_quality = _quality(ctx)
            return a
        against = policy.closes_against(ctx.completed, side, ctx.cfg.weak_trend_bars)
        a.evidence["adverse_closes_last_n"] = against
        a.evidence["weak_trend_threshold"] = ctx.cfg.weak_trend_min_against
        if against >= ctx.cfg.weak_trend_min_against:
            a.verdict = "THESIS_WEAKENED"
            a.uncertainty = "momentum deterioration is evidence only; no exit proposed"
        else:
            a.verdict = "THESIS_INTACT"
        a.evidence_quality = _quality(ctx)
        return a

    @staticmethod
    def _window_elapsed(ctx: WorkerCtx) -> bool:
        """Entry window (6x15m) elapsed AND enough bars observed to judge fairly.

        Fairness rule mirrors the tracker: never invalidate young setups on
        truncated data — require >= max_entry_bars completed bars after the
        signal timestamp before calling the window.
        """
        from datetime import datetime, timezone

        try:
            ts = ctx.signal.get("timestamp")
            sig_ms = int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
        except (TypeError, ValueError):
            return False
        bars_after = [k for k in ctx.completed if float(k.get("timestamp", 0)) > sig_ms]
        window_ms = 6 * ctx.cfg.timeframe_ms
        return len(bars_after) >= 6 and (ctx.now_ms - sig_ms) > window_ms


class TargetWorker:
    """Verdict on whether TP1/TP2/TP3 remain realistic.

    A remaining target farther than max_tp_atr ATRs from the reference price
    (last completed close) is flagged unrealistic. Proposes pulling flagged
    targets back to the ATR cap while preserving direction ordering and the
    minimum adjusted-TP1 economics. Targets already hit (partial fills) are
    excluded from adjustment — the fill is history, not a plan.
    """

    ID = "target"
    VERSION = "1.0.0"

    def assess(self, ctx: WorkerCtx) -> WorkerAssessment:
        a = _base(self.ID, self.VERSION, ctx)
        a.indicators = ["close", "atr14"]
        if not ctx.fresh or ctx.last_close is None:
            a.uncertainty = f"abstain: {ctx.freshness_reason}"
            return a
        atr = policy.atr_wilder(ctx.completed, ctx.cfg.atr_period)
        if atr is None or atr <= 0:
            a.uncertainty = "abstain: ATR unavailable (short history)"
            return a
        # Only not-yet-hit targets are adjustable.
        tps = self._remaining_tps(ctx)
        dists = policy.tp_distances_atr(ctx.last_close, ctx.side, tps, atr)
        a.evidence = {
            "atr": round(atr, 6),
            "ref_price": ctx.last_close,
            "remaining_tps": tps,
            "distances_atr": dists,
            "max_tp_atr": ctx.cfg.max_tp_atr,
        }
        flagged = [t for t, d in zip(tps, dists) if d is not None and d > ctx.cfg.max_tp_atr]
        if not flagged:
            a.verdict = "TARGETS_OK"
            a.evidence_quality = _quality(ctx)
            return a
        new_tps = self._pull_to_cap(ctx, tps, atr)
        if new_tps is None:
            a.verdict = "TARGETS_UNREALISTIC"
            a.uncertainty = (
                "targets exceed ATR cap but no valid pullback preserves "
                "ordering/economics; no adjustment proposed"
            )
            a.evidence_quality = _quality(ctx)
            return a
        a.verdict = "TARGETS_UNREALISTIC"
        a.proposed_action = ExitAction.ADJUST_TAKE_PROFITS.value
        a.proposal = {"tp1": new_tps[0], "tp2": new_tps[1], "tp3": new_tps[2]}
        a.evidence["proposed_tps"] = new_tps
        a.evidence_quality = _quality(ctx)
        return a

    def _remaining_tps(self, ctx: WorkerCtx) -> List[float]:
        """TPs not yet filled. Fill state comes from the outcome record."""
        tps = list(ctx.plan_tps)
        if ctx.state == PositionState.PARTIAL:
            if ctx.outcome.get("tp2_hit"):
                return [tps[2]]
            if ctx.outcome.get("tp1_hit"):
                return [tps[1], tps[2]]
        return tps

    def _pull_to_cap(
        self, ctx: WorkerCtx, tps: List[float], atr: float
    ) -> Optional[List[float]]:
        """Pull over-cap targets back to the ATR cap, preserving the ladder.

        Stability lesson (eval v1): pulling every violating rung to the same
        cap flattens tp1==tp2==tp3 and destroys the 50/25/25 tranche structure
        the settlement accounting assumes. So after the pull, rung spacing of
        at least min_tp_gap_atr is enforced OUTWARD from the nearest target:
        tp1 (the actionable rung) stays at the realistic cap while outer
        rungs keep structural separation. If economics on tp1 then fail, or
        ordering cannot be preserved, propose nothing — a retained original
        plan beats a flattened ladder.
        """
        ref, side = ctx.last_close, ctx.side
        cap = ctx.cfg.max_tp_atr * atr
        gap = ctx.cfg.min_tp_gap_atr * atr
        pulled = []
        for tp in tps:
            dist = (tp - ref) if side == "LONG" else (ref - tp)
            if dist > cap:
                pulled.append(ref + cap if side == "LONG" else ref - cap)
            else:
                pulled.append(tp)
        # Direction ordering first (LONG ascending / SHORT descending).
        ordered = sorted(pulled) if side == "LONG" else sorted(pulled, reverse=True)
        # Then enforce minimum rung spacing outward from the nearest target.
        spaced = [ordered[0]]
        for rung in ordered[1:]:
            if side == "LONG":
                spaced.append(max(rung, spaced[-1] + gap))
            else:
                spaced.append(min(rung, spaced[-1] - gap))
        if side == "LONG":
            full = [spaced[0]] * (3 - len(spaced)) + spaced if len(spaced) < 3 else spaced
        else:
            full = spaced + [spaced[-1]] * (3 - len(spaced)) if len(spaced) < 3 else spaced
        # Economics gate on the nearest adjusted target.
        entry, sl = ctx.entry, ctx.plan_stop
        rr = policy.gross_rr(entry, sl, full[0])
        net = policy.remaining_net_rr(ref, side, entry, sl, full[0], ctx.cfg)
        if rr < ctx.cfg.min_adjusted_tp1_rr or net <= 0:
            return None
        return [round(x, 6) for x in full]


class StopWorker:
    """Verdict on whether the stop should be tightened.

    Proposes TIGHTEN_STOP only when a NEW structural level (recent swing)
    sits strictly between the current stop and the entry, leaves at least
    min_stop_dist_atr of adverse room, and only for OPEN/PARTIAL positions.
    Pending setups have no exposure to protect — never touch their stops.
    Tightening toward (never beyond) entry; widening is not representable.
    """

    ID = "stop"
    VERSION = "1.0.0"

    def assess(self, ctx: WorkerCtx) -> WorkerAssessment:
        a = _base(self.ID, self.VERSION, ctx)
        a.indicators = ["close", "atr14", "swing20"]
        if ctx.state not in (PositionState.OPEN, PositionState.PARTIAL):
            a.verdict = "STOP_OK"
            a.uncertainty = f"no stop action for {ctx.state.value} positions"
            a.evidence_quality = _quality(ctx)
            return a
        if not ctx.fresh or ctx.last_close is None:
            a.uncertainty = f"abstain: {ctx.freshness_reason}"
            return a
        atr = policy.atr_wilder(ctx.completed, ctx.cfg.atr_period)
        if atr is None or atr <= 0:
            a.uncertainty = "abstain: ATR unavailable (short history)"
            return a
        hi, lo = policy.swing_levels(ctx.completed, ctx.cfg.swing_lookback)
        if hi is None or lo is None:
            a.uncertainty = "abstain: swing levels unavailable"
            return a
        side, entry, sl = ctx.side, ctx.entry, ctx.plan_stop
        buf = ctx.cfg.swing_buffer
        if side == "LONG":
            candidate = lo * (1 - buf)
            tighter = candidate > sl
            room_ok = (ctx.last_close - candidate) >= ctx.cfg.min_stop_dist_atr * atr
            in_range = candidate < entry
        else:
            candidate = hi * (1 + buf)
            tighter = candidate < sl
            room_ok = (candidate - ctx.last_close) >= ctx.cfg.min_stop_dist_atr * atr
            in_range = candidate > entry
        a.evidence = {
            "atr": round(atr, 6),
            "swing_high": hi,
            "swing_low": lo,
            "structural_candidate": round(candidate, 6),
            "strictly_tighter": tighter,
            "adverse_room_ok": room_ok,
            "inside_entry": in_range,
        }
        if tighter and room_ok and in_range:
            a.verdict = "STOP_TIGHTEN_CANDIDATE"
            a.proposed_action = ExitAction.TIGHTEN_STOP.value
            a.proposal = {"stop_loss": round(candidate, 6)}
        else:
            a.verdict = "STOP_OK"
            a.uncertainty = "no structural level justifies a tighter stop right now"
        a.evidence_quality = _quality(ctx)
        return a


class CounterSignalWorker:
    """Opposing-setup evaluation with reversal-vs-conflict discipline.

    - GENUINE_REVERSAL requires BOTH an opposite completed-close break AND
      momentum deterioration (4-of-5 adverse closes). Either alone is not
      a reversal.
    - A lone opposing setup (break without deterioration, or deterioration
      without a break) is WEAK_OPPOSITION: evidence, never an exit trigger.
    - Proposes FLAG_CONFLICT on reversal; CLOSE_PAPER_POSITION only when the
      position is OPEN/PARTIAL AND already down >= 0.75R (thesis materially
      damaged, not a prediction). An opposing signal alone NEVER closes —
      the supervisor enforces this even if this worker is misread.
    Shares ATR/swing/break primitives with Structure/Target workers
    (see module docstring: not independent evidence).
    """

    ID = "counter"
    VERSION = "1.0.0"

    def assess(self, ctx: WorkerCtx) -> WorkerAssessment:
        a = _base(self.ID, self.VERSION, ctx)
        a.indicators = ["close", "oppbreak20"]
        if not ctx.fresh or ctx.last_close is None:
            a.uncertainty = f"abstain: {ctx.freshness_reason}"
            return a
        if ctx.state in (PositionState.CLOSED, PositionState.INVALIDATED):
            a.verdict = "NO_OPPOSITION"
            a.uncertainty = "nothing to oppose in a terminal state"
            a.evidence_quality = _quality(ctx)
            return a
        broken, info = policy.opposite_break(ctx.completed, ctx.side, ctx.cfg.swing_lookback)
        against = policy.closes_against(ctx.completed, ctx.side, ctx.cfg.weak_trend_bars)
        deteriorated = against >= ctx.cfg.weak_trend_min_against
        a.evidence = {
            "opposite_break": broken,
            "break_info": info,
            "adverse_closes_last_n": against,
            "momentum_deteriorated": deteriorated,
        }
        if broken and deteriorated:
            a.verdict = "GENUINE_REVERSAL"
            unreal = self._unrealized_r(ctx)
            a.evidence["unrealized_r"] = unreal
            if ctx.state in (PositionState.OPEN, PositionState.PARTIAL) and unreal <= -0.75:
                a.proposed_action = ExitAction.CLOSE_PAPER_POSITION.value
                a.proposal = {"reason": "reversal evidence with >=0.75R damage"}
            else:
                a.proposed_action = ExitAction.FLAG_CONFLICT.value
                a.proposal = {"reason": "reversal evidence; hold for structural verdict"}
        elif broken or deteriorated:
            a.verdict = "WEAK_OPPOSITION"
            a.uncertainty = "opposing evidence without confirmation never triggers an exit"
        else:
            a.verdict = "NO_OPPOSITION"
        a.evidence_quality = _quality(ctx)
        return a

    def _unrealized_r(self, ctx: WorkerCtx) -> float:
        try:
            risk = abs(ctx.entry - ctx.plan_stop)
            if risk <= 0 or ctx.last_close is None:
                return 0.0
            move = (ctx.last_close - ctx.entry) if ctx.side == "LONG" else (ctx.entry - ctx.last_close)
            return round(move / risk, 3)
        except (TypeError, ValueError):
            return 0.0


class RiskRewardWorker:
    """Net reward-to-risk from the CURRENT price (not from entry).

    Uses remaining distances to un-hit TPs and to the stop, minus full
    round-trip costs (conservative: the exit still pays them). Proposes
    REDUCE_PAPER_POSITION when the next target's remaining net R falls below
    min_remaining_rr on an OPEN/PARTIAL position; CLOSE_PAPER_POSITION only
    when every remaining target is net-negative AND downside exceeds upside
    by 2x (i.e. the plan is arithmetically dead, not merely unattractive).
    Never proposes widening, sizing up, or leverage changes (not representable).
    """

    ID = "riskreward"
    VERSION = "1.0.0"

    def assess(self, ctx: WorkerCtx) -> WorkerAssessment:
        a = _base(self.ID, self.VERSION, ctx)
        a.indicators = ["close"]
        if not ctx.fresh or ctx.last_close is None:
            a.uncertainty = f"abstain: {ctx.freshness_reason}"
            return a
        if ctx.state not in (PositionState.OPEN, PositionState.PARTIAL):
            a.verdict = "RR_OK"
            a.uncertainty = f"no exposure to re-risk for {ctx.state.value} state"
            a.evidence_quality = _quality(ctx)
            return a
        ref, side, entry, sl = ctx.last_close, ctx.side, ctx.entry, ctx.plan_stop
        tps = self._remaining_tps(ctx)
        nets = [policy.remaining_net_rr(ref, side, entry, sl, tp, ctx.cfg) for tp in tps]
        risk_dist = abs(entry - sl)
        downside = abs(ref - sl) / risk_dist if risk_dist > 0 else float("inf")
        upside = max(nets) if nets else 0.0
        a.evidence = {
            "ref_price": ref,
            "remaining_tps": tps,
            "remaining_net_rr": nets,
            "downside_r": round(downside, 3),
            "best_upside_net_r": round(upside, 3),
            "fees_cost_r": policy.fee_cost_r(entry, risk_dist, ctx.cfg),
        }
        if nets and all(n < 0 for n in nets) and downside > 2 * max(upside, 0.0):
            a.verdict = "RR_DEAD"
            a.proposed_action = ExitAction.CLOSE_PAPER_POSITION.value
            a.proposal = {"reason": "all remaining targets net-negative, downside dominates"}
        elif nets and nets[0] < ctx.cfg.min_remaining_rr:
            a.verdict = "RR_DEGRADED"
            a.proposed_action = ExitAction.REDUCE_PAPER_POSITION.value
            a.proposal = {"fraction": ctx.cfg.reduce_fraction}
        else:
            a.verdict = "RR_OK"
        a.evidence_quality = _quality(ctx)
        return a

    def _remaining_tps(self, ctx: WorkerCtx) -> List[float]:
        tps = list(ctx.plan_tps)
        if ctx.state == PositionState.PARTIAL:
            if ctx.outcome.get("tp2_hit"):
                return [tps[2]]
            if ctx.outcome.get("tp1_hit"):
                return [tps[1], tps[2]]
        return tps


ALL_WORKERS = [StructureWorker, TargetWorker, StopWorker, CounterSignalWorker, RiskRewardWorker]
