# Deploy Readiness — Waves 1 and 2

**Future deploy plan. Execution is blocked until Phase 04 completes AND the
operator explicitly authorizes a deploy event.** No deploy, no production write,
and no Railway read has been performed.

**Evidence base for every claim below:** 11 days (2026-10-08 → 2026-10-19), 204
entry signals, one regime (bearish 157 / neutral 47), UNIUSDT-dominated exit
population. R-weighted where a policy claim is made. No claim is a 30-day result.

---

## 0. Deploy units and scope

| unit | service | start command | volume |
|---|---|---|---|
| main | `crypto-alrtbot` | `python -m src.service` | `/app/data_store` |
| mirror | `pariston-mimic` | `python -m mirror` | `/app/mirror_data` |

Production reference: **2026-10-13**. Last confirmed mirror revision:
**`f2dfde8`**. The mirror is uploaded from the local tree with `railway up` —
GitHub auto-deploy does not fire. The main service's running revision is
**unconfirmed** (Railway auth expired).

**37 commits** since `f2dfde8`; **20 touch runtime code**. 11 days / 204 signals /
one regime / UNIUSDT-dominated applies to all behaviour claims.

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

---

## 6. Pre-deploy operator actions

All are operator actions; none has been performed.

1. **`railway login`** — auth is expired. Until refreshed, the running revision
   of each service cannot be confirmed and none of the post-deploy checks can be
   run.
2. **Confirm the running revision per service.** Do not assume `f2dfde8` for the
   mirror or any particular commit for main.
3. **Confirm `INVO_TRADING_REFRESH` is set on `pariston-mimic`.** Without it,
   W6 queues records safely but delivers none — `post()` raises
   `no trading session (refresh failed)` on every attempt.
4. **Confirm Invo dedup on `clientTxId`** (§4). One deliberate duplicate
   submission of a harmless record settles it.
5. **Authorize the deploy event** — only after Phase 04 completes and the
   standing deploy policy is lifted. Wave 1 and Wave 2 may then ship together or
   in sequence; the mirror may ship independently of both.

Env vars: **Wave 1 mirror** — `INVO_TRADING_REFRESH` (required),
`INVO_TRADING_TOKEN` (optional). **Wave 1 main** — no new variables.
**Wave 2** — no new variables.

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
`railway up`. Do **not** `git revert` the 37-commit range as a unit — it mixes
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

- **Standing deploy policy: no deploy until Phase 04 completes AND the operator
  explicitly authorizes a deploy event.** This document is a future plan, not an
  imminent execution.
- Nothing here has been deployed, run against production, or pushed. All commits
  are local.
- Wave 1 and Wave 2 are separable; the mirror is separable from both.
- Railway reads remain blocked until `railway login` is refreshed, so the running
  main-service revision cannot be confirmed before then.
- The open input is **Phase 04 direction**, not deploy authorization.
