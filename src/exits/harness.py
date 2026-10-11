"""Candidate harness — baseline-vs-orchestrator comparison (offline).

Stage-2 deliverable for spec §6; NO large experiment is launched here.
The harness READS historical klines and existing signals only.

Design:
- Baseline arm: untouched SignalOutcomeEvaluator (same file, same defaults).
- Candidate arm: CandidateEvaluator below — a line-by-line mirror of the
  baseline loop with ONE difference: SL/TPs are read from the per-bar active
  plan, which the ExitSupervisor may change at bar closes using strictly
  history-only data (no look-ahead by construction: the decision for bar i
  sees only bars < i; reduce/close events execute at bar i's OPEN).
- Equivalence guard: with a static original plan and no events, candidate
  output is asserted bit-identical to baseline output (unit-tested on
  fixtures covering wins, losses, BE stops, trailing stops, expiry,
  ambiguous candles, and liquidation).
- One documented semantic difference: the BE-after-TP1 / trail-after-TP2
  overlay references the TP1 in effect at fill time (with a static plan this
  is identical to baseline).

Fee/slippage/ambiguity settings default to the baseline's constructor
defaults; a unit test pins them equal so upstream changes break loudly.
"""
from typing import Any, Callable, Dict, List, Optional

from src.backtest.evaluator import AmbiguousResolution, SignalOutcomeEvaluator
from src.exits import policy
from src.exits.config import ExitConfig
from src.exits.models import ExitPlan, PositionState


def _default_baseline() -> SignalOutcomeEvaluator:
    return SignalOutcomeEvaluator()


