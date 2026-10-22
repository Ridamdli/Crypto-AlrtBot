# Deploy Readiness — W5–W10

**This document authorizes nothing.** No deploy, no production write, no Railway
read has been performed. Every step below runs only on explicit operator
authorization.

**Evidence base:** 11 days (2026-10-08 → 2026-10-19), 204 signals, one regime
(bearish 157 / neutral 47), UNIUSDT-dominated exit population. Nothing in this
checklist depends on a count-only reading; the behaviour claims are replay results.

---

## 1. What is deployed today, and what is not

Production reference date: **2026-10-13**. Last known deployed mirror revision:
**`f2dfde8`**. The mirror is uploaded from the local tree with `railway up` —
GitHub auto-deploy does not fire. The main service's exact running revision is
**unknown** and must be read from Railway after `railway login`; do not assume it
matches the local tree.

**37 commits** sit between `f2dfde8` and `HEAD`. 20 touch runtime code; the rest
are tests, scripts, docs, and lint.

### 1a. Runtime-affecting commits by service

**Main service (`crypto-alrtbot`, `python -m src.service`)**

| commit | change | risk |
|---|---|---|
| `12faa3c` | retry transient capital skips; un-bury seen coins | low |
| `c941c6c` | OI short + momentum scoring inputs | medium — changes signal selection |
| `d9212eb` | count only delivered Telegram sends | low |
| `b787459` | atomic ledger writes | low |
| `7542c9a` | zero evidence quality on abstains; RR_DEAD fix | medium — changes scoring |
| `d666c7c` | hash exit decision keys from clean proposal fields | low |
| `86e1fcb` | retain exit idempotency keys past the cap | low |
| `674c86b` | **fix ATR stop double-subtraction** (momentum, volume, EMA) | **high — changes stop levels and therefore R** |
| `fa79b58` | `as_of_time` NameError fix | low |
| `c90507a` | archive 7-day-old signal files; chronological merge | medium — moves files |
| `02aa4f3` | ruff fixes in `src` | low |
| `2bcf1b7` | corrupt daily signal files preserved; UTC day boundary | low |
| `3602856` | retire `rsi_mean_reversion` | medium — fewer signals |
| `52bf4b3` | momentum lookahead fix (forming candle excluded) | **high — changes SL lookback** |
| `49ff810` | persist `applied` on exit decisions | low |
| `a29831a` | **retire `ema_trend_20_50`** | **high — registry, config, deleted module** |
| `f62d9a1` | **P2 TP-widening rejection** | medium — can only reject, never widen |
| `95db541` | **P3 shadow log** | low — write-only |

**Mirror service (`pariston-mimic`, `python -m mirror`)**

| commit | change | risk |
|---|---|---|
| `fcfdc33` | **queue records when no trading session; flush oldest-first** | medium — new file, new drain path |

`f84872d` (dashboard P0) is dated 2026-10-13 and may or may not be in the running
main image — confirm before counting it as pending.

### 1b. Not deployed by any of this

- Exit management is closed (03-B-2). P2 is an invariant, not an exit redesign.
- No schema migration, no ledger rewrite, no data rewrite. All new files are
  additive.
- No change to the mirror's execution gate (`MIMIC_LIVE`) or to the main bot's
  live/paper flags.

## 2. Environment variables

### Main service — nothing new is required

`EXITS_ENABLED=true` and `EXITS_APPLY=false` keep supervision recording
proposals only; that is the intended state for W9/W10. `EXITS_STORAGE_DIR`
(default `data_store`) is already on the mounted volume.

**Operational caveat — P3 shadow path.** `src/exits/p3_shadow.py` writes
`mirror_data/p3_shadow.jsonl`, literally as specified. On the **main service**
the persistent volume is `/app/data_store`, not `/app/mirror_data`. The file will
be created inside the container's writable layer, survive until the next deploy,
and then reset. It cannot be read back from a volume. If the shadow sample is
meant to survive redeploys, the path needs to be configurable (one-line change) —
say so and I will make it `EXITS_P3_SHADOW_PATH` with the current value as the
default.

### Mirror service — `INVO_TRADING_*` required for W6 to deliver

`mirror/recorder.py` builds its session from:

| variable | role |
|---|---|
| `INVO_TRADING_REFRESH` | long-lived refresh JWT; **required** — without it every record queues and raises `no trading session (refresh failed)` |
| `INVO_TRADING_TOKEN` | optional short-lived access-token override |

`MIRROR_DATA_DIR` (default `mirror_data`) decides where
`pending_records.jsonl` lives; on the mimic that path is the mounted volume, so
the queue survives restarts.

