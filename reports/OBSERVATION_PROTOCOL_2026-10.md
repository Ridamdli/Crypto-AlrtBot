# 30-Day Observation Protocol (2026-10)

**Created:** 2026-10-22. **Status:** **Window 1 CLOSED EARLY on 2026-10-24 by operator
decision.** Window 2 (post-deploy) is defined in §7 and starts on the deploy-success date.
Pre-registered companion: `reports/PRE_REGISTERED_HYPOTHESES_2026-10.md` (H1–H4).
**Decision it feeds:** Phase 04B/04C direction. It is not a profitability trial.

## Window 1 closure (2026-10-24) — read before anything else

**Termination.** Operator decision, 2026-10-24: the window is terminated early because the
running system is losing capital while the fixes are already diagnosed and committed.
Continuing a 30-day observation on a known-broken system spends capital to re-measure what
`reports/W_LIVE_AUDIT_2026-10-24.md` already measured.

**What the 2.8 days are.** 2026-10-22 → 2026-10-24: 59 published signals, **49 settled**
(Measured). Archived as a **pre-deploy baseline**, not a 30-day result. Population: one regime
(published bearish 44 / neutral 13 / bullish 2), UNIUSDT-dominated exits.

**H1–H4 status: PAUSED / INCONCLUSIVE — not failed.** All four were mid-window readings against
a **5-family** system production should not have been running (see the deploy-lag row below):

| H | Reading at closure (Measured, n=49) | Status |
|---|---|---|
| H1 gate 65 vs 60 | gate 65 n=35 mean **−0.000R**; 60–64 band n=14 mean **−0.552R** | **Paused** — failure-by-sample at n=35; the n ≥ 50 gate was never reached |
| H2 regime filter | aligned n=36 mean **+0.051R** vs all −0.158R | **Paused** — n=36 against a required n ≥ 80; the total-R condition is degenerate when the unfiltered total is negative |
| H3 side-opposed | **0 opposed signals** | **Untestable in Window 1** — no population |
| H4 regime-only scoring | `scoring_breakdown` absent from every production signal | **Untestable pre-deploy** — becomes Measurable in Window 2 |

**The post-window decision tree is not applied.** No branch is invoked and no gate, filter,
strategy or exit change is authorized by it. The tree below stays frozen verbatim and is
re-pointed at Window 2 in §7.

**Why these numbers are not a verdict on the new system.** **89%** of Window 1's net loss
(−6.896R of −7.736R) came from `ema_trend_20_50` — already retired locally (`a29831a`) and
still live in production (deploy lag, backlog item 11). The deploy removes it, so Window 2
measures a different system and must not inherit Window 1's means.

## Scope (read first)

**Window 1** measured the **OLD production system** (revision pre-`49ff810`; 61 commits since
the last confirmed production reference `f2dfde8`, at least 32 since `49ff810`). It did **NOT**
test:

- RSI / `ema_trend` retirements
- Momentum lookahead fix
- P2 invariant
- Persistence fixes
- W6 `record_failed` fallback
- W-DD `scoring_breakdown` persistence (local only until deploy)

What it **DOES** measure:

- **H1** (gate 65 vs 60): real signals from old scoring
- **H2** (regime filter): real signals from old regime weighting
- **H3** (side-opposed): real signals from old regime alignment
- **H4** (regime-only vs full): **DEGRADED** — component breakdown is Estimated, not recorded

**Post-window decision tree** (pre-registered 2026-10-22; do not modify after seeing results):

```
IF H1 succeeds (gate 65 mean >= +0.20R, n>=100):
  -> Recommend deploy: local fixes + gate 65
IF H2 succeeds (regime-filtered mean >= +0.28R, n>=80, total R >= 0.9x unfiltered):
  -> Recommend deploy: local fixes + regime filter
IF H3 succeeds (side-opposed mean < aligned by >=0.3R):
  -> Recommend deploy: local fixes + fix regime_filter (make non-inert)
IF H3 fails (opposed >= aligned):
  -> Recommend deploy: local fixes + REMOVE regime_filter (dead code)
IF every branch above is inconclusive (and no strong fail):
  -> Recommend deploy: local fixes only. Re-observe 30 days on NEW system.
IF strong fail (CI lower < -0.10 OR median < -0.30):
  -> Recommend halt new entries. Rebuild or abandon.
```

**Note:** "Recommend deploy" is a recommendation, and the tree above stays frozen verbatim. **Deploy
authorization was granted separately on 2026-10-24** (Window 1 closure block) — it did not come from
this tree. The window's exposure is $16 of real money on old strategy behaviour — an expected outcome
is *measurable but noisy*, not decisive.