class CandidateEvaluator:
    """Baseline mirror with supervisor-driven per-bar plans."""

    def __init__(
        self,
        max_entry_bars: int = 6,
        max_holding_bars: int = 100,
        ambiguous_resolution: AmbiguousResolution = AmbiguousResolution.CONSERVATIVE,
        taker_fee_pct: float = 0.0004,
        slippage_pct: float = 0.0002,
    ):
        self.max_entry_bars = max_entry_bars
        self.max_holding_bars = max_holding_bars
        self.ambiguous_resolution = ambiguous_resolution
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct

    # ── plan provider ───────────────────────────────────────────
    # decide_fn(signal, plan: ExitPlan, fill: dict, history: list[klines],
    #           data_ts_ms: int) -> (ExitPlan|None, events: list)
    # history contains ONLY bars strictly before the bar being processed.
    # events: [{"kind": "reduce"|"close", "fraction": float}] executed at the
    # current bar's OPEN (intrabar path unknown — open is the only price known
    # at decision time, documented conservative choice).

    def evaluate_signal(
        self,
        signal: Dict[str, Any],
        subsequent_klines: List[Dict[str, Any]],
        decide_fn: Optional[Callable] = None,
        context_klines: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        # context_klines: pre-signal completed bars (worker history only —
        # entry search and event processing use subsequent_klines exclusively).
        # Mirrors live, where the monitor fetches history including pre-signal
        # bars. Without context, early-trade supervision abstains on thin data.
        from src.papertrade.liquidation import estimate_liq_price

        side = signal["side"]
        context = list(context_klines or [])
        offset = len(context)
        combined = context + list(subsequent_klines)
        entry_target = float(signal["entry"])
        base_plan = ExitPlan.genesis(signal)
        risk_distance = abs(entry_target - base_plan.stop_loss)

        liq_price, liq_loss_r = self._liq(signal, entry_target, risk_distance)

        # ── Step 1: entry (IDENTICAL to baseline) ───────────────
        entry_triggered = False
        actual_entry_price = None
        entry_timestamp = None
        entry_bar_idx = None
        check_limit = min(len(subsequent_klines), self.max_entry_bars)
        for i in range(check_limit):
            k = subsequent_klines[i]
            k_low, k_high = float(k["low"]), float(k["high"])
            if side == "LONG":
                if k_low <= entry_target:
                    entry_triggered, actual_entry_price = True, entry_target
                    entry_timestamp, entry_bar_idx = k.get("timestamp"), i
                    break
            else:
                if k_high >= entry_target:
                    entry_triggered, actual_entry_price = True, entry_target
                    entry_timestamp, entry_bar_idx = k.get("timestamp"), i
                    break
        if not entry_triggered:
            out = self._expired(signal, liq_price, check_limit)
            out["plan_changes"] = 0
            out["supervisor_events"] = []
            return out

        # ── Step 2: active progression with per-bar plans ───────
        active = subsequent_klines[entry_bar_idx:][: self.max_holding_bars]
        tp1_hit = tp2_hit = tp3_hit = sl_hit = liquidated = False
        first_event = None
        mfe = mae = 0.0
        active_sl = base_plan.stop_loss
        plan = base_plan
        plan_changes = 0
        events_log: List[Dict[str, Any]] = []
        seen_plan_hashes = {(plan.stop_loss, plan.tp1, plan.tp2, plan.tp3)}
        holding_bars = 0
        remaining_position = 1.0
        realized_r = 0.0
        supervisor_closed = False

        for rel, k in enumerate(active):
            abs_idx = entry_bar_idx + rel
            holding_bars = rel + 1
            k_open = float(k["open"])
            k_high = float(k["high"])
            k_low = float(k["low"])

            # Supervisor consult: history strictly before this bar
            # (pre-signal context + subsequent bars so far).
            if decide_fn is not None and rel > 0:
                history = combined[: offset + abs_idx]
                data_ts = self._bar_close_ms(subsequent_klines[abs_idx - 1])
                fill = {"tp1_hit": tp1_hit, "tp2_hit": tp2_hit,
                        "remaining": remaining_position,
                        "holding_bars": holding_bars - 1}
                try:
                    new_plan, events = decide_fn(signal, plan, fill, history, data_ts) or (None, [])
                except Exception:
                    new_plan, events = None, []
                if new_plan is not None:
                    key = (new_plan.stop_loss, new_plan.tp1, new_plan.tp2, new_plan.tp3)
                    if key not in seen_plan_hashes:
                        seen_plan_hashes.add(key)
                        plan = new_plan
                        plan_changes += 1
                        events_log.append({"type": "plan", "at_bar": abs_idx,
                                           "plan": plan.to_dict()})
                for ev in events or []:
                    events_log.append({"type": ev.get("kind"), "at_bar": abs_idx,
                                       "detail": ev})
                    if ev.get("kind") == "reduce" and remaining_position > 0:
                        frac = min(max(float(ev.get("fraction", 0.5)), 0.0), 1.0)
                        rr_now = self._r_at_price(k_open, actual_entry_price,
                                                  risk_distance, side)
                        realized_r += remaining_position * frac * rr_now
                        remaining_position *= (1.0 - frac)
                        if first_event is None:
                            first_event = "REDUCE"
                    elif ev.get("kind") == "close" and remaining_position > 0:
                        rr_now = self._r_at_price(k_open, actual_entry_price,
                                                  risk_distance, side)
                        realized_r += remaining_position * rr_now
                        remaining_position = 0.0
                        supervisor_closed = True
                        if first_event is None:
                            first_event = "CLOSE"
                if supervisor_closed or remaining_position <= 0:
                    break

            sl, tp1, tp2, tp3 = plan.stop_loss, plan.tp1, plan.tp2, plan.tp3

            if side == "LONG":
                bar_favorable = max(0.0, k_high - actual_entry_price)
                bar_adverse = max(0.0, actual_entry_price - k_low)
            else:
                bar_favorable = max(0.0, actual_entry_price - k_low)
                bar_adverse = max(0.0, k_high - actual_entry_price)
            mfe = max(mfe, bar_favorable)
            mae = max(mae, bar_adverse)

            if liq_price is not None and liq_loss_r is not None:
                liq_touched = (k_low <= liq_price) if side == "LONG" else (k_high >= liq_price)
            else:
                liq_touched = False
            if liq_touched:
                liquidated = True
                if first_event is None:
                    first_event = "LIQ"
                realized_r = liq_loss_r
                remaining_position = 0.0
                break

            next_target = tp3 if tp2_hit else (tp2 if tp1_hit else tp1)
            if side == "LONG":
                target_touched = k_high >= next_target
                sl_touched = k_low <= active_sl
            else:
                target_touched = k_low <= next_target
                sl_touched = k_high >= active_sl

            if target_touched and sl_touched:
                target_first = self._resolve_ambiguous(k_open, next_target, active_sl, side)
            else:
                target_first = target_touched

            if target_first and target_touched:
                if not tp1_hit:
                    tp1_hit = True
                    if first_event is None:
                        first_event = "TP1"
                    rr_tp1 = abs(tp1 - actual_entry_price) / risk_distance
                    realized_r += 0.50 * rr_tp1
                    remaining_position -= 0.50
                    active_sl = actual_entry_price
                elif not tp2_hit:
                    tp2_hit = True
                    if first_event is None:
                        first_event = "TP2"
                    rr_tp2 = abs(tp2 - actual_entry_price) / risk_distance
                    realized_r += 0.25 * rr_tp2
                    remaining_position -= 0.25
                    active_sl = tp1
                elif not tp3_hit:
                    tp3_hit = True
                    if first_event is None:
                        first_event = "TP3"
                    rr_tp3 = abs(tp3 - actual_entry_price) / risk_distance
                    realized_r += 0.25 * rr_tp3
                    remaining_position -= 0.25
                    break
            elif sl_touched:
                sl_hit = True
                if first_event is None:
                    first_event = "SL"
                if remaining_position == 1.0:
                    realized_r = -1.0
                else:
                    if active_sl == actual_entry_price:
                        realized_r += 0.0
                    else:
                        trailing_rr = (
                            (active_sl - actual_entry_price) / risk_distance
                            if side == "LONG"
                            else (actual_entry_price - active_sl) / risk_distance
                        )
                        realized_r += remaining_position * trailing_rr
                remaining_position = 0.0
                break

        mfe_r = round(mfe / risk_distance, 2) if risk_distance > 0 else 0.0
        mae_r = round(mae / risk_distance, 2) if risk_distance > 0 else 0.0
        total_costs_pct = (2 * self.taker_fee_pct) + (2 * self.slippage_pct)
        cost_in_price = actual_entry_price * total_costs_pct
        fees_cost_r = round(cost_in_price / risk_distance, 3) if risk_distance > 0 else 0.0
        net_realized_r = round(realized_r - fees_cost_r, 3)

        if supervisor_closed or (remaining_position == 0 and not (tp1_hit or tp2_hit or tp3_hit or sl_hit or liquidated)):
            status = "SUPERVISOR_CLOSE"
        else:
            status = "STOPPED_OUT" if sl_hit and remaining_position == 0 and not tp1_hit else (
                "TP3_HIT" if tp3_hit else (
                    "TP2_HIT" if tp2_hit else (
                        "TP1_HIT" if tp1_hit else "ACTIVE")))
        if liquidated:
            status = "LIQUIDATED"

        return {
            "signal_id": signal.get("signal_id"), "symbol": signal.get("symbol"),
            "side": side, "entry_triggered": True, "expired": False,
            "first_event": first_event or "TIMEOUT",
            "actual_entry_price": actual_entry_price,
            "entry_timestamp": entry_timestamp,
            "candles_to_entry": entry_bar_idx,
            "tp1_hit": tp1_hit, "tp2_hit": tp2_hit, "tp3_hit": tp3_hit,
            "sl_hit": sl_hit, "liquidated": liquidated, "liq_price": liq_price,
            "mfe": round(mfe, 6), "mae": round(mae, 6),
            "mfe_r": mfe_r, "mae_r": mae_r, "holding_bars": holding_bars,
            "realized_r": round(realized_r, 3), "net_realized_r": net_realized_r,
            "fees_cost_r": fees_cost_r, "status": status,
            "plan_changes": plan_changes, "supervisor_events": events_log,
        }

    # ── shared-shape helpers (same formulas as baseline) ────────
    def _liq(self, signal, entry_target, risk_distance):
        from src.papertrade.liquidation import estimate_liq_price
        liq_price = liq_loss_r = None
        try:
            _lev, _margin, _notional = (signal.get("leverage"), signal.get("margin"),
                                        signal.get("position_notional"))
            if _lev is not None and (_margin is not None or _notional is not None):
                liq_price = estimate_liq_price(entry_target, signal["side"], float(_lev))
                _notional = float(_notional) if _notional is not None else float(_margin) * float(_lev)
                _qty = _notional / entry_target if entry_target else 0.0
                _risk_usd = risk_distance * _qty
                if liq_price is not None and _risk_usd > 0:
                    liq_loss_r = round(-float(_margin if _margin is not None else _notional / float(_lev)) / _risk_usd, 3)
        except (TypeError, ValueError, ZeroDivisionError):
            liq_price, liq_loss_r = None, None
        return liq_price, liq_loss_r

    def _expired(self, signal, liq_price, check_limit):
        return {
            "signal_id": signal.get("signal_id"), "symbol": signal.get("symbol"),
            "side": signal["side"], "entry_triggered": False, "expired": True,
            "first_event": "EXPIRED", "actual_entry_price": None,
            "entry_timestamp": None, "candles_to_entry": check_limit,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "sl_hit": False, "liquidated": False, "liq_price": liq_price,
            "mfe": 0.0, "mae": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
            "holding_bars": 0, "realized_r": 0.0, "net_realized_r": 0.0,
            "fees_cost_r": 0.0, "status": "EXPIRED",
        }

    @staticmethod
    def _r_at_price(px, entry, risk_distance, side):
        if risk_distance <= 0:
            return 0.0
        move = (px - entry) if side == "LONG" else (entry - px)
        return move / risk_distance

    @staticmethod
    def _bar_close_ms(k):
        try:
            return int(float(k.get("close_time", k.get("timestamp", 0))))
        except (TypeError, ValueError):
            return 0

    def _resolve_ambiguous(self, candle_open, target_price, sl_price, side):
        if self.ambiguous_resolution == AmbiguousResolution.CONSERVATIVE:
            return False
        if self.ambiguous_resolution == AmbiguousResolution.OPTIMISTIC:
            return True
        if self.ambiguous_resolution == AmbiguousResolution.OPEN_PROXIMITY:
            return abs(candle_open - target_price) < abs(candle_open - sl_price)
        return False


# ── comparison metrics ──────────────────────────────────────────
def arm_metrics(results: List[Dict[str, Any]], seed: int = 42,
                iters: int = 2000) -> Dict[str, Any]:
    """Aggregate metrics over evaluated trades. Deterministic bootstrap CI."""
    trig = [r for r in results if r.get("entry_triggered")]
    nets = [float(r.get("net_realized_r", 0.0)) for r in trig]
    n = len(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = round(gross_w / gross_l, 3) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
    exp = round(sum(nets) / n, 4) if n else 0.0
    curve, peak, mdd = 0.0, 0.0, 0.0
    for x in nets:
        curve += x
        peak = max(peak, curve)
        mdd = max(mdd, peak - curve)
    ci = _bootstrap_ci(nets, seed, iters) if n >= 10 else {"lo": None, "hi": None,
                                                          "note": "n<10: CI withheld"}
    return {
        "trades": len(results), "triggered": n,
        "expectancy_net_r": exp, "total_net_r": round(sum(nets), 3),
        "profit_factor": pf, "max_drawdown_r": round(mdd, 3),
        "win_rate": round(len(wins) / n, 3) if n else 0.0,
        "avg_win_r": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "stop_freq": round(sum(1 for r in trig if r.get("first_event") == "SL") / n, 3) if n else 0.0,
        "tp1_rate": round(sum(1 for r in trig if r.get("tp1_hit")) / n, 3) if n else 0.0,
        "tp2_rate": round(sum(1 for r in trig if r.get("tp2_hit")) / n, 3) if n else 0.0,
        "tp3_rate": round(sum(1 for r in trig if r.get("tp3_hit")) / n, 3) if n else 0.0,
        "exposure_mean_bars": round(sum(r.get("holding_bars", 0) for r in trig) / n, 1) if n else 0.0,
        "expectancy_ci95": ci,
    }


def _bootstrap_ci(xs: List[float], seed: int, iters: int) -> Dict[str, Any]:
    import random
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(iters))
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters) - 1]
    return {"lo": round(lo, 4), "hi": round(hi, 4),
            "method": f"percentile bootstrap, seed={seed}, iters={iters}"}


