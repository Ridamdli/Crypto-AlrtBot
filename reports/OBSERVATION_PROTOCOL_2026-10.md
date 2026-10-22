# 30-Day Observation Protocol (2026-10)

**Created:** 2026-10-22. **Status:** active, standing. Pre-registered companion:
`reports/PRE_REGISTERED_HYPOTHESES_2026-10.md` (H1–H4).
**Decision it feeds:** Phase 04B/04C direction. It is not a profitability trial.

## Scope (read first)

This observation window measures the **OLD production system** (revision pre-`49ff810`, 37 commits held locally).
It does **NOT** test:

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

**Note:** "Recommend deploy" is a recommendation. Deployment requires operator authorization per the standing
no-deploy policy. The window's exposure is $16 of real money on old strategy behaviour — an expected outcome is
*measurable but noisy*, not decisive.

## 1. Window

| Item | Value |
|---|---|
| Start | **2026-10-22** (date of this document). No deploy is authorized, so the window begins on the **current production run** — signals already publishing from the running revision. |
| End | **2026-11-21** (start + 30 calendar days) **OR** the moment the settled population reaches **n = 200**, whichever comes first. |
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

## 5. Mechanics

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