## 1. Window 1 (closed early — 2026-10-24)

| Item | Value |
|---|---|
| Start | **2026-10-22** (date of this document). No deploy was authorized, so the window began on the **then-current production run** — signals already publishing from the running revision. |
| Planned end | **2026-11-21** (start + 30 calendar days) **OR** the moment the settled population reached **n = 200**, whichever came first. |
| **Actual end** | **2026-10-24 — day 3 of 30**, by operator decision (closure block above). 49 settled of a planned 100 minimum. |
| Minimum useful n | **100** settled signals. Below that, every hypothesis is Inconclusive **by sample** and the window extends rather than concludes. |
| Counting rule | A signal enters the population only when its outcome is **settled** with a recorded `net_r`. Open, unsettled, and EXPIRED-without-fill rows are excluded — never imputed. |

**Deploy caveat.** See *Scope (read first)*: this window measures the running (pre-`49ff810`) code, so window signals
will not carry `scoring_breakdown` and any per-component number is reconstructed and labeled **Estimated**. That cost
is accepted and does not block H1–H3, which read `confidence` and outcomes only.

## 2. Primary metrics (per signal, per arm)

Reported for the whole population and for each arm the hypotheses need (gate 60, gate 65, aligned, neutral, opposed):

| Metric | Definition |
|---|---|
| Mean net R | Arithmetic mean of `net_r` — tail-sensitive, always paired with median |
| Median net R | Median `net_r` — the tail-insensitive read |
| CI 95% lower | Bootstrap lower bound on the mean (10,000 resamples, fixed seed 20261022) |
| Top-5 concentration | Share of total positive R from the five largest trades — tests whether the result is a tail artifact |
| Win rate | Fraction with `net_r > 0` — **count metric, never policy evidence alone** |
| Fee/gross | Total fees ÷ total gross R |

Supporting, not decision-bearing: n per arm, regime counts, symbol concentration, longest losing streak,
strategy-family split.

## 3. Assessment criteria (five bands)

Applied to the **whole settled population** at window close. Criteria are absolute, not relative to the 11-day
baseline — the baseline is context, not a target.

- **Strong pass:** CI 95% lower ≥ **+0.10** AND median ≥ **−0.05**
- **Weak pass:** CI 95% lower ≥ **+0.05** AND median ≥ **−0.10**
- **Inconclusive:** CI 95% lower in **[0, +0.05)** OR median in **[−0.10, −0.20)**
- **Weak fail:** CI 95% lower < **0** OR median < **−0.20**
- **Strong fail:** CI 95% lower < **−0.10** OR median < **−0.30** OR fee/gross > **0.70**

Band semantics: a **pass** authorizes proposing a 04B direction on this evidence; it does not authorize a deploy.
A **fail** authorizes no change to the strategy layer except through a separately approved work package. Any
single-metric boundary crossing puts the read in the worse band — the protocol does not average a good CI against a
bad median.

### Baseline context (Measured, do not compare across populations)

| Population | Mean net R | CI 95% | Note |
|---|---|---|---|
| All 204, gate 60 | +0.109 | [−0.089, +0.304] | includes retired `ema_trend_20_50` |
| Live 182, gate 60 | +0.211 | [+0.003, +0.424] | post-hoc exclusion of the retired family |

The CI lower of **+0.003** is the edge of noise. It is the possibility of an edge, not an edge. Any window result
must be reported with its own population definition, or it is not comparable.

## 4. Re-evaluation triggers (mid-window)

1. **Bullish regime appears.** If any calendar week inside the window shows a bullish BTC 4h regime (prior 7-day
   read = BULL), **pause the protocol**, and re-examine H2 from scratch: inside a mixed-regime window "aligned"
   changes meaning, and a single-regime filter verdict no longer transfers. Record the pause date and the regime
   read that caused it. Resume only after the operator states how the mixed sample is to be read.
2. **Tail event.** A streak of **5 consecutive wins > 2R** is noted in the snapshot the day it completes. It moves
   the mean and not the median; the note exists so the mean is never read as the strategy's typical trade. No
   threshold change, no early conclusion.

Standing kill-switch (unchanged, `AGENTS.md` §5) overrides this protocol: halt new entries if the $16 cap is hit, if
a 30-day sample has CI lower ≤ 0 **and** median ≤ −0.5R, or if fees exceed gross in a rolling 7-day window.

**Trigger status at Window 1 closure (Measured, `W_LIVE_AUDIT_2026-10-24.md`):** no trigger fired.
Bullish-regime week: no (bearish 174 / neutral 63 / bullish 11 regime reads). Five consecutive
>2R: **could not have fired** — the best net R in the window was 1.952R and zero trades exceeded
2.0R net. CI lower ≤ 0: **true** (−0.545) but the 30-day sample gate was not met. Fees > gross:
**met on every available span** (whole window 8.736R vs 1.000R; 10-22 and 10-24 individually),
but a literal rolling 7-day window could not form before 10-29. All triggers **re-arm at Window 2
start** (§7).