def make_decide_fn(
    cfg: ExitConfig,
    workers: Optional[List] = None,
    disabled: frozenset = frozenset(),
):
    """Build the history-only decide_fn used by the candidate arm.

    decide_fn(signal, plan, fill, history, data_ts) -> (new_plan|None, events).
    history holds ONLY bars strictly before the bar under evaluation
    (look-ahead prevention is structural: the caller truncates). Disabled
    worker ids are replaced by abstain stubs for ablation runs.
    """
    from src.exits.models import ExitPlan, PositionState
    from src.exits.risk_guard import RiskGuard
    from src.exits.supervisor import ExitSupervisor
    from src.exits.workers import ALL_WORKERS, WorkerAssessment, WorkerCtx

    # NOTE: worker classes expose ID as a class attribute.
    inst = []
    for wcls in (workers or ALL_WORKERS):
        if getattr(wcls, "ID", "") in disabled:
            inst.append(None)
        else:
            inst.append(wcls())
    sup = ExitSupervisor(cfg)
    seen: set = set()
    # Last applied intervention (plan change or reduce/close event) in
    # simulated ms. REDUCE/CLOSE leave plan levels untouched, so without
    # this the cooldown clock would never start on event-only decisions
    # and every-bar repeats would slip through (observed in eval v1).
    intervention: Dict[str, int] = {"ts": 0}

    def abstain(wid, ctx):
        return WorkerAssessment(worker_id=wid, worker_version="ablated",
                                assessed_at_ms=0, data_ts_ms=ctx.data_ts_ms,
                                verdict="ABSTAIN",
                                uncertainty="ablated for contribution analysis",
                                evidence_quality=0.0)

    def decide_fn(signal, plan, fill, history, data_ts):
        # Decision "at" the close of the last history bar.
        now_ms = data_ts + cfg.timeframe_ms
        # decide_fn is consulted only post-entry, so state is OPEN/PARTIAL.
        # (PENDING setups never reach the candidate loop's consult point —
        #  entry-phase logic is baseline-identical, including expiry.)
        if fill.get("tp1_hit") or fill.get("tp2_hit"):
            state = PositionState.PARTIAL
        else:
            state = PositionState.OPEN
        from src.exits import policy as _policy
        fresh, reason, dts = _policy.check_freshness(history, now_ms, cfg)
        # Fill state doubles as the outcome record so workers that exclude
        # already-hit targets (Target, RiskReward) see partial fills.
        outcome = {"tp1_hit": bool(fill.get("tp1_hit")),
                   "tp2_hit": bool(fill.get("tp2_hit"))}
        ctx = WorkerCtx(signal, plan.stop_loss, [plan.tp1, plan.tp2, plan.tp3],
                        state, history, now_ms, cfg, data_ts_ms=dts or data_ts,
                        outcome=outcome, fresh=fresh, freshness_reason=reason)
        assessments = []
        for w in inst:
            if w is None:
                continue
            assessments.append(w.assess(ctx))
        for wid in disabled:
            assessments.append(abstain(wid, ctx))
        from src.exits.models import PositionView
        view = PositionView(signal, None, state, plan, False)
        dec = sup.decide(view, assessments, ctx, now_ms,
                         seen_keys=frozenset(seen), last_data_ts_ms=0,
                         last_change_ts_ms=intervention["ts"])
        seen.add(dec.decision_key)
        new_plan = ExitPlan.from_dict(dec.plan_after) if dec.plan_after else None
        if new_plan is not None or dec.events_applied:
            intervention["ts"] = now_ms
        return new_plan, dec.events_applied

    return decide_fn


