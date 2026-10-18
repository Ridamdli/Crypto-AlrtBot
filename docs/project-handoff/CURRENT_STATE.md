# CURRENT_STATE.md — Crypto-AlrtBot project snapshot

**Snapshot date (UTC):** 2026-10-13
**Purpose:** verified current-state record for handoff. See also [ENGINEERING_CONTEXT.md](ENGINEERING_CONTEXT.md), [ROADMAP.md](ROADMAP.md), [HANDOFF_FOR_AI.md](HANDOFF_FOR_AI.md).

**Claim discipline:** VERIFIED = confirmed from current artifacts/commands in this audit. REPORTED = stated in a prior report, not re-verified. INFERRED = reasonable interpretation. UNKNOWN = cannot be determined.

---

## 1. Repository state (VERIFIED)

- **Root:** `C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot` — project `crypto-alrtbot` v1.0.0 (`pyproject.toml`), deterministic Binance USDT-perp futures signal generator with Telegram delivery.
- **Branch:** `master`. **HEAD:** `f2dfde8a5de98203a9db3a636f764a9105e21ff2` (2026-10-13) — "Unwrap history post update shape (fixes blind edit sync, UNI TP)".
- **Working tree:** clean (`git status --short` empty), no stash entries.
- **Recent commits (VERIFIED via `git log`):**
  - `f2dfde8` history-post `update` unwrap (mirror edit-sync fix)
  - `fd65505` Pariston-edit ground-truth sync via history detail
  - `ff285b9` dashboard bridge log TP/SL/ids/error details
  - `8fbcd79` persistent event history + log reader
  - `eeafd4a` single-TP bot end-to-end (signals, exits, tracking, display)
  - `c2eb718` sync applied exit decisions to Invo paper positions
  - `ae66879` exit orchestrators + stability gate + validated-coin live gate + Telegram hardening
  - `8d07507` live 14-coin bot on Railway with dashboard/tracking/paper trading
- **Git history depth:** `git log -- src/exits/` starts at `ae66879`; `src/backtest|research` at `eeafd4a`/`8d07507`; `reports/` has no tracked history output shown (reports are git-ignored — see §7).
- **Ignored/generated (VERIFIED via `git check-ignore` + `.gitignore`):** `data_store/`, `mirror_data/`, `*.db`, `reports/*.json`, `reports/*.md` (except README), `*.log`, `.env`. I.e. all research evidence, ledgers, and DBs are local-only with backup risk.

## 2. Dependencies & runtime (VERIFIED)

- **Runtime:** Python ≥3.10. Deps (`requirements.txt`): `requests`, `python-dotenv`, `pandas`, `pydantic`, `hyperliquid-python-sdk>=0.24.0`, `eth-account`; dev: `pytest`, `pytest-cov`. Note: `pyproject.toml` omits the Hyperliquid/eth-account deps (config drift — installer should use `requirements.txt`).
- **Entry points:**
  - `python -m src.service` — combined production process (dashboard thread + `BotScheduler().start_loop()`); Docker `CMD` (`Dockerfile:16`). `PORT` default 8080, `SCAN_INTERVAL_SECONDS` default 900.
  - `python -m src.scheduler [--once|--deep-scan|--interval N]` (default 900s), `python -m src.main` (one-shot legacy), `python -m src.dashboard`, `python -m mirror` (watch default; `MIRROR_MODE=auto` for mimic executor).
- **Config:** `src/config.py` (universe, thresholds, risk, scoring weights) + env overrides; only var names recorded here (no secret values inspected).
- **Persistence:** main bot under `DATA_DIR` (default `data_store/`): `signals_YYYY-MM-DD.json`, `scheduler_state.json` (6h `symbol_side` cooldown ledger), `scan_history.json` (cap 1000), `outcomes.json`, `paper_portfolio.json` (virtual $1,000), `exit_decisions.json`, `invo_*.json/jsonl`, `telegram_dead_letter.json` (cap 200), `historical/historical_data.db` (~75MB SQLite), `evaluations/`. Mirror under `MIRROR_DATA_DIR` (default `mirror_data/`): `mirrored.json`, `mirror_audit.jsonl` (cap 5000), `mirror_events.jsonl` (cap 5000), own `invo_session.json`. Mirror never touches `data_store/` (`mirror/store.py:1-9`).

