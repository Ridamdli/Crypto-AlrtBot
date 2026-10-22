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

**Window-relevant note (added 2026-10-22).** During the active observation window the Bollinger gate rejects
**7 would-be-published signals** (ADA SHORT score 80, NEAR SHORT 75 ×2, NEAR LONG 60 ×2, ZEC SHORT 75). They are
potential signal loss in the window sample, and they cluster on NEAR — 4 of the 7.

Read them conditionally, once the tree decides:

- **H1–H4 show edge** → evaluate whether these 7 would have improved or worsened outcomes. Their absence is a
  mitigating factor *for the gate*, not evidence that it works.
- **H1–H4 inconclusive** → these 7 are potential noise; the gate's effect stays unmeasured either way.

**Do not remove the gate mid-window.** A post-window removal decision must cite this deferred set, not the
bandwidth distribution.

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

**Done:** `ec8527e` — README test count (620 / 31 files, per-file counts), threshold location, multi-TP ladder and
the OI/funding claim corrected; the 1h/4h/1d per-coin tables are now explicitly labelled research ideas. Residual
risk: those tables still read like design intent at a glance.

---

## 5. Momentum ATR parameters inert

**Impact: low for the window, medium for interpretation.** The momentum family declares `atr_filter` and
`atr_multiplier` and never consumes them.

**Evidence (measured 2026-10-22, read-only).** `src/strategies/momentum.py`:

| Line | Code | Fate |
|---|---|---|
| 122 | `atr_15m = self._calculate_atr(...) if self.params["atr_filter"] else 0` | assigned, never read |
| 139 | `atr_stop_dist = self._calculate_atr(...) * self.params["atr_multiplier"] if ... else 0` | assigned, never read |
| 140 | `min_risk_dist = self._calculate_atr(...) * 0.5 if self._calculate_atr(...) > 0 else ...` | recomputes ATR three times in one expression |
| 150+ | structural SL from the recent swing low | what actually prices the stop |

`atr_filter` and `atr_multiplier` appear in `default_params` and `param_schema` and nowhere else in the decision
path.

**Same defect class as items 1 and 3:** a parameter is declared, advertised, never read. Item 1 is `regime_filter`,
item 3 is `StrategyFamilyConfig`. This one is quieter — nothing reads the value at all, so no filter silently passes
everything; the config simply describes a stop rule that does not exist.

**Classification: BEHAVIORAL DECISION, not cleanup.** Deleting the two unused locals is a no-op; deciding what
`atr_filter` / `atr_multiplier` should *do* is not.

**Options**

- **(a) Wire the ATR stop in** — behaviour change. Would replace or bound the structural swing low, changing stop
  distance, R multiple and every outcome in this family. Needs its own post-window observation before any 04B
  citation, same as item 2.
- **(b) Delete `atr_filter` and `atr_multiplier`** — no-op removal of a lie, plus the three dead locals. Behaviour
  identical, docs stop advertising a filter that does not exist. Should leave one line in the family docstring
  saying the stop is structural, since (b) otherwise erases the record that ATR was ever intended.

**Impact note.** Operator reports momentum at **+1.42R mean on 22 signals** in the current evidence set
(*User-provided* — not re-derived here; `signals.jsonl` holds **21** momentum signals, so the count needs
reconciling before the figure is cited). If that edge is real it comes from the structural swing-low stop, not from
ATR — which argues for (b) rather than for wiring ATR in to "fix" a family that already works.

**Window-safe: do not touch.** Current momentum behaviour is what the window measures; inert parameters change
nothing about it.

---

## Order of work (when authorized)

| # | Item | When | Kind of change |
|---|---|---|---|
| 2 | Bollinger gate: remove or re-threshold | **after the window closes**; before any 04B citation of +19R | behaviour (11 extra detections, 7 publish) — needs a published-set diff |
| 4 | Docs correction | before any 04B decision references the docs | documentation |
| 1 | `regime_filter` fix-or-remove | after H3 resolves | behaviour - needs H3 evidence |
| 5 | Momentum ATR: wire in or delete the params | after the window; before any momentum figure is cited as an ATR result | behaviour (a) or no-op deletion (b) - needs a decision |
| 3 | Provenance completeness | after the window, as its own work package | evidence plumbing |

## Not in this backlog (on purpose)

- Exit management / P2–P3 tuning — **03-B-2 is CLOSED**; do not reopen.
- Retired families (`ema_trend_20_50`, `rsi_mean_reversion`) — standing decision, they stay retired.
- Momentum threshold (M1), `confirmation_bars` (M2), partial-bar volume (M4), forming-bar bands (M5), duplicate
  method definitions (L1/L2), donchian close-vs-price wording (L4), orphan `.pyc` (L6), Bollinger SHORT tests (L7).
  These are real but none is decision-blocking; they are recorded in the findings register and wait for a work
  package that touches that family.

---

## Post-window cleanup queue (added 2026-10-22)

Static-analysis debt with no decision value **during** the window. `ruff --fix` rewrites bytes: a large mechanical
diff would obscure the H1–H4 read, because nobody reviewing the tree can tell a cleanup hunk from a behaviour hunk.
Everything here waits until the window closes (2026-11-21).

| Rule | Count | Effort | Notes |
|---|---|---|---|
| F401 unused import | 27 | ~1h | `--fix`, then review removals inside modules that legitimately re-export |
| F841 unused variable | 25 | ~1h | `--fix` with a review pass; the one pair that was not cosmetic moved to item 5 |
| F541 f-string without placeholder | 11 | ~10m | safe |
| F811 redefinition | 3 | ~1h | `src/config.py:118` (`StrategyFamilyConfig`, same object as backlog item 3), `src/orchestrator.py:410` (`get_approved_strategies_for_all`), `src/research/strategy_research.py:127` (`StrategyResearchRunner`). These are **class/function redefinitions**, not the momentum duplicate-method pair (L1/L2) |
| F601 repeated dict key | 2 | ~5m | audited clean — verdict below |

**F601 verdict — INTENTIONAL, no bug (audited 2026-10-22, read-only).** `src/pipeline.py:387-388` and `:410-411`
both set `strategy_id` and `strategy_version` in the same candidate dict, with **identical** expressions
(`cand.strategy_id`, `cand.strategy_version`). The later pair wins and equals the earlier pair, so the candidate is
byte-identical either way: a leftover from moving those keys down the literal. Cleanup is deleting lines 410-411 —
deleting the *second* pair is a pure no-op. Not done now.

**Deferred rule families:** the config selects F + E9 only. E4 / E7 stay unselected until this queue is empty; the
upgrade trigger lives in `pyproject.toml`.
