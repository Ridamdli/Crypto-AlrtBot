# W-S Defects Backlog

**Date:** 2026-10-22. **Source:** `reports/STRATEGIES_AUDIT.md`, `reports/STRATEGIES_AUDIT_TABLE.md`
(findings register E, items H1 / H2 / H3 / M3 / M6 / L5).
**Status:** backlog only. **Nothing here is approved for a fix.** No behaviour change ships during the 30-day
observation window (`reports/OBSERVATION_PROTOCOL_2026-10.md` §5) except where a disposition says otherwise.
**Relation to W-DD:** W-DD Option A (implemented 2026-10-22) records the *scoring* breakdown. It does **not** record
strategy parameters — item 3 below stays open on purpose.

Ranking is by **impact on decisions the operator is about to make**, not by code severity.

---

## 1. `regime_filter` is inert — config advertises a filter that does not exist

**Impact: highest.** A config key named `regime_filter` reads as an active hard filter; it is not one. All four
strategy families are direction-agnostic, and **14 of 204 published signals (6.9%, Measured)** were side-opposed to
the regime with nothing stopping them. Any future tuning of that key would silently do nothing, and any report
written against the name would overstate what the system does.

**Evidence:** `donchian.py:117-120`, `bollinger_atr.py:128-129`, `volume_breakout.py:32,103`; findings register H1.

**Options**
- **Fix** — implement the filter. Requires a behaviour decision (which alignments to exclude) and a re-test.
- **Remove** — delete the dead key so the config stops advertising behaviour it does not have.

**Disposition: WAIT for H3.** H3 (`reports/PRE_REGISTERED_HYPOTHESES_2026-10.md`) is exactly the fix-or-remove test:
if opposed mean is worse by ≥ 0.3R, fix; if opposed is not worse, remove. No code change to `regime_filter` before
H3 resolves. Do not "fix it while we're in there" — the direction is unresolved and a fix without H3 would ship an
untested behaviour change during the window.

---

## 2. Bollinger compression gate never binds

**Impact: high, and it changes how the best result is read.** The 10% bandwidth gate fired on **193 vs 195 vs 27**
candidates across three threshold settings over 3,370 windows; median bandwidth was **0.85–2.8% (Measured)** against
a 10% gate. The production Bollinger family is therefore a **plain band breakout**, and the **+19.06R / 51 signals
(Measured)** belongs to a band breakout — not to a squeeze strategy. Leaving it is a standing invitation to
misattribute that result in a future 04B review.

**Evidence:** `bollinger_atr.py:28,116`; findings register H2; also M5 (bands include the forming bar).

**Options**
- **Remove** — accept that the family is a band breakout and delete the pretence.
- **Fix threshold** — set a value that actually filters (median 0.85–2.8% suggests a gate near the observed
  distribution, not 10%).

**Disposition: DEFERRED — it is not a no-op, so it is not window-safe.** Corrected by replay on 2026-10-22
(17,024 windows, 2026-10-08 → 10-19, all 21 symbols in `data_store/historical/historical_data.db`, strategy called
directly): **gate on → 1,281 candidates; gate removed → 1,292.** The gate rejects **11** detections (0.86%), not
zero, and **7 of the 11 clear the publish floor** (scores 60–80 against `min_confidence=60`). Removing it would
therefore add published signals during the observation window — a behaviour change the protocol forbids (§5).

This supersedes the earlier "removing a gate that never binds cannot move an outcome": the gate *does* bind, just
rarely. Remove-vs-re-threshold stays open until the window closes; whoever takes it must re-run the same replay and
present the published-set diff explicitly, because the cascade is not inferable from the bandwidth distribution
(this is the same class of error as assuming a 1% candidate rejection cannot reach the published set).

```
gate on  1,281 candidates  sha bcce6c5bd8cd9fa0
gate off 1,292 candidates  sha 8b1cd8eefbfbcf0d
extras 11 -> ADA 1 (80), NEAR 4 (75,75,60,60; one at 55 filtered), UNI 2 (both 55, filtered),
              ZEC 3 (75; two at 55 filtered)
```

---

## 3. Strategy parameters invisible to provenance

**Impact: high for evidence integrity, low for this window.** `configuration_hash` excludes strategy parameters,
`StrategyFamilyConfig` is declared twice and never instantiated, the pipeline passes `{}`, and
`strategy_version` is the literal `"1.0.0"` on every family. A parameter change is therefore unrecorded: replay and
diff cannot distinguish strategy revisions from market change.

**Evidence:** `config.py:124-152`, `pipeline.py:280,378`, findings register H3, L3.

**Options**
- Fold strategy params into `configuration_hash`; version each family honestly.
- Leave it and label every cross-version comparison as unprovenanced.

**Disposition: DEFER to a follow-up "provenance completeness" task.** Explicitly out of scope for W-DD: Option A
records what the *scorer saw*, and §4.1 forbids smuggling strategy params in. Do it as its own change with its own
tests, after the window. **Why it is not urgent:** no parameter is being changed during the window, so no
comparison in the window is at risk. **Trigger to move it up:** the first time a strategy parameter is tuned, or
the first cross-version comparison that a 04B decision depends on — whichever comes first.

---

## 4. Docs are stale in ways that mislead tuning and coverage

**Impact: medium now, blocking before 04B.** Docs claim **24** strategy tests where the suite collects **39**;
retired families are still described; the multi-TP ladder is described as if live; the documented timeframes
(1h/4h/1d) are not what runs (all families are 15m); OI and funding are described as unused while scoring uses both.

**Evidence:** findings register M3, M6, L5; `README.md:116,383`; `docs/Crypto_strategies.md` line 232.

**Options**
- Update the four affected claims to what the code does.
- Delete the sections that no longer describe anything.

**Disposition: UPDATE BEFORE any Phase 04 decision cites the docs — not blocking the window.** It is a
documentation change, so it cannot move a number and is safe to do inside the window; it is simply not the best use
of the window's attention. Non-negotiable before 04B: a phase decision that references a stale doc is a decision
built on a wrong description. Fix the four claims (test count, family list, multi-TP, timeframe), then delete
whatever is left that describes nothing.

---

## Order of work (when authorized)

| # | Item | When | Kind of change |
|---|---|---|---|
| 2 | Bollinger gate: remove or re-threshold | **after the window closes**; before any 04B citation of +19R | behaviour (11 extra detections, 7 publish) — needs a published-set diff |
| 4 | Docs correction | before any 04B decision references the docs | documentation |
| 1 | `regime_filter` fix-or-remove | after H3 resolves | behaviour — needs H3 evidence |
| 3 | Provenance completeness | after the window, as its own work package | evidence plumbing |

## Not in this backlog (on purpose)

- Exit management / P2–P3 tuning — **03-B-2 is CLOSED**; do not reopen.
- Retired families (`ema_trend_20_50`, `rsi_mean_reversion`) — standing decision, they stay retired.
- Momentum threshold (M1), `confirmation_bars` (M2), partial-bar volume (M4), forming-bar bands (M5), duplicate
  method definitions (L1/L2), donchian close-vs-price wording (L4), orphan `.pyc` (L6), Bollinger SHORT tests (L7).
  These are real but none is decision-blocking; they are recorded in the findings register and wait for a work
  package that touches that family.