## 3. Signal engine (VERIFIED except where noted)

- **Data:** `src/data/binance_client.py` → `https://fapi.binance.com`: `exchangeInfo`, `klines`, `openInterest` + `openInterestHist` (15m×10), `fundingRate` (hist) / `premiumIndex` (live), `ticker/24hr`. 3 retries, 10s timeout. Replay uses `CachedFuturesClient` (SQLite, `timestamp <= end_time`, no future leak); `get_mark_price` mocked to 0 (limitation).
- **Universe:** 14 coins (`src/config.py:35-50`): 8 validated AVAILABLE (BTC/ETH/SOL/XRP/BNB/DOGE/ADA/AVAX) + 6 DATA_MISSING (ZEC/HYPE/UNI/NEAR/LINK/DOT). `LIVE_REQUIRE_VALIDATED` (default true) gates live scans to the 8. Explicit `symbols=` calls bypass volume/validated gates (research path).
- **Timeframes:** 4h regime, 1h trend, 15m entry (`src/config.py:54-57`); all 6 strategy families evaluate 15m (`src/strategies/__init__.py:27-34`).
- **Strategies** (`src/strategies/`): `donchian` (20-bar breakout + 1.5× vol), `momentum` (2% move + trend), `ema_trend` (20/50 + pullback, only regime-vetoing strategy), `bollinger_atr` (breakout + compression), `rsi_mean_reversion` (RSI14 30/70 reversal, **TP 1.5R — always fails the pipeline `min_rr=2.0` gate**), `volume_breakout` (structure + 2× vol). Per-symbol priority lists in `src/config.py:36-49`. All TP 2R except RSI.
- **Gates:** volume pre-filter (ratio ≥1.5 over 20 bars, `src/pipeline.py:252-259`); OI `optional` mode never rejects; funding ±1% penalty 15; RR recompute drops `rr<2.0`.
- **Scoring** (`src/engines/scoring_engine.py`, weights sum 100, floor 60): regime 20, structure 15 (always +10 — free credit), momentum 15 (always +7 — `price_trend` never set by pipeline), volume 15, OI 15 (SHORT side broken — `price_trend` hardcoded `"up"`, `src/pipeline.py:265,315`), funding 5, liquidity 5 (always), RR 10. Strategy-level confidence (68–75) discarded.
- **Risk** (`src/engines/risk_engine.py`, $1,000 account, 1% risk, 10% target margin, max lev 10): risk $10/trade, size = 10/risk_distance, leverage = min(floor(notional/100)+1, 10). Liq: LONG `entry*(1-1/lev+0.004)`, SHORT mirrored (`src/papertrade/liquidation.py`).
- **Signal contract** (`src/models/signal.py`): id `SIG-YYYYMMDD-SYM-HASH6`, entry/TP/SL, leverage/margin/notional, confidence, conditions/invalidation/management text; geometry validators enforce SL<entry<TP (LONG).
- **Lifecycle** (`src/models/lifecycle.py`): GENERATED → WAITING_FOR_ENTRY → ACTIVE → TP1_HIT / STOPPED_OUT / INVALIDATED / EXPIRED; TP2/TP3 legacy, single-TP full close. Tracker is passive (live driver UNKNOWN — not verified in this audit).
- **Settlement** (`src/backtest/evaluator.py` + `src/tracking/outcome_tracker.py`): entry within 6 bars else EXPIRED; per-bar liq-first, then TP/SL touch; costs 0.04% taker + 0.02% slip ×2 round-trip in R; CONSERVATIVE ambiguity (SL first); max 100 bars; finality requires terminal status or 106 elapsed bars. Paper books only final outcomes, `pnl = net_R × $10`, no compounding (`src/papertrade/portfolio.py`).
- **Delivery:** pipeline → validate → persist → format → Telegram (`POST sendMessage`, Markdown, retries 1/2/4s, 400 fail-fast, `TELEGRAM_ENABLED` kill switch, dead-letter cap 200). 6h `symbol_side` cooldown dedup (`src/scheduler.py`, `src/orchestrator.py`).