def compare_arms(
    signals: List[Dict[str, Any]],
    klines_map: Dict[str, List[Dict[str, Any]]],
    decide_fn_factory: Callable[..., Callable],
    cfg: Optional[ExitConfig] = None,
    max_signals: Optional[int] = None,
    ablate_workers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Baseline vs candidate on IDENTICAL signals/klines/assumptions.

    decide_fn_factory(disabled=frozenset()) builds a fresh decide_fn (fresh
    idempotency state) per run. ablate_workers reruns the candidate with each
    listed worker replaced by an abstain stub. Bounded by max_signals.
    Read-only w.r.t. repo data.
    """
    cfg = cfg or ExitConfig()
    base_ev = _default_baseline()
    cand_ev = CandidateEvaluator()
    signals = signals[:max_signals] if max_signals else signals

    def run_one(factory, disabled=frozenset()):
        base_rows, cand_rows = [], []
        for sig in signals:
            full = sorted(klines_map.get(sig.get("symbol", ""), []),
                          key=lambda k: float(k.get("timestamp", 0)))
            sig_ts = _sig_ts_ms(sig)
            kl = [k for k in full if float(k.get("timestamp", 0)) > sig_ts]
            # Worker context: up to 60 pre-signal completed bars (history only;
            # never part of entry search or event processing).
            ctx_bars = [k for k in full if float(k.get("timestamp", 0)) <= sig_ts][-60:]
            base_rows.append(base_ev.evaluate_signal(sig, kl))
            cand_rows.append(cand_ev.evaluate_signal(sig, kl, factory(disabled=disabled),
                                                     context_klines=ctx_bars))
        return base_rows, cand_rows

    base_rows, cand_rows = run_one(decide_fn_factory)
    out: Dict[str, Any] = {
        "n_signals": len(signals),
        "baseline": arm_metrics(base_rows, cfg.bootstrap_seed, cfg.bootstrap_iters),
        "candidate": arm_metrics(cand_rows, cfg.bootstrap_seed, cfg.bootstrap_iters),
        "trades": [
            {"signal_id": b.get("signal_id"), "symbol": b.get("symbol"),
             "base_net_r": b.get("net_realized_r"), "base_status": b.get("status"),
             "cand_net_r": c.get("net_realized_r"), "cand_status": c.get("status"),
             "plan_changes": c.get("plan_changes"),
             "cand_events": c.get("supervisor_events")}
            for b, c in zip(base_rows, cand_rows)
        ],
        "change_stats": {
            "changed": sum(1 for c in cand_rows if c.get("plan_changes")),
            "reduces": sum(1 for c in cand_rows for e in c.get("supervisor_events", [])
                           if e.get("type") == "reduce"),
            "closes": sum(1 for c in cand_rows for e in c.get("supervisor_events", [])
                          if e.get("type") == "close"),
        },
    }
    out["delta_expectancy"] = round(
        out["candidate"]["expectancy_net_r"] - out["baseline"]["expectancy_net_r"], 4)
    if ablate_workers:
        out["ablation"] = {}
        for wid in ablate_workers:
            _, a_rows = run_one(decide_fn_factory, disabled=frozenset([wid]))
            out["ablation"][f"without_{wid}"] = arm_metrics(
                a_rows, cfg.bootstrap_seed, cfg.bootstrap_iters)
    return out


def _sig_ts_ms(sig: Dict[str, Any]) -> int:
    from datetime import datetime
    ts = sig.get("timestamp", 0)
    if isinstance(ts, (int, float)):
        return int(ts) if ts > 100_000_000_000 else int(ts * 1000)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0
