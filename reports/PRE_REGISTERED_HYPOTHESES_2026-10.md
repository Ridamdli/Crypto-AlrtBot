# Pre-Registered Hypotheses — 30-Day Observation Window (2026-10)

**Pre-registered:** 2026-10-22, before any data from the window exists.
**Window:** see `reports/OBSERVATION_PROTOCOL_2026-10.md` (start 2026-10-22; end 2026-11-21 or n = 200, whichever first).
**Decision context:** Phase 04A verdict stands at 0/5 ADOPT (aggregate kill). Operator decision **(f) status quo + observe**:
no strategy rebuild, no gate change, no regime filter, no deploy. These hypotheses decide what the window's
evidence authorizes next (04B direction, or a halt), not whether the strategy is profitable.

### Status of the two facts this document must not re-open

1. **"Regime is the entire score" — RETRACTED by the operator.** Regime partial r +0.101 (204) / +0.109 (177 live-only);
   non-regime partial +0.109 / +0.112; corr(non-regime, regime) −0.582. All components are weak; none does the
   work alone. Do not re-raise.
2. **"Adjustment over-firing" (from W2) — RETRACTED, remains retracted.** Do not re-raise.

### Evidence this rests on, and its hard limits

- **Measured**, 11 days / 204 entry signals / 5.1 days / 97 exit signals / one regime (bearish 157, neutral 47) /
  UNIUSDT-dominated exit population. No claim here is a 30-day result.
- **$16 cannot prove profitability.** Top-5 trades = 60% of profit. CI lower **+0.003** at gate 60 live-only is the
  edge of noise — *the possibility of an edge*, not an edge.
- All policy statements are **R-weighted**. A count-only comparison cannot justify a policy change.
- Every number in this document is labeled — **Measured / User-provided / Estimated**. Thresholds are operator-set
  targets, not measurements.

---

## Measurement definitions (fixed before data, not adjustable after)

| Term | Definition |
|---|---|
| Population | Published signals with a **settled** outcome and a recorded `net_r`. Unsettled / EXPIRED-without-fill rows are excluded, not imputed. |
| `net_r` | The outcomes ledger's net R after fees, as recorded (outcomes ledger keyed by `signal_id`). |
| Gate | `confidence` threshold on the published signal row. Gate 60 = all published (production floor); gate 65 = `confidence >= 65`. |
| Aligned / opposed | `aligned` = (side LONG and `btc_regime` bullish) or (side SHORT and bearish); `opposed` = the opposite pairing; `neutral` regime is neither and is reported separately. |
| Mean / median | Arithmetic mean and median of `net_r`. **Both are always reported.** The mean is tail-driven; the median is not. |
| CI 95% lower | Lower bound of a bootstrap 95% CI on the mean `net_r` (10,000 resamples, fixed seed 20261022, recorded). |
| Top-5 concentration | Share of total positive R contributed by the five largest `net_r` trades. |
| Win rate | Fraction of settled signals with `net_r > 0`. Count metric — **never** used as policy evidence on its own. |
| Fee/gross | Total fees ÷ total gross R, per the ledger's recorded fee fields. |
| Correlation `r` | **Spearman ρ** (primary, pre-registered) plus Pearson for comparability with the 04A numbers, both on the same settled sample. Rank correlation is primary because regime points take few discrete values. |

**Breakdown availability:** with no deploy during the window, production signals will **not** carry
`scoring_breakdown`. Audits must reconstruct the component points and label any derived per-component number
**Estimated**. This affects H4's attribution, not H1–H3, which read `confidence` and outcomes only.

---

## H1 — Gate 65 vs Gate 60

**Claim.** Gate 65 maintains mean ≥ +0.20R with n reduced by ~30%.

**Test.** After the window closes, compare gate 60 (all published signals) against gate 65 (`confidence >= 65`)
on the same settled population. Report mean, median, CI lower, and n for both arms.

**Success.** Gate 65 mean ≥ **+0.20R** AND gate 65 n ≥ **100**.

**Failure.** Gate 65 mean < **+0.10R** OR gate 65 n < **50**.

**Inconclusive.** Neither.

**Note.** This tests **generalizability**, not superiority: success makes gate 65 a *defensible* choice, not a
better one than gate 60. Sample gate (Measured on the 204-signal distribution: 157/204 = 77% of signals score
≥ 65): success needs ~**130** settled signals total (Estimated). If the window closes below that, H1 is
inconclusive **by sample**, and that is recorded as such — not as a failure. Gate 65 was the in-sample total-R
maximum; it must not be adopted on that basis alone.

---

## H2 — Hard regime filter

