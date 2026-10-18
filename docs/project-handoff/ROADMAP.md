# ROADMAP.md — prioritized from verified state

Companion to [CURRENT_STATE.md](CURRENT_STATE.md). Goals: (1) reliable autonomous Telegram signals, (2) auditable deterministic risk, (3) exits smarter than static TP/SL, (4) rigorous evidence before exits become authoritative, (5) minimal cost / no paid LLM, (6) paper-only before any real-money thought.

---

## P0 — correctness, safety, operational blockers

- **P0.1 Fix live scoring-data bugs (D1+D2).** Why: OI SHORT scoring and momentum credit are wrong on every live scan NOW. Modules: `src/pipeline.py` (pass real trend / set `price_trend`), `src/engines/scoring_engine.py` unchanged. Tests: SHORT OI confirm/reject unit cases; momentum match/mismatch cases; pipeline-level trend-wiring test. Changes live behavior (scores → selection) — announce, watch 2–3 Telegram cycles. Approval: yes (live behavior).
- **P0.2 Verify Railway env flags.** Why: real-money paths exist behind flags; repo cannot prove prod is paper-only. Check `MIMIC_LIVE`, `INVO_ENABLED`, `INVO_PAPER_DRY_RUN`, `EXITS_APPLY` on both services via dashboard/cloud (read-only). No code change. Approval: no.
- **P0.3 Fix deploy path.** Why: GitHub push did not auto-deploy 2026-10-13; stale-prod risk. Either repair webhook/watch patterns or document `railway up` as the deploy runbook (+ verify deployed SHA in logs on each release). Modules: none (ops). Approval: yes before any prod change.
- **P0.4 Retire or repair RSI 1.5R (D4).** Why: dead strategy misleads menus and wastes scan compute. Options: remove from priority lists or parameterize TP to 2.0. Modules: `src/strategies/rsi_mean_reversion.py`, `src/config.py`, tests. Changes live behavior. Approval: yes.

## P1 — next highest value

- **P1.1 Exit-integration decision (the gap).** Why: supervision currently cannot move live paper PnL; decide explicitly: (a) consume `active_plan` in `OutcomeTracker` (paper evaluation follows supervised levels), or (b) keep monitor-only + Invo-sync as the only actuator. Prerequisites: P0.2, V3 eval results (below). Modules: `src/tracking/outcome_tracker.py`, `src/backtest/evaluator.py` (plan-aware mode, default off), `src/exits/ledger.py` queries. Acceptance: plan-aware paper ledger + dashboard column + equivalence vs baseline when plan static. Changes paper behavior only. Approval: yes.
- **P1.2 Exit eval V3 with adequate power.** Why: V2 inconclusive (CI spans zero). Pre-register: wider window (add 2024–2025 if DB allows, else 4h-step denser scans on same window), same verdict rule (Δ>0, CI excludes zero), no tuning. Modules: `scripts/eval_exits_v1.py` params only. No live change. Approval: no (compute only).
- **P1.3 Surface exits in dashboard.** Why: `linked_outcome` + active plans + decision counts are unauditable in UI. Modules: `src/dashboard.py` (read `exit_decisions.json`), no behavior change. Approval: no.

## P2 — validation & integration

- **P2.1 Real walk-forward + non-degenerate robustness.** `tuner.walk_forward_evaluate` exists; `walk_forward.md`/`robustness.md` are empty. Run bounded, report CIs.
- **P2.2 Coin-specific exact matches.** Matrix has 0/42 exact; promote top-coin winners (momentum BTC/SOL/XRP/BNB/DOGE) to per-symbol params behind a flag, validated out-of-sample.
- **P2.3 DATA_MISSING universe expansion.** 6 coins (incl. UNI/LINK/HYPE, all mirror-traded) have no validated history; backfill + validate before live promotion.
- **P2.4 Baseline/harness parity.** Reconcile single-TP-full-close vs 50/25/25 text; pin one semantic in both evaluator and harness; document.
- **P2.5 Engine consolidation.** Wire `EntryEngine`/`StopLossEngine`/`TakeProfitEngine` into pipeline or delete (kill D5 dual truth).

## P3 — optional advanced

- Break-even/trailing in baseline evaluator parity; Jesse-export validation run; multi-TF strategy entries beyond 15m; funding-aware PnL; multi-coin portfolio risk (currently fixed $10/trade, no compounding by design).

---

## Recommended next task (ONE): P0.1 — fix the two scoring-data bugs

**Why first:** they corrupt every live signal selection today; the fix is small, fully testable, and unblocks trustworthy P1 exit work (supervision quality depends on candidate quality). It outranks P1.1/P1.2 because evidence built on mis-scored candidates is suspect, and outranks ops items because no money/positions are at acute risk (paper-only defaults verified in code).

**Prerequisites:** none (code + tests only). Read `src/pipeline.py` OI/scoring call sites, `src/engines/oi_engine.py:79-85`, `src/engines/scoring_engine.py:32-62`.

**Out of scope:** weight re-tuning, RSI fix (P0.4), any exit changes, any deploys, any backtests beyond targeted unit checks.

**Smallest useful change:**
1. Compute real `price_trend` for the OI window (sign of close change over the OI span) and pass it to `analyze_oi`/`should_reject_candidate` for both LONG and SHORT.
2. Set candidate `price_trend` from the 15m trend already computed per strategy before calling `score_signal`.
3. Add unit tests: OI SHORT confirm/reject/divergence; momentum side-match full vs half credit; one pipeline test asserting trend flows into scoring.

**Acceptance:** new tests pass; full suite passes (`pytest tests/ -q`); score outputs on 2–3 fixture candles change only in the documented direction (SHORT OI now discriminates; momentum match earns full credit).

**Rollback / safe-disable:** single revertible commit; live behavior changes on next scan — monitor next 2–3 Telegram cycles and `data_store/scan_history.json`; revert + `railway up` if selection degrades. **Do not perform this task now** — description only.

**Approval required:** yes (changes live signal selection).
