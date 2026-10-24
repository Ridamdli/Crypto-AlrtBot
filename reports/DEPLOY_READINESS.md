# Deploy Readiness — Waves 1 and 2

**AUTHORIZED 2026-10-24 by operator decision.** Window 1 was terminated early
(`reports/OBSERVATION_PROTOCOL_2026-10.md`); this document is now an **execution
checklist**. **The agent does not deploy. The operator executes every step.**

**Target commit: `HEAD` at push time.** Runtime code is **identical to `f80ff11`** — every commit
after it is documentation only, provable with
`git diff --stat f80ff11..HEAD -- src mirror scripts` (must print nothing). Nothing has been pushed.

**Evidence base:** Window 1 (`reports/W_LIVE_AUDIT_2026-10-24.md` — 2.8 days, 59 published,
49 settled, one regime) plus the 11-day / 204-signal baseline where stated. R-weighted where a
policy claim is made. No claim is a 30-day result.

**Why deploy now, recorded:** the running system lost **−8.44% paper equity in 2.8 days**
(1028.75 → 941.93), with **89% of the net loss from `ema_trend_20_50`** — a family already
retired locally — and gross **+1.000R** against modeled fees **−8.736R**. Continuing a 30-day
observation on a system whose defects are already diagnosed spends capital to re-measure known
results.

---

## 0. Deploy units and scope

| unit | service | start command | volume |
|---|---|---|---|
| main | `crypto-alrtbot` | `python -m src.service` | `/app/data_store` |
| mirror | `pariston-mimic` | `python -m mirror` | `/app/mirror_data` |

Production reference: **2026-10-13**. Last confirmed mirror revision:
**`f2dfde8`**. The mirror is uploaded from the local tree with `railway up` —
GitHub auto-deploy does not fire.

**Ship set — Measured.** **61 commits** since `f2dfde8`; **78 files** touched under `src/`,
`mirror/`, `scripts/`. The main service's exact running revision is unconfirmed, but it is
provably **pre-`49ff810`**, which makes the provable floor **32 commits**:

| Proof | Observation |
|---|---|
| `49ff810` not running | no `applied` key on any of the **1,315** in-window exit decisions |
| `4ddd798` not running | no `scoring_breakdown` on any published signal |
| `a29831a` not running | `ema_trend_20_50` still publishing (8 in-window signals) |

Railway auth is **valid** as of 2026-10-24. Window 1's 11 days / 204 signals / one regime
baseline still bounds every behaviour claim.

### 0.1 Deploy event — 2026-10-24, target `HEAD` (runtime `f80ff11`)

| Step | Unit | Action | Gate before the next step |
|---|---|---|---|
| 1 | repo | `git push origin master` | push succeeds; remote `master` = local `HEAD` |
| 2 | main (`crypto-alrtbot`) | auto-deploy on push, or manual deploy from that `HEAD` | deploy status **SUCCESS** + §7.1 checks |
| 3 | mirror (`pariston-mimic`) | `railway up` from the local tree (auto-deploy does **not** fire) | deploy status **SUCCESS** + §7.2 checks |

The mirror ships second on purpose: if the main service misbehaves, rolling it back does not
require touching a service that may be holding live positions.

---

## 1. Wave 1 — safety and correctness

Ships the main-service image and the mirror independently. Neither depends on
the other.

### 1a. Main service

