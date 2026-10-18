"""ExitMonitor — event-driven supervision inside the scheduler loop.

Evaluates a non-final setup when a meaningful event occurs:
  - a new completed 15m candle since its last evaluation,
  - price (last completed close) within proximity_atr of the next TP/SL,
  - or scheduled reevaluation due (max_reeval_s elapsed).
Invalidation evidence is decided by workers once triggered (a print
through a level normally trips proximity first); worst case it waits
for the next completed candle, not for max_reeval_s.

Guarantees: completed candles only; configurable frequency + freshness
limits; idempotent (decision-key dedupe + data-ts monotonicity); bounded
work (max_evals_per_cycle, klines_limit); never raises — any per-signal
failure is logged and skipped so signal generation is never blocked.

Paper-only: the monitor writes ONLY the exit-decision ledger. It never
touches outcome/portfolio/Invo ledgers and never emits orders. With
EXITS_APPLY=false (default) even plan overlays stay frozen — decisions are
recorded as proposals for audit and for the offline candidate harness.
"""
from typing import Any, Callable, Dict, List, Optional

from src.exits import policy
from src.exits.config import ExitConfig, load_exit_config
from src.exits.ledger import ExitDecisionLedger
from src.exits.models import (
    ExitAction,
    PositionState,
    PositionView,
    classify_position,
)
from src.exits.supervisor import ExitSupervisor
from src.exits.workers import ALL_WORKERS, WorkerCtx
from src.tracking.outcome_tracker import OutcomeTracker
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ExitMonitor:
    def __init__(
        self,
        cfg: Optional[ExitConfig] = None,
        tracker: Optional[OutcomeTracker] = None,
        ledger: Optional[ExitDecisionLedger] = None,
        fetcher: Any = None,
        now_ms_fn: Optional[Callable[[], int]] = None,
        apply: Optional[bool] = None,
    ):
        # NOTE: fetcher is injected (duck-typed: fetch_standardized_klines).
        # This package never constructs network/trading clients itself — the
        # scheduler passes the pipeline's existing public-data fetcher.
        # fetcher=None is fail-safe: every signal records INSUFFICIENT_DATA.
        self.cfg = cfg or load_exit_config()
        self.tracker = tracker or OutcomeTracker()
        self.ledger = ledger or ExitDecisionLedger(
            storage_dir=self.cfg.storage_dir, ledger_name=self.cfg.ledger_name,
            max_decisions=self.cfg.max_decisions_kept)
        self.fetcher = fetcher
        self._now_ms_fn = now_ms_fn
        if apply is None:
            import os
            apply = os.getenv("EXITS_APPLY", "false").lower() == "true"
        self.apply = bool(apply)
        self.supervisor = ExitSupervisor(self.cfg)
        self._workers = [w() for w in ALL_WORKERS]

    @staticmethod
    def default() -> "ExitMonitor":
        return ExitMonitor()

    def _now_ms(self) -> int:
        if self._now_ms_fn is not None:
            return int(self._now_ms_fn())
        import time
        return int(time.time() * 1000)

    # ── main cycle ────────────────────────────────────────────────
    def run_cycle(self) -> Dict[str, Any]:
        """Supervise all due setups. Returns a summary; never raises."""
        now_ms = self._now_ms()
        summary: Dict[str, Any] = {"evaluated": 0, "skipped": 0,
                                   "actions": {}, "errors": 0}
        try:
            outcomes = self.tracker.load_ledger()
            signals = self.tracker.load_all_signals()
        except Exception as e:
            logger.warning(f"[Exits] Cannot load signals/outcomes: {e}")
            return {**summary, "error": "load failed"}
        evals = 0
        for sig in signals:
            if evals >= self.cfg.max_evals_per_cycle:
                break
            try:
                acted = self._supervise_one(sig, outcomes, now_ms)
            except Exception as e:
                logger.warning(f"[Exits] Supervision failed for "
                               f"{sig.get('signal_id', '?')}: {e}")
                summary["errors"] += 1
                continue
            if acted is None:
                summary["skipped"] += 1
            else:
                evals += 1
                summary["evaluated"] += 1
                summary["actions"][acted] = summary["actions"].get(acted, 0) + 1
        # Link fresh outcomes back to past decisions (audit trail).
        try:
            for sid, oc in outcomes.items():
                if oc.get("final"):
                    self.ledger.outcome_link(sid, oc)
        except Exception as e:
            logger.warning(f"[Exits] Outcome linking failed: {e}")
        return summary

    # ── per-signal ────────────────────────────────────────────────
    def _supervise_one(
        self, signal: Dict[str, Any], outcomes: Dict[str, Dict[str, Any]], now_ms: int
    ) -> Optional[str]:
        """Returns the decided action, or None when no evaluation was due."""
        sid = signal.get("signal_id", "?")
        outcome = outcomes.get(signal.get("signal_id", ""))
        if outcome is not None and outcome.get("final"):
            return None  # CLOSED — nothing to supervise
        invalidated = self.ledger.is_invalidated(signal.get("signal_id", ""))
        state = classify_position(signal, outcome, invalidated)
        if state in (PositionState.CLOSED, PositionState.INVALIDATED):
            return None
        plan = self.ledger.active_plan(signal)

        klines = self._fetch(signal.get("symbol", ""), now_ms)
        completed, _ = policy.split_completed(klines, now_ms, self.cfg.timeframe_ms)
        fresh, reason, data_ts = policy.check_freshness(completed, now_ms, self.cfg)

        last = self.ledger.last_eval(signal.get("signal_id", ""))
        if not self._is_due(plan, completed, data_ts, last, now_ms):
            return None

        ctx = WorkerCtx(signal, plan.stop_loss, [plan.tp],
                        state, completed, now_ms, self.cfg,
                        data_ts_ms=data_ts, outcome=outcome,
                        fresh=fresh, freshness_reason=reason)
        assessments = [w.assess(ctx) for w in self._workers]
        # Stability gate: throttle on the last APPLIED intervention (plan
        # change or reduce/close event), not just plan birth — otherwise a
        # repeat REDUCE (which leaves levels untouched) would never start
        # the cooldown clock. Genesis (no interventions) is exempt.
        last_change = plan.decided_at_ms
        try:
            for e in self.ledger.get_events(signal.get("signal_id", "")):
                last_change = max(last_change, int(e.get("decided_at_ms", 0) or 0))
        except (TypeError, ValueError):
            pass
        decision = self.supervisor.decide(
            PositionView(signal, outcome, state, plan, invalidated),
            assessments, ctx, now_ms,
            seen_keys=self.ledger.seen_keys(),
            last_data_ts_ms=last["data_ts"],
            last_change_ts_ms=last_change,
        )
        # Ledger-level idempotency: identical re-decision is recorded once.
        if self.ledger.has_key(decision.decision_key):
            self.ledger.mark_evaluated(sid, data_ts, now_ms)
            return None
        # Fail safe: application requires a healthy persistent ledger.
        apply = self.apply and self.ledger.healthy
        if self.apply and not self.ledger.healthy:
            logger.warning("[Exits] Ledger unhealthy — recording decision without applying.")
        self.ledger.record(decision, apply=apply)
        logger.info(f"[Exits] {sid} {state.value} -> {decision.action} "
                    f"(plan v{plan.version}, data_ts={data_ts}, apply={apply})")
        return decision.action

    def _fetch(self, symbol: str, now_ms: int) -> List[Dict[str, Any]]:
        if self.fetcher is None:
            return []  # fail safe: workers abstain -> INSUFFICIENT_DATA
        try:
            return self.fetcher.fetch_standardized_klines(
                symbol, "15m", limit=self.cfg.klines_limit) or []
        except Exception as e:
            logger.warning(f"[Exits] Kline fetch failed for {symbol}: {e}")
            return []

    def _is_due(
        self, plan: Any,
        completed: List[Dict[str, Any]], data_ts: int,
        last: Dict[str, int], now_ms: int,
    ) -> bool:
        """Event-driven gating: evaluate only when something merits it."""
        if not completed:
            # No market data at all: record INSUFFICIENT_DATA at most on the
            # scheduled cadence instead of spamming every cycle.
            if not last["at"]:
                return True
            return (now_ms - last["at"]) >= self.cfg.max_reeval_s * 1000
        if last["data_ts"] and data_ts and data_ts < last["data_ts"]:
            return False  # out-of-order feed; never go backwards
        if not last["data_ts"]:
            return True  # never evaluated
        if now_ms - last["at"] < self.cfg.min_reeval_s * 1000:
            return False  # too soon regardless of events
        if data_ts != last["data_ts"]:
            return True  # new completed candle — the primary event
        if now_ms - last["at"] >= self.cfg.max_reeval_s * 1000:
            return True  # scheduled reevaluation due
        # Proximity event: last close near next TP/SL.
        try:
            ref = float(completed[-1]["close"])
            atr = policy.atr_wilder(completed, self.cfg.atr_period)
            if atr and atr > 0:
                levels = [plan.tp, plan.stop_loss]
                if any(policy.near_level(ref, lv, atr, self.cfg.proximity_atr)
                       for lv in levels):
                    return True
        except (TypeError, ValueError, KeyError):
            pass
        return False
