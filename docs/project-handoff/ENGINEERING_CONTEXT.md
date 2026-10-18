# ENGINEERING_CONTEXT.md — decisions, invariants, debt

Companion to [CURRENT_STATE.md](CURRENT_STATE.md). Same claim discipline (VERIFIED / REPORTED / INFERRED / UNKNOWN).

---

## 1. Settled decisions (do not casually reverse)

1. **Single-TP bot** (`eeafd4a`): one target end-to-end; TP2/TP3 are legacy display only. Rationale: pipeline `min_rr=2.0` + evaluator full-close at TP1. Reversing requires evaluator + ledger + dashboard + Invo-sync changes together.
2. **Paper-only default everywhere**: `INVO_DRY_RUN=true`, `INVO_PAPER_DRY_RUN=true`, `EXITS_APPLY=false`, `MIMIC_LIVE=false`, two-key live guards (`mimic_open_once.py` env + `--live`; `__main__.py` refuses live without execution creds). Real-money paths exist but must never activate by accident.
3. **Exits monitor-only until proven**: `EXITS_APPLY=false` default; adoption verdict rule in exit-eval scope ("adopt only if Δ>0 with 95% CI excluding zero"). V2 was inconclusive → plans stay advisory. Do not flip APPLY on V2 evidence.
4. **Ground-truth over feed for mirror edits**: feed posts redact TP/SL for non-owners; sims rows carry none; history detail (`fetch_position_history`, fields under `update`) is authoritative. Feed = fast path, history = ground truth every ~4th cycle. Verified live on UNI/ADA 2026-10-13.
5. **Never flatten on ambiguity**: unreadable board aborts before any close (`mirror/auto.py`, `scheduler.py:236-243,517-530`); fills stand on record/TPSL failure. This doctrine is load-bearing for unattended operation.
6. **First-poll indexing**: watchers index existing posts, only new posts become signals (no retro-mimics).
7. **Completed-candles-only** for all exit math (`policy.split_completed`; forming candle always excluded) — the core anti-look-ahead invariant.
8. **Atomic persistence**: tmp-file + `os.replace` for all ledgers; append-only capped audit logs; fail-safe to in-memory blank + `healthy=False` (monitor then refuses to apply).
9. **6h `symbol_side` cooldown** dedup (scheduler + orchestrator share `scheduler_state.json`); 60s dashboard-signal cache; 15m scan cadence; 4h exit-adjust cooldown; 5m min re-eval.
10. **Secrets hygiene**: tokens/keys never in repo (`.env` + Railway vars only); dashboard never exposes env; sync-audit redacts secrets; handoff/docs record names + presence only.

## 2. Module relationships (VERIFIED)

```
Binance fapi → binance_client → ohlcv_fetcher / pipeline
  → universe_filter × symbol_registry (eligibility)
  → btc_regime (BTC 4h) → strategies/* (candidates)
  → volume/OI/funding engines (gates) → risk_engine (size/lev/RR)
  → scoring_engine (rank) → SignalModel → persistence → Telegram
  → scheduler: cooldown dedup → outcomes (OutcomeTracker+evaluator)
     → paper portfolio → Invo paper-share → exits monitor → Invo exit-sync
```

- `orchestrator.py` = batch/research-facing pipeline wrapper (conflict resolution, `top3_by_coin.json` override, double-`mark_published` bug at `:397,406` — INFERRED bug, needs a test to confirm impact).
- `scheduler.py` = live loop owner: scan → publish → track → share → supervise → sync. `service.py` = process wrapper (dashboard thread + loop).
- `src/exits/monitor.py` consumes outcome-classified positions + klines fetcher; writes only `exit_decisions.json`; `scheduler._sync_exits_to_invo` is the sole external consumer besides the harness.
- `mirror/` is independent: own Invo login, own `mirror_data/`, own loop. Only coupling to main bot: optional dashboard-signal fallback for TP/SL resolve + `MIMIC_MAIN_*` env.

## 3. Lifecycle semantics (VERIFIED)

- Signal states: GENERATED → WAITING_FOR_ENTRY → ACTIVE → TP1_HIT/STOPPED_OUT (+INVALIDATED/EXPIRED). Tracker passive; live WAITING→ACTIVE driver not verified (gap).
- Position derivation for exits (`models.classify_position`): no outcome → PENDING; final+entered → CLOSED; final+!entered → INVALIDATED; entered → OPEN. PARTIAL legacy-only.
- Outcome finality: terminal status OR (EXPIRED + ≥6 future bars) OR ≥106 elapsed bars. Young truncated signals never finalize (prevents premature verdicts).
- Exit decisions link to outcomes audit-only (`linked_outcome`); outcomes never mutate past decisions. Dashboard does not surface `linked_outcome` (gap).

## 4. Research assumptions & historical findings (REPORTED unless noted)

- 8-coin validated universe, 2026-07-07→10-05, 15m/1h/4h Binance perps; OI history 100% missing (filter neutral); funding available but ignored in PnL.
- Momentum weakly positive on 5/8 (small-n, DD up to ~49R); all else negative; VAL-positive/TEST-negative instability.
- Exit V2 (VERIFIED numbers): Δ +0.0289, CIs include zero → inconclusive; supervisor cut exposure 38.5→28.9 bars and stop freq 0.495→0.394 with PF 0.784→0.819 — directionally favorable, statistically unproven.
- Backtest R/PF assume exact-price limit fills, no funding, no depth/spread — not profitability evidence.

## 5. Technical debt register (VERIFIED by inspection)

| # | Debt | Location | Live impact |
|---|---|---|---|
| D1 | OI `price_trend` hardcoded `"up"` | `pipeline.py:265,315` | SHORT OI scoring wrong |
| D2 | Momentum `price_trend` never set (always +7) | `pipeline.py` → `scoring_engine.py:32-36` | confidence deflated |
| D3 | Structure +10 unconditional | `scoring_engine.py:29-30` | confidence inflated |
| D4 | RSI 1.5R vs 2.0R gate (dead strategy) | `rsi_mean_reversion.py:174,229` | wasted compute, misleading menu |
| D5 | Entry/SL/TP engines unused in pipeline | `entry_engine/sl_engine/tp_engine.py` | dual truth, drift risk |
| D6 | Strategy `confidence` discarded | `pipeline.py` | dead field |
| D7 | TP2/TP3 legacy fields/states | `models.py`, `lifecycle.py` | confusion, display risk |
| D8 | Orchestrator double `mark_published` | `orchestrator.py:397,406` | INFERRED, needs test |
| D9 | `plans/events/invalidated/last_eval` uncapped | `ledger.py` | slow unbounded growth |
| D10 | `linked_outcome` unsurfaced | dashboard | exits unauditable in UI |
| D11 | `pyproject.toml` missing HL deps | `pyproject.toml` vs `requirements.txt` | fresh-install breakage |
| D12 | GitHub auto-deploy not firing | repo↔Railway | stale-prod risk |
| D13 | Evidence all git-ignored, single copy | `.gitignore`, 75MB SQLite | loss risk |
| D14 | `CachedFuturesClient.get_mark_price` = 0 | `cached_client.py:101-115` | research/live funding-key mismatch risk |
| D15 | Live lifecycle driver unverified | lifecycle wiring | UNKNOWN who moves WAITING→ACTIVE live |
| D16 | `FuturesDataService` instantiated, never called in `run_pipeline` | `pipeline.py:55` | dead path or hidden use |