| commit | change | effect on recorded history |
|---|---|---|
| `12faa3c` | retry transient capital skips; un-bury seen coins | none (live-only path) |
| `c941c6c` | OI short + momentum scoring inputs | **Hold** — see §3 |
| `d9212eb` | count only delivered Telegram sends | none |
| `b787459` | atomic ledger writes | none |
| `7542c9a` | zero evidence quality on abstains; RR_DEAD fix | none on past rows; forward exit decisions change |
| `d666c7c` | decision keys hashed from clean proposal fields | none (future keys only) |
| `86e1fcb` | retain idempotency keys past the cap | none |
| `674c86b` | **ATR stop double-subtraction fix** | none on past rows; **forward stops change** |
| `fa79b58` | `as_of_time` NameError; dead duplicate removed | parser correctness |
| `c90507a` | archive 7-day signal files; chronological merge | storage only |
| `02aa4f3` | ruff fixes in `src` | none |
| `2bcf1b7` | corrupt daily files preserved; UTC day boundary | storage bucketing |
| `3602856` | retire `rsi_mean_reversion` | **deliberate reduction** |
| `52bf4b3` | momentum lookahead fix (forming candle excluded) | none on past rows; **forward SL changes** |
| `49ff810` | persist `applied` on exit decisions | none |
| `a29831a` | retire `ema_trend_20_50` | **deliberate reduction** (evidence: −0.736R, CI [−1.291, −0.181]) |

Three commits are **forward-only behaviour changes**, not history no-ops:
`674c86b`, `52bf4b3`, and the two retirements. They are correctness fixes and an
evidence-backed removal, but they are not "no-op" and must be verified as
forward changes (§7, V1.3).

### 1b. Mirror service

| commit | change | effect |
|---|---|---|
| `fcfdc33` | queue records when no trading session; flush oldest-first | none on history; new durable queue file |

### 1c. Wave 1 verification — see §7
### 1d. Wave 1 rollback criteria — see §8.1

---

## 2. Wave 2 — policy instrumentation

Both commits are **replay-verified no-ops** and change no past decision.

| commit | change | evidence |
|---|---|---|
| `f62d9a1` | **P2** — a target may only move toward the mark; widening rejected | 31/31 historical ADJUSTs accepted, 0 widenings, 0 flips (`scripts/verify_p2_zero_change.py`) |
| `95db541` | **P3 shadow** — records what P3 would have decided on pending invalidation | write-only; invalidation still applies (test) |
| `4ddd798` | **W-DD** — persist `scoring_breakdown` on published signals | additive field; `None` on every pre-deploy row, never zero |

Wave 2 depends on Wave 1 only because it is the same image. It has no runtime
dependency on any Wave 1 commit. 11 days / 204 signals / one regime /
UNIUSDT-dominated still bounds the evidence: P2's proof is 31 rows, not 31 days.

### 2a. Verification — see §7
### 2b. Rollback criteria — see §8.2

---

## 3. Hold — Phase 04 pending

| commit | why held |
|---|---|
| `c941c6c` | changes OI/short scoring inputs, i.e. **which entry signals are selected** |
| anything else touching entry selection or per-coin strategy mapping | Phase 04 owns it |

`c941c6c` is a bug fix, not a redesign. It is held because the rule is literal:
entry selection changes wait for Phase 04. If the operator prefers to ship it
with Wave 1, it moves; that is an operator call, not a technical one.

---

## 4. W6 Dedup Risk & Mitigation

**Question:** on crash-restart, can a fill be POSTed twice, and is that safe?

**Finding 1 — `clientTxId` exists on both record types.**
- open: `mirror/auto.py:127` → `entry:<uuid4 hex[:8]>`
- close: `mirror/auto.py:726` → `close:<uuid4 hex[:8]>`

**Finding 2 — the id is stable across retries.**
- It is generated **once**, when the payload dict is built.
- `TradingSession.post()` serialises that dict into `pending_records.jsonl`
  **before** raising, so a queued replay sends the **same** id.
- `flush()` re-posts the stored body verbatim; it never rebuilds it.
- `_post_now`'s 401 refresh retry reuses the same `payload` object.
- Neither the open path nor the close path rebuilds a payload after a failure:
  the open catches and returns; the close logs `record_close_failed`, and a later
  cycle hits the `already-flat` early return before any record is built.
- The queue rewrite is the only at-least-once window: POST succeeds, the process
  dies before `_rewrite_queue` → the row replays with the **same** id.