## 5. Mechanics

**Cadence below applied to Window 1 and is superseded for Window 2 by §7.**

- **Cadence:** one snapshot per week (dates: 10-29, 11-05, 11-12, 11-19) plus a closing snapshot. All snapshots are
  appended to the single file `reports/OBSERVATION_SNAPSHOTS.md` (newest last; gitignored via `reports/*.md`) — never
  a source of truth for the ledger.
- **Snapshot contents:** window day count, settled n, the six primary metrics, arm table, top-5 concentration,
  current band, trigger status, and any anomaly (symbol concentration, missing outcomes, duplicate `signal_id`).
- **Source of truth:** the outcomes ledger joined to signal rows by `signal_id`. Dashboard or Telegram counts are
  never used as evidence.
- **Reproducibility:** every snapshot states the exact filter used (dates, gate, settled-only), so a later reader
  can rebuild it. Snapshot scripts are temporary and deleted after use; only the numbers persist.
- **No mid-window policy changes.** No behaviour change ships during the window. Persistence/provenance work is
  allowed (it does not move a number); gate, filter, strategy, and exit changes are not.
- **No re-wording.** Hypothesis thresholds and population definitions are frozen at 2026-10-22. If a criterion
  cannot be evaluated, the result is Inconclusive by sample with the reason recorded verbatim.

## 6. What this protocol is not

- It is not proof of profitability: $16 cannot prove profitability, and the distribution is tail-dependent
  (top-5 = 60% of profit).
- It is not a 04B green light: a pass produces a *direction* to evaluate, and 04B still needs its own approval.
- It is not a reason to keep a losing strategy alive: the standing kill-switch fires independently of these bands.
- It is not retrospective: no hypothesis is added, removed, or re-worded once the window has started.

---

## 7. Window 2 — post-deploy (starts on the deploy-success date)

**Start:** the timestamp of the successful **main-service** deploy. **End:** start + 30 calendar
days, or **n = 200** settled, whichever comes first. **Deploy target:** local `HEAD` at push time;
runtime code is `f80ff11` (`reports/DEPLOY_READINESS.md`).

**Why a separate window, not a continuation.** The deployed code differs materially:
`ema_trend_20_50` and `rsi_mean_reversion` are gone, the ATR double-subtraction and momentum
lookahead are fixed, P2 rejects TP widening, W6 queues records when the Invo session is missing,
and `scoring_breakdown` starts persisting. Window 1 measured a system that still ran a retired,
losing family. Pooling the two populations would average two different systems and call it one
result.

**Hypotheses.** H1–H4 carry over **unchanged** — same claims, same thresholds, same failure
branches (`reports/PRE_REGISTERED_HYPOTHESES_2026-10.md`, frozen 2026-10-22). They are evaluated
on Window 2's settled population only. **Window 1 rows are never pooled in.**

**What becomes measurable only now:**

- **H4 stops being Estimated.** `scoring_breakdown` persists on every published signal, so
  regime-only vs full-scoring is Measured for the first time.
- **Per-strategy R is a 4-family number.** Any Window 1 comparison must exclude
  `ema_trend_20_50` from the Window 1 side, or it compares 4 families against 5.
- **Fee drag is read against a stated model.** Window 1's model is
  `2 × (0.04% taker + 0.02% slippage) = 0.12%` of notional per round trip
  (`src/backtest/evaluator.py:30-31,228`; mirrored in `src/exits/config.py:115-117`). That is
  **more conservative than Hyperliquid base taker/taker (0.045% × 2 = 0.09%)** and ~4× the
  maker/maker cost (0.015% × 2 = 0.03%). State the model in every snapshot until a real fee
  statement replaces it — a maker-heavy fill regime would shrink the drag by ~4×.

**Triggers:** unchanged, re-armed at Window 2 start (bullish regime week; 5 consecutive >2R;
CI lower ≤ 0 on the rolling 30-day; fees > gross on a rolling 7-day).

**Minimum useful n stays 100 settled.** Window 1 produced 49 settled in 2.8 days; 30 days is
therefore expected to clear 100 (Estimated), and if it does not the window extends rather than
concludes.

**Baseline for comparison.** Window 1 is the only pre-deploy archive and is a **reference, not a
control**: different code, different regime mix, 2.8 days. Any delta must be labeled Estimated
until Window 2 itself has enough settled rows to carry its own CI.
