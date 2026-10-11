"""Exit Orchestrator configuration.

Every threshold the subsystem uses lives here with a documented rationale.
Values may be overridden by environment variables (EXITS_*). Nothing here
changes the signal-generation, risk-budget, or paper-settlement configuration
in src/config.py.
"""
import os
from dataclasses import dataclass


def _getenv_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ExitConfig:
    # Market-data basis. Supervision runs on the 15m entry timeframe, the same
    # candles the outcome tracker settles against (OutcomeTracker.BAR_MS).
    timeframe_ms: int = 15 * 60 * 1000

    # A completed 15m candle is "fresh" if it closed within this window.
    # Rationale: tolerates one missed fetch cycle (900s scheduler cadence)
    # plus network slack, while refusing day-old data.
    max_data_age_s: int = 1800

    # Minimum completed bars required before any worker may opine.
    # Rationale: mirrors the pipeline's own >=25-candle gate so workers never
    # judge on thinner data than the strategy that opened the trade.
    min_bars: int = 25

    # ATR (Wilder) period. Rationale: matches DonchianBreakoutStrategy and
    # StopLossEngine defaults (atr_period=14) so volatility is measured on the
    # same scale the entry used.
    atr_period: int = 14

    # Swing lookback for structural levels. Rationale: matches
    # RiskConfig.sl_lookback=20, i.e. the same structural window behind SLs.
    swing_lookback: int = 20

    # A remaining TP farther than this many ATRs from the reference price is
    # flagged unrealistic. Rationale: heuristic guardrail — on 15m crypto
    # perps with ~1%-risk stops, targets beyond ~6 ATRs from the decision
    # point almost never print before the stop does. Configurable because it
    # is a judgment call, not a market law.
    max_tp_atr: float = 6.0

    # Price within this many ATRs of the next TP/SL counts as "approaching"
    # and triggers a reevaluation event. Rationale: 0.5 ATR on 15m is close
    # enough to matter, far enough to avoid evaluating every bar.
    proximity_atr: float = 0.5

    # Stop-tightening guard: a proposed stop must leave at least this much
    # adverse room in ATR units. Rationale: tighter is NOT automatically
    # better — a stop inside normal noise just manufactures premature exits.
    min_stop_dist_atr: float = 0.25

    # Structural buffer when deriving a stop from a swing level (0.1%).
    # Rationale: matches DonchianBreakoutStrategy.breakout_buffer.
    swing_buffer: float = 0.001

    # ── Plan-stability gate (anti-churn) ──────────────────────────
    # Rationale (from bounded eval v1: one trade accumulated 15 plan
    # versions; repeated sub-noise re-plans bled the position): an
    # adjustment must clear BOTH a materiality bar and a cooldown before
    # RiskGuard even sees it. Safety actions (CLOSE/INVALIDATE) always
    # bypass the gate — stability must never delay an exit.
    #
    # A stop move smaller than this (in ATRs) is noise by the same scale
    # the stop worker itself uses (min_stop_dist_atr): re-planning inside
    # normal wobble manufactures premature exits, never edge.
    min_stop_move_atr: float = 0.25
    # TP moves sacrifice upside, so they clear a wider bar than stops.
    min_tp_move_atr: float = 0.5
    # Ladder rungs must stay at least this far apart (in ATRs). Rationale:
    # the evaluator settles 50/25/25 tranches in tp1->tp2->tp3 order; a
    # flattened ladder (tp1==tp2==tp3) destroys the tranche structure the
    # whole accounting assumes. If spacing cannot be preserved within the
    # economics floor, the worker proposes nothing.
    min_tp_gap_atr: float = 1.0
    # Minimum time between applied plan adjustments per signal (4h).
    # Rationale: baseline mean holding is ~7.5h; a 4h cooldown allows ~1-2
    # deliberate interventions per trade while blocking every-bar churn.
    # Applies to TIGHTEN/ADJUST/REDUCE only — never to CLOSE/INVALIDATE.
    min_adjust_interval_s: int = 14400

    # Adjusted TP1 must keep at least this gross R multiple and positive net R
    # after costs. Rationale: pulling a target closer trades reward for
    # realism; below 1R gross the "target" no longer compensates the risk.
    min_adjusted_tp1_rr: float = 1.0

    # Weakening rule: this many of the last N closes against the direction
    # marks momentum deterioration (evidence only, never standalone exit).
    # Rationale: 4-of-5 is a supermajority that rarely occurs by chop alone.
    weak_trend_bars: int = 5
    weak_trend_min_against: int = 4

    # Risk/Reward worker: propose REDUCE when remaining net R to the next
    # target falls below this. Rationale: below ~0.5R the upside no longer
    # justifies full exposure; de-risk instead of hoping.
    min_remaining_rr: float = 0.5

    # Fraction of remaining paper exposure a REDUCE action sheds.
    # Rationale: half-off is the same de-risk quantum as the TP1 tranche.
    reduce_fraction: float = 0.5

    # Fee/slippage mirror of SignalOutcomeEvaluator defaults
    # (taker_fee_pct=0.0004, slippage_pct=0.0002, two round-trip legs).
    # A unit test pins these to the evaluator's constructor defaults so any
    # upstream change breaks loudly instead of drifting silently.
    taker_fee_pct: float = 0.0004
    slippage_pct: float = 0.0002
    fee_legs: int = 2

    # Scheduler integration bounds.
    min_reeval_s: int = 300       # fastest repeat evaluation per signal
    max_reeval_s: int = 3600      # scheduled reevaluation even with no event
    max_evals_per_cycle: int = 20  # hard cap on worker runs per scan cycle
    klines_limit: int = 60        # candles fetched per supervised symbol

    # Ledger caps (bounded disk growth).
    max_decisions_kept: int = 2000

    # Harness determinism.
    bootstrap_seed: int = 42
    bootstrap_iters: int = 2000

    storage_dir: str = "data_store"
    ledger_name: str = "exit_decisions.json"


def load_exit_config() -> ExitConfig:
    """Build an ExitConfig, honouring EXITS_* environment overrides."""
    return ExitConfig(
        max_data_age_s=_getenv_int("EXITS_MAX_DATA_AGE_S", 1800),
        min_reeval_s=_getenv_int("EXITS_MIN_REEVAL_S", 300),
        max_reeval_s=_getenv_int("EXITS_MAX_REEVAL_S", 3600),
        max_evals_per_cycle=_getenv_int("EXITS_MAX_EVALS_PER_CYCLE", 20),
        storage_dir=os.getenv("EXITS_STORAGE_DIR", "data_store"),
    )


def config_version(cfg: ExitConfig) -> str:
    """Short deterministic hash identifying the config in ledger records."""
    import hashlib
    import json
    from dataclasses import asdict
    raw = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