**Finding 3 — Invo-side dedup is NOT proven from this repository.**
The client always sends `clientTxId`, and
`reports/MIMIC_RECORD_FAILED_INVESTIGATION.md` says a successful record
"registers" the id server-side. That is consistent with dedup but is not proof of
a uniqueness constraint, and no code or doc shows Invo rejecting a duplicate. A
read-only check of the API can't resolve it either — it needs a deliberate
duplicate submission.

**Finding 4 — id space (client side, resolved).** It was 32-bit
(`uuid4().hex[:8]`): ~1.2% collision at 10,000 records. Both open and close now
use `hex[:16]` (64-bit), lowering that to ~2.7 × 10⁻¹¹. Old queued and recorded
ids keep their original width; no migration. The main bot already used a full
UUID, so this was mirror-only.

**Verdict:** retries send byte-identical ids. **Retries are safe only if Invo
dedupes on `clientTxId`** — which remains unconfirmed. Mitigation until
confirmed:
1. Deploy W6 **with monitoring**, not standalone: count POSTs per `clientTxId`;
   any count > 1 after a queue replay is a duplicate.
2. If duplicates appear, do not patch the recorder blind. A reconcile pass that
   queries Invo for the id and skips existing records is the fix; propose it
   before writing it.
3. The client-side collision risk is **fixed** (`hex[:16]`). It does not fix
   missing server dedup; that remains external and must be confirmed before W6.

**No change to the delivery or retry mechanism has been made.** Only the id width.

---

## 5. p3_shadow persistence

`src/exits/p3_shadow.py` writes **`data_store/p3_shadow.jsonl`**. `data_store` is
the main service's mounted volume, so the sample survives redeploys. The earlier
`mirror_data/` path was container-local on the main service and would have
reset on every deploy — that is fixed in `2f5c06a`, and the test pins the default
path.

The file is append-only JSONL, one row per pending invalidation. It is not read
by any decision path.

### 5.1 Fee model behind every R figure (D1, open)

Every `net_r` in the audit is gross R minus `fees_cost_r`. The model is
`total_costs_pct = 2 × (0.0004 taker + 0.0002 slippage) = 0.0012` of notional per
round trip, converted to R by `actual_entry_price / risk_distance`
(`src/backtest/evaluator.py:30-31,228-231`; mirrored in `src/exits/config.py:115-117`,
`src/exits/policy.py:155-161`, `src/exits/harness.py:228-230`).

Against Hyperliquid's published base tier (Measured, 2026-10-24,
`hyperliquid.gitbook.io/hyperliquid-docs/trading/fees` — perps taker 0.045%, maker
0.015%; tier 2 >$25M volume 0.035% / 0.008%):

| Fill regime | Round-trip cost | vs model 0.12% |
|---|---|---|
| taker / taker | 0.09% | model **+33% conservative** |
| maker / maker | 0.03% | model **~4× conservative** |

**Verdict: the model overstates cost, so Window 1's −8.736R fee drag is an upper
bound, not a fact.** It is **not** a MUST-FIX before deploy: a conservative cost
model biases *against* shipping, never for it. What it does mean is that
`0.178R/trade` must not be quoted as the real fee rate. One Invo/HL fee statement
replaces the estimate; until then every snapshot states the model it used.

This also does not reproduce the operator's "61% fee/gross" figure: Measured,
fees ÷ gross profit = **32.0%**, fees ÷ |gross loss| = **24.9%**, and fees ÷ gross
edge = **8.7×** — three different ratios, none equal to 61%.

---

## 6. Pre-deploy operator actions

Status as of 2026-10-24: **1 is done, 5 is granted; 2, 3 and 4 still need doing.**

1. **`railway login` — DONE** (2026-10-24, authenticated). Keep the session alive for the
   post-deploy checks; if it expires, verification stops at the logs.