**Behaviour without the refresh token:** the fix makes the failure
non-destructive — records are appended to the queue with `fsync` and the caller
still raises and alerts. It does **not** make them deliverable. Confirm the
variable is present on `pariston-mimic` before treating W6 as operational.

**W6 dedup — answered: there is none.** `flush()` has no `clientTxId`, no `oid`,
and no payload-hash check. It re-reads the whole queue, posts each row, and
rewrites whatever is left. A crash between a successful POST and the queue
rewrite will resend that row. This is documented in the module as
`shortcut: at-least-once … Invo must dedupe on it`. Consequence: **at-least-once
delivery**, not exactly-once. If Invo does not dedupe on `clientTxId`, a restart
during a flush can duplicate a paper record. This was already true of any retry
path; the queue makes the window larger, not new.

## 3. Post-deploy verification

Run in order; stop at the first failure.

1. **Liveness.** `GET /healthz` returns OK. Scheduler log shows a completed scan
   cycle with no traceback.
2. **Registry.** Startup log lists four families. `ema_trend_20_50` and
   `rsi_mean_reversion` appear nowhere in the registry or in today's signal file
   under `data_store/signals/`.
3. **Signal sanity.** New signals carry `signal_id`, `side`, `entry`,
   `stop_loss`, and a single `tp`. Spot-check one stop against the ATR formula —
   this is where the double-subtraction fix (`674c86b`) shows up; a stop that is
   one ATR too tight means the old code is running.
4. **Exit ledger.** New rows in `data_store/` exit decisions include `applied`
   and `applied_at`. `ADJUST_TAKE_PROFITS` rows show `plan_after.tp` closer to
   the mark than the prior target, never further.
5. **P2.** Grep new decisions for `TP_WIDEN_REJECTED`. Expected count: **0** —
   every historical ADJUST was a tightening (31/31 on replay). A non-zero count
   is not an error; it means a worker proposed a widening and the guard caught
   it. Log it for review.
6. **P3 shadow.** After the first pending invalidation, the file exists with one
   JSON row per invalidation containing `structure_pass`, `counter_pass`,
   `verdict`, `breach_margin_pct`. Invalidation itself still fires — P3 is
   write-only. Remember the ephemeral-path caveat in §2.
7. **Mirror queue.** With a valid `INVO_TRADING_REFRESH`, record a close and
   watch `mirror_data/pending_records.jsonl`: the line count must return to zero.
   The audit/event log must not gain new `record_close_failed` entries.
8. **Duplicate watch (at-least-once).** After any restart during a flush, compare
   record counts per signal in Invo. A duplicate is the known failure mode, not a
   regression; it is the reason `clientTxId` exists.

## 4. Rollback

**Preferred: redeploy the previous Railway deployment.** Both services were
uploaded from the local tree, so the prior image is the true previous state.
Redeploy from the Railway dashboard; do not rebuild from an untagged tree.

**If a rebuild is required:** check out the last known-good revision
(`f2dfde8` for the mirror; the confirmed main-service revision for the main bot)
and `railway up`. Do not `git revert` the 37-commit range as a unit — it mixes
tests, scripts, and lint with runtime changes and will produce conflicts.

**Rollback consequences to expect:**

- Reverting the image restores `ema_trend_20_50` and `rsi_mean_reversion`; the
  signal rate rises and the negative family returns. That is the intended
  meaning of rolling back W5, not a side effect to patch.
- P2 disappears: a widening proposal would be accepted again. No historical
  decision changes either way (31/31 replay).
- The P3 shadow file stops being written; the existing file is untouched.
- **No data rollback is needed or possible.** Exit ledgers are append-only
  overlays; the outcome ledger is written by the evaluator, not by this change
  set. `pending_records.jsonl` is a durable queue: rolling the mirror back
  leaves it in place, and either version will drain it (the old version simply
  fails to queue new ones during a credential gap).
- Rolling back the mirror does **not** un-send records already delivered.

**Verification after rollback:** repeat steps 1, 2, 4, and 7. Step 3 should now
show whichever stop formula the restored revision used.

## 5. Authorization

- Nothing in this document has been deployed, run against production, or pushed
  to a remote. All commits are local.
- The deploy strategy is still the operator's open decision: (a) deploy now,
  (b) deploy after Phase 04, (c) partial deploy now.
- The mirror change (`fcfdc33`) is independent of the main-service changes and
  can ship on its own. W9/W10/W5 all live in the main image and ship together
  with it.
- Railway reads remain blocked until `railway login` is refreshed; the running
  main-service revision cannot be confirmed before then.