## 4. Exit Orchestrators (VERIFIED from `src/exits/` + scheduler)

- **Modules:** `config` (thresholds, 5 env overrides, config-version hash), `models` (PositionState PENDING/OPEN/PARTIAL/CLOSED/INVALIDATED; 8 ExitActions; single-TP ExitPlan with genesis + versioning), `policy` (completed-candles-only primitives), `workers` (5 assessors, propose-only), `supervisor` (ordered doctrine + stability gate + decision keys), `risk_guard` (10 invariants, certifies safe-not-wise), `monitor` (event-driven supervision in scheduler loop), `ledger` (`data_store/exit_decisions.json`, atomic, cap 2000 decisions, restart-safe, fail-safe unhealthy→record-only), `harness` (offline baseline-vs-candidate comparison with bit-identity equivalence guard).
- **Workers:** Structure (stop-breach close / 6-bar pending-window invalidate / 4-of-5 adverse closes = evidence only), Target (pull TP beyond 6 ATR, economics gates), Stop (tighter-only, ≥0.25 ATR room, never beyond entry), CounterSignal (reversal = opp-break-20 AND 4-of-5 adverse; exit only if damage ≤-0.75R... precisely: CLOSE only when reversal + unrealized ≤ -0.75R, else FLAG), RiskReward (all-nets<0 + downside>2×upside → CLOSE; nets<0.5 → REDUCE 50%). Missing required input → ABSTAIN (quality 0); stale data → workers abstain and supervisor emits INSUFFICIENT_DATA (data-first doctrine).
- **Modes:** `EXITS_ENABLED` default true (scheduler gate; off = no supervision at all). `EXITS_APPLY` default false (monitor gate; off = decisions recorded, plans frozen). Live settlement (`OutcomeTracker`) **does not consume supervised plans** (VERIFIED: `outcome_tracker.py:170-183`, `evaluator.py:54-58`, monitor docstring) — supervised plans affect only the offline harness and Invo paper TP/SL sync. This is the principal integration gap (see ROADMAP).
- **Invo paper path:** applied plans → TP+SL modify + supervisor CLOSE via `scheduler._sync_exits_to_invo`, requires `INVO_PAPER_ENABLED=true` + live (non-dry) + `EXITS_APPLY=true`; REDUCE/FLAG/INVALIDATE never posted. Decisions link to final outcomes as audit-only `linked_outcome` (`monitor.py:106-111`).
- **Tests:** `tests/test_exits.py` (~999 lines) covers policy/workers/supervisor/guard/ledger/monitor/harness incl. equivalence, idempotency, fail-safe. Untested (VERIFIED by inspection): env-override loading, `config_version` determinism, FLAG/INSUFFICIENT_DATA apply paths, `max_evals_per_cycle` ordering, `proximity_atr` branch, PARTIAL live paths.

## 5. Research & backtests (VERIFIED from `reports/` artifacts)