2. **Confirm the target is `HEAD`** and that `git status` is clean before pushing. Runtime code
   must still equal `f80ff11`: `git diff --stat f80ff11..HEAD -- src mirror scripts` prints nothing.
   Do **not** deploy a dirty tree — the mirror is uploaded from the working tree with
   `railway up`, so an uncommitted edit ships silently.
3. **Confirm `INVO_TRADING_REFRESH` is set on `pariston-mimic` — REQUIRED.** Without it, W6
   queues records safely and delivers **none**: `post()` raises
   `no trading session (refresh failed)` on every attempt (`mirror/recorder.py:128`), which is
   exactly the 1,252 `RECORD FAILED` events in Window 1. `INVO_TRADING_TOKEN` is **optional** —
   the access token is minted by the refresh call and rotated into `trading_session.json`
   (`mirror/__main__.py:39-42`, `recorder.py:82-118`). Setting both is harmless; setting only the
   access token is not sufficient because it expires.
4. **Confirm Invo dedup on `clientTxId`** (§4). Still unproven from the repo. One deliberate
   duplicate submission of a harmless record settles it. If you skip this, W6 ships with the
   monitoring in V1.8 instead — that is a conscious acceptance, not an oversight.
5. **Authorize the deploy event — GRANTED 2026-10-24.** Window 1 is closed; the standing
   no-deploy policy no longer applies.

Env vars: **mirror** — `INVO_TRADING_REFRESH` (required), `INVO_TRADING_TOKEN` (optional). **main**
— no new variables. No Wave 2 variable.

---

## 7. Post-deploy verification

### 7.1 Wave 1 (main)

- **V1.1 Liveness.** `GET /healthz` OK; one completed scan cycle in the log with
  no traceback.
- **V1.2 Registry.** Startup lists four families; `ema_trend_20_50` and
  `rsi_mean_reversion` appear in neither the registry nor today's signal file.
- **V1.3 Stop sanity (forward change).** Spot-check one new signal's stop against
  the ATR formula. A stop exactly one ATR too tight means `674c86b` is not
  running. Stop must sit on the correct side of entry.
- **V1.4 Persistence.** New exit-decision rows carry `applied` and `applied_at`;
  a write leaves valid JSON (atomic path).
- **V1.5 Signal volume.** Compare the first 72h against the 11-day baseline
  (16.5 signals/day across 14 coins; **Measured** on 204 signals, **Estimated**
  as a rate). A drop caused by the retirements is expected; a drop elsewhere is
  not.
- **V1.9 Deployed revision.** `GET /api/version` (needs `ADMIN_TOKEN`) returns
  `commit_sha` from `RAILWAY_GIT_COMMIT_SHA` (`src/dashboard.py:607-612`). It must equal
  `git rev-parse HEAD` at push time (40 chars). Docs-only commits after `f80ff11` move the hash,
  not the code — so compare against the pushed SHA, not against `f80ff11`. Caveat: the **mirror**
  is uploaded with `railway up`, so its `RAILWAY_GIT_COMMIT_SHA` may be empty — verify the mirror
  by behaviour (V1.6/V1.7), not by hash.
- **V1.10 `scoring_breakdown` persists.** The first post-deploy published signal carries a
  non-null `scoring_breakdown` dict; every pre-deploy row still reads `None` (never `0`, never
  reconstructed). This is the one check that proves `4ddd798` is live, and it is what makes H4
  Measurable in Window 2.

### 7.2 Wave 1 (mirror)

- **V1.6 Queue drains.** With a valid session, record a close and watch
  `mirror_data/pending_records.jsonl` return to zero.
- **V1.7 No new close failures.** The audit stream gains no `record_close_failed`
  entries.
- **V1.8 Duplicate watch.** Count POSTs per `clientTxId` (§4).

### 7.3 Wave 2 (main)

- **V2.1 P2 wiring.** New `ADJUST_TAKE_PROFITS` rows have `plan_after.tp` closer
  to the mark than the prior target. Grep for `TP_WIDEN_REJECTED`: 0 is expected
  (31/31 on replay). A hit is not a bug — it is the guard doing its job; log it.