**Claim.** Applying a hard regime filter (aligned signals only) maintains total R with ~30% fewer trades AND
improves mean.

**Test.** After the window closes, compare aligned-only against all settled signals: mean, total R, n.

**Success.** Filtered mean ≥ **+0.28R** AND filtered n ≥ **80** AND filtered total R ≥ **0.9 ×** unfiltered total R.

**Failure.** Filtered mean < **+0.20R** OR filtered total R < **0.7 ×** unfiltered total R.

**Inconclusive.** Neither.

**Note — the single-regime trap, stated up front.** The 204-signal window contains essentially one regime
(bearish 157, neutral 47). "Aligned" therefore has almost no counterfactual: a filter validated here would be a
filter validated against a single regime, and it is un-testable inside that regime. Consequently: **if a bullish
period appears inside the window, this hypothesis is re-evaluated from scratch** — "aligned" must still mean what
it meant, and the protocol pauses (see `OBSERVATION_PROTOCOL_2026-10.md`, trigger 1). A pass here is evidence of
*coherence with one regime*, never evidence that the filter generalizes.

---

## H3 — Side-opposed signals

**Claim.** Signals side-opposed to regime (14/204 in the current window) have worse outcomes than aligned signals.

**Test.** Split the window's settled sample by regime alignment (aligned / neutral / opposed); compare means and
medians, aligned vs opposed.

**Success.** Opposed mean < aligned mean by at least **0.3R**.

**Failure.** Opposed mean ≥ aligned mean. (Reading: the regime filter should be **removed**, not fixed.)

**Inconclusive.** Difference < 0.3R but direction consistent with the claim.

**Note.** This decides whether the **currently inert** `regime_filter` should be **fixed or removed** — it is not
a case for adding a new filter. Opposed signals are ~6.9% of the population (Measured, 14/204), so the opposed arm
is small by construction: at n = 200 the expected opposed count is ~14 (Estimated), and a 0.3R mean difference on
n ≈ 14 has a very wide CI. Expect **Inconclusive** to be the likely outcome, and treat any pass as directional
evidence requiring a second window. This hypothesis is the gate on backlog item 1 (W-S H1) — no code changes to
`regime_filter` until it resolves.

---

## H4 — Regime-only vs full scoring

**Claim.** If regime alone correlates with outcomes at r ≥ **+0.10** on 30-day data, full scoring is redundant.

**Test.** Spearman ρ of regime points alone vs `net_r`, and of total score (`confidence`) vs `net_r`, on the same
settled sample. Report both ρ and their difference.

**Success.** Regime-only ρ ≥ total-score ρ − **0.02**.

**Failure.** Regime-only ρ ≤ total-score ρ − **0.05**.

**Inconclusive.** Neither.

**Note.** Read against the retraction: the 204-signal measurement already shows regime is *not* the whole score
(r ≈ +0.10 each, non-redundant at −0.58). H4 is a generalizability check on that finding, not a test of the
retracted claim. Because `scoring_breakdown` will be absent (no deploy), the regime component must be
**reconstructed** from `btc_regime` + `side` — that reconstruction is exact for this component (the rule is
deterministic) and is labeled **Estimated** anyway.

---

## What each result authorizes

| Result | Authorized next step | Not authorized |
|---|---|---|
| H1 success | Consider gate 65 in 04B as a defensible default | Deploying a gate change without a separate authorization |
| H1 failure | Keep gate 60; stop revisiting the gate | Removing signals / tightening the floor as a fix |
| H2 success | A regime-filter *proposal* for 04B, with the single-regime caveat attached | Enabling a filter in production on this evidence |
| H2 failure | Drop the hard-filter direction; keep observing | Removing `regime_filter` code on a filter test alone (H3 owns that) |
| H3 success | Fix `regime_filter` (backlog item 1) | Assuming the fix improves outcomes without a re-test |
| H3 failure | **Remove** the inert filter (backlog item 1) | Leaving it as documentation of intent |
| H4 success | Treat the score as regime-dominated in 04B scoping | Deleting non-regime components |
| Any inconclusive | Extend the window or hold status quo | Re-wording the hypothesis to fit the data |

## Pre-registration integrity

- These four hypotheses, their thresholds, and the measurement definitions are frozen at 2026-10-22.
- **No threshold is adjusted after seeing data.** No population is redefined after seeing data. A miss is
  reported as a miss.
- If a criterion cannot be evaluated (e.g. sample shortfall, no opposed signals), the hypothesis is **Inconclusive
  by sample** and that reason is recorded verbatim.
- The window may be closed early only for the entry kill-switch conditions already standing in `AGENTS.md`, or by
  operator decision — never to avoid an unfavourable read.