- **Generic grid** (`research_summary.md`, 96 runs, 8 coins × 6 families × aware/neutral, 2026-07-07→2026-10-05, 8,673 15m candles/coin): momentum positive only on BTC (+0.124, PF 2.01, n=70), SOL, XRP, BNB, DOGE (small-n, high variance); everything else negative. Regime aware≡neutral bit-identical for 5/6 families (regime input no-op evidence).
- **Baseline** (`baseline.md` + `baseline.json` 1,263 evals, 10 symbols, 11.1d): TRAIN exp −0.119 PF 0.78 / VAL +0.033 PF 1.07 / TEST −0.074 PF 0.87. VAL-positive vs TRAIN/TEST-negative = instability, not confirmation.
- **Exit evals** (VERIFIED JSON): V1 n=8 (2026-09-14→10-04, BTC+ETH): base −0.5867 vs cand −0.6235, Δ −0.0368, CI withheld (n<10). V2 n=188 (8 coins, full window, ran 2026-10-11T15:49Z): base exp −0.1419 (CI −0.336/+0.051) vs cand −0.113 (CI −0.298/+0.070), **Δ +0.0289, both CIs include zero → inconclusive** per the report's own verdict rule (adopt only if Δ>0 with CI excluding zero). Supervisor changed 121 / reduced 37 / closed 32; exposure 38.5→28.9 bars; stop freq 0.495→0.394.
- **Coverage:** 103 `research_*.json` files; coin-specific strategies NOT implemented (matrix: 42 assignments, 0 exact matches). Walk-forward/robustness reports empty/degenerate (`walk_forward.md` header-only, `robustness.md` all-zeros). `DATASET_VALIDATION.md` (2026-10-06): 8/8 symbols 100% in-range, OI history 100% missing (REPORTED in FINAL report), funding ~540 pts/symbol.
- **Bias risks:** look-ahead gated by `as_of_time` + completed-candle-only policy (REPORTED, determinism asserted not re-verified); survivorship (tradable-only universe); no funding payments in evaluator; exact-price limit-fill assumption; baseline single-TP-full-close vs advertised 50/25/25 split (R/PF don't match described management — do not size on these numbers).

## 6. Tests (VERIFIED)

- **458 tests collected** (`pytest --collect-only`, 23 files). Full suite **not executed** in this audit (bounded collection only, per task constraints).
- Last observed focused run (earlier today, same session): `test_mirror_auto.py` + `test_mirror_feed.py` — 40 passed after the `update`-unwrap fix.
- Previously REPORTED full passes are historical; treat as REPORTED, not current proof.

## 7. Deployment & operations (mixed; labeled)

- **Repo-proven:** Docker `CMD python -m src.service`; mirror runs as `python -m mirror`; dashboard 8 endpoints + `/healthz`, `ADMIN_TOKEN` auth; no Railway service definitions in repo (no Procfile/railway.json/toml — VERIFIED absent).
- **VERIFIED via Railway API earlier today (same session):** project `crypto-alrtbot` with services `crypto-alrtbot` (main, volume `/app/data_store`) and `pariston-mimic` (volume `/app/mirror_data`, start `python -m mirror`). `pariston-mimic` deployed from local tree via `railway up` (GitHub auto-deploy did not fire on push). Local tree == deployed code at HEAD `f2dfde8` for the mirror service only; main service sync state UNKNOWN.
- **VERIFIED live today:** UNI TP edit synced on-chain (`tpsl resting UNI LONG tp=8.114131`, replaced 2 triggers); ADA also synced. Mirror runs `MIMIC_LIVE=true`, poll 15s.
- **Live vs paper (code-proven):** live = Binance scans, Telegram, dashboard, Invo reads. Paper-only = Invo paper-share/modify/sell, $1,000 PaperPortfolio, exits supervision (APPLY=false default), mirror dry-run default. Real-money paths exist (`dex/position/*`, `HlBridge`, `mimic_open_once.py --live`) behind flags — current Railway flag values UNKNOWN from repo; verify in dashboard/cloud before trusting "paper-only".
- **Failure behavior (code-admitted):** 3× scan retry then failed heartbeat; Telegram dead-letter; Invo 401 → fresh OTP next cycle (1800s OTP cooldown); exit-sync retryable/permanent classification; unreadable board aborts before any close (never flattens on ambiguity); per-cycle catch-alls in both bots.

## 8. Known limitations (VERIFIED)

1. OI SHORT scoring broken (`price_trend` hardcoded `"up"`); momentum confidence always half-credit (`price_trend` unset). Both distort live signal selection NOW.
2. RSI 1.5R can never pass the 2.0R gate (dead strategy in practice).
3. `EntryEngine`/`StopLossEngine`/`TakeProfitEngine` instantiated but unused in pipeline (strategies inline duplicates) — dual sources of truth.
4. Baseline evaluator single-TP-full-close ≠ advertised 50/25/25 management text.
5. Supervised exit plans don't reach live settlement (integration gap).
6. `pyproject.toml` omits Hyperliquid/eth-account deps (use `requirements.txt`).
7. GitHub→Railway auto-deploy did not trigger (manual `railway up` needed).
8. All evidence (reports/DBs/logs) git-ignored — single-machine backup risk.