- **V2.2 Shadow writes.** After the first pending invalidation,
  `data_store/p3_shadow.jsonl` has one row per invalidation with
  `structure_pass`, `counter_pass`, `verdict`, `breach_margin_pct`.
- **V2.3 Invalidation unchanged.** Pending invalidations still fire at the same
  rate; P3 is write-only.

---

## 8. Rollback

**Mechanics:** redeploy the previous Railway deployment for the affected service.
Both services were uploaded from the local tree, so the prior image is the true
prior state. If a rebuild is required, check out the confirmed prior revision and
`railway up`. Do **not** `git revert` the 61-commit range as a unit — it mixes
runtime, tests, and lint.

**No data rollback is needed or possible.** Exit ledgers are append-only overlays;
the outcome ledger is written by the evaluator, not this change set.
`pending_records.jsonl` is a durable queue that both versions can drain.
`p3_shadow.jsonl` is append-only and unaffected by a rollback.

### 8.1 Wave 1 criteria — roll back if any is true

- **Stop geometry:** a new signal's stop is on the wrong side of entry, or
  `|entry − stop| / entry` falls outside **[0.05%, 10%]**.
- **Persistence:** any ledger or daily signal file is unreadable, or a daily file
  is overwritten instead of archived.
- **Volume:** 72h signal count outside **[50%, 200%]** of the 11-day baseline
  (16.5/day, Estimated) **and** attributable to the deploy rather than the regime.
- **Mirror:** `pending_records.jsonl` does not reach 0 within **60 minutes** of a
  valid session, or `record_close_failed` repeats.

### 8.2 Wave 2 criteria — roll back if any is true

- **P2 bypass:** an `ADJUST_TAKE_PROFITS` is accepted while the new target is
  farther from the mark than the old target. This should be impossible; its
  presence means the guard is not wired and the invariant is fiction.
- **Shadow blocks decisions:** any exit decision is missing, or a cycle is
  delayed, because the shadow write raised. Shadowing is not allowed to affect
  exits under any condition.
- **Decision collapse:** the exit-decision rate over 72h falls below **50%** of
  the pre-deploy 7-day mean, with no market explanation. Before rolling back,
  count `TP_WIDEN_REJECTED` rows — if they explain the drop, the finding is P2
  rejecting real proposals, which is a Phase 04 question, not a rollback
  reflex.

### 8.3 Rollback consequences to expect

- Reverting the main image restores `ema_trend_20_50` and `rsi_mean_reversion`;
  the signal rate rises and the −0.736R family returns. That is the intended
  meaning of rolling back the retirements.
- P2 disappears; widening proposals are accepted again. No historical decision
  changes either way.
- The P3 shadow file stops growing; existing rows are untouched.
- Rolling back the mirror does not un-send records already delivered.

---

## 9. Status and authorization

- **AUTHORIZED 2026-10-24.** The standing no-deploy policy is lifted for this event by operator
  decision; Window 1 was terminated early to enable it.
- **The agent has not deployed, pushed, restarted, or written to production.** Everything in the
  repo is local; `HEAD` is `f80ff11` plus the docs-only commits of this record.
- Target `HEAD` at push time; runtime code is `f80ff11` (docs-only commits may follow). 61 commits
  since the last confirmed production reference `f2dfde8`, at least 32 since `49ff810`.
- Order is **main → verify → mirror → verify** (§0.1). Wave 1 and Wave 2 ship as one image; the
  mirror is a separate unit.
- Railway auth is valid as of 2026-10-24, so the running revision can now be confirmed.
- **Window 2 starts on the successful main deploy** and is defined in
  `reports/OBSERVATION_PROTOCOL_2026-10.md` §7. The deploy's success is the window's start gate.
- The open input is no longer Phase 04 direction; it is **the deploy result**, then Phase 04.
