# Crypto-AlrtBot — Project Handoff Summary

**Date (UTC):** 2026-10-13 · **Branch:** `master` · **HEAD:** `f2dfde8` ("Unwrap history post update shape") · **Tree:** clean + this file
**Full docs:** [CURRENT_STATE.md](CURRENT_STATE.md) · [ENGINEERING_CONTEXT.md](ENGINEERING_CONTEXT.md) · [ROADMAP.md](ROADMAP.md) · [HANDOFF_FOR_AI.md](HANDOFF_FOR_AI.md)
**Labels:** VERIFIED = confirmed from artifacts/commands · REPORTED = prior report, not re-verified · INFERRED = interpretation · UNKNOWN = indeterminable.

---

## 1. What this project is

Deterministic Binance USDT-perp futures signal bot (Python ≥3.10): 15-minute scans → 6 strategy families → volume/OI/funding gates → risk engine → confidence scoring → Telegram alerts. Paper-only by default (Invo paper-share, $1,000 virtual portfolio, Exit Orchestrators in monitor-only mode). Real-money paths exist (`dex/position/*`, Hyperliquid `HlBridge`, `mimic_open_once.py --live`) behind flags. Entry: `python -m src.service` (dashboard + scheduler). Sibling: `python -m mirror` — Pariston-mimic bot (watch detect-only / auto executor).

## 2. Current state (VERIFIED)

- **HEAD** `f2dfde8` (2026-10-13); prior: `fd65505` (Pariston-edit ground-truth sync), `eeafd4a` (single-TP end-to-end), `c2eb718` (exit→Invo sync), `ae66879` (exits + stability gate), `8d07507` (Railway live bot).
- **Deps** (`requirements.txt`): requests, dotenv, pandas, pydantic, hyperliquid-sdk, eth-account. Note: `pyproject.toml` omits the HL/eth deps (drift — install via requirements).
- **Tests:** 458 collected (`pytest --collect-only`); full suite not run in audit; `tests/test_exits.py` (~999 lines) is the exits spec; mirror suites (40 tests) last passed same-day after the `update`-unwrap fix.
- **Persistence:** `data_store/` (signals, scheduler 6h-cooldown ledger, scan history cap 1000, outcomes, paper portfolio, exit decisions, invo_*, telegram dead-letter cap 200, 75MB SQLite history) and `mirror_data/` (mirror ledger/audit/events, own Invo session). All git-ignored → single-machine backup risk.
- **Deployment:** no Railway definitions in repo. Verified via Railway API same-day: `crypto-alrtbot` (main, `/app/data_store`) + `pariston-mimic` (`python -m mirror`, `/app/mirror_data`). GitHub auto-deploy did NOT fire on push — deploy via `railway up`. Mirror live at HEAD: UNI TP synced on-chain `8.24844 → 8.114131` (+ ADA fix) same day; poll 15s, `MIMIC_LIVE=true`. Railway flag values for money paths UNKNOWN from repo — verify before trusting "paper-only".

## 3. Architecture (VERIFIED)

- **Data:** `src/data/binance_client.py` → fapi.binance.com (exchangeInfo, klines, openInterest(+Hist 15m×10), fundingRate/premiumIndex, 24hr ticker). Replay via `CachedFuturesClient` (`ts <= end_time`, no future leak; `get_mark_price` mocked 0).
- **Universe:** 14 coins; 8 validated (BTC/ETH/SOL/XRP/BNB/DOGE/ADA/AVAX), 6 DATA_MISSING (ZEC/HYPE/UNI/NEAR/LINK/DOT). `LIVE_REQUIRE_VALIDATED` (default true) gates live scans. Timeframes 4h/1h/15m; all strategies evaluate 15m.
- **Strategies** (`src/strategies/`): donchian, momentum, ema_trend (only regime veto), bollinger_atr, rsi_mean_reversion (**TP 1.5R — always fails `min_rr=2.0` gate, effectively dead**), volume_breakout. All others TP 2R.
- **Risk** (`src/engines/risk_engine.py`): $1,000 / 1% risk / 10% margin / max-lev-10 → $10/trade, size = 10/risk_distance. Liq: LONG `entry*(1-1/lev+0.004)` (SHORT mirrored).
- **Scoring** (weights=100, floor 60): regime 20, structure 15 (**always +10**), momentum 15 (**always +7 — `price_trend` never set**), volume 15, OI 15 (**SHORT broken — trend hardcoded `"up"`**), funding 5, liquidity 5 (always), RR 10. Strategy confidence discarded.
- **Lifecycle** (`src/models/lifecycle.py`): GENERATED → WAITING_FOR_ENTRY → ACTIVE → TP1_HIT/STOPPED_OUT/INVALIDATED/EXPIRED; single-TP full close; tracker passive (live driver UNKNOWN).
- **Settlement** (`evaluator.py` + `outcome_tracker.py`): entry ≤6 bars else EXPIRED; liq-first per bar; costs 0.04%+0.02% ×2 in R; CONSERVATIVE ambiguity (SL first); 100-bar max; paper books finals only, `pnl = net_R × $10`, no compounding.
- **Delivery:** validate → persist → format → Telegram (retries 1/2/4s, 400 fail-fast, `TELEGRAM_ENABLED` kill switch, dead-letter). 6h `symbol_side` cooldown.

## 4. Exit Orchestrators (VERIFIED)

`src/exits/`: config (5 env overrides + version hash) · models (PENDING/OPEN/PARTIAL/CLOSED/INVALIDATED; 8 actions; versioned single-TP plan) · policy (completed-candles-only) · 5 workers (Structure / Target / Stop / CounterSignal / RiskReward — propose-only, ABSTAIN on missing/stale input) · supervisor (data-first → safety → opposition-never-exits-alone → adjustments with 4h cooldown + ATR materiality → default NO_CHANGE; decision-key idempotency) · RiskGuard (10 invariants: safe-not-wise, no size/lev widening, geometry + economics gates) · monitor (event-driven: new candle/proximity/scheduled, freshness ≥25 bars & ≤1800s, ≤20 evals/cycle, never raises) · ledger (`exit_decisions.json`, atomic, 2,000-decision cap, restart-safe, fail-safe unhealthy→record-only) · harness (offline baseline-vs-candidate, bit-identity equivalence guard).
**Modes:** `EXITS_ENABLED` (default true) / `EXITS_APPLY` (default false → record-only, plans frozen). **Integration gap:** live settlement does NOT consume supervised plans (proven: `outcome_tracker.py:170-183`, `evaluator.py:54-58`) — plans affect only the harness + Invo paper TP/SL sync. Decisions link to outcomes audit-only.

## 5. Research evidence (VERIFIED numbers from artifacts)

- **Grid** (8 coins × 6 families × aware/neutral, 2026-07-07→10-05): momentum weakly positive on BTC (+0.124, PF 2.01, n=70)/SOL/XRP/BNB/DOGE (small-n, DD to ~49R); rest negative. Regime aware≡neutral 5/6 families (regime no-op evidence).
- **Baseline** (1,263 evals, 10 symbols, 11.1d): TRAIN exp −0.119 PF 0.78 / VAL +0.033 PF 1.07 / TEST −0.074 PF 0.87 → instability, not confirmation. Caution: evaluator single-TP-full-close ≠ advertised 50/25/25 text.
- **Exit V2** (n=188, ran 2026-10-11): base −0.1419 (CI −0.336/+0.051) vs candidate −0.113 (CI −0.298/+0.070), Δ **+0.0289, CIs include zero → inconclusive** per verdict rule. Changed 121 / reduced 37 / closed 32; exposure 38.5→28.9 bars; stops 0.495→0.394. V1 (n=8): Δ −0.0368, CI withheld.
- **Gaps:** coin-specific strategies 0/42 exact; walk-forward/robustness reports empty; OI history 100% missing; funding ignored in PnL; exact-fill/no-depth assumptions. Backtests ≠ profitability evidence.

## 6. Debt & risks

D1 OI-SHORT scoring wrong · D2 momentum half-credit always · D3 free structure +10 · D4 dead RSI · D5 Entry/SL/TP engines unused (dual truth) · double `mark_published` (`orchestrator.py:397,406`, INFERRED) · uncapped plans/events · `linked_outcome` unsurfaced · auto-deploy broken · evidence single-copy.

## 7. Roadmap & next action

**P0:** fix scoring-data bugs (D1+D2) · verify Railway money flags · repair/document deploy path · retire/repair RSI. **P1:** exit-integration decision (consume plans vs stay advisory) · eval V3 with power (pre-registered, same verdict rule) · dashboard exits surfacing. **P2:** real walk-forward, coin-specific matches, DATA_MISSING backfill, baseline/harness parity, engine consolidation. **P3:** BE/trail parity, Jesse validation, funding-aware PnL.
**ONE next task: P0.1 scoring fix** — pass real trend into OI + scoring, add SHORT/momentum unit tests, full suite green, watch 2–3 live cycles (revert = rollback). Approval required (live behavior). Why first: corrupts every live selection today; exit evidence on mis-scored candidates is suspect.

## 8. Rules for the next assistant

Label claims VERIFIED/REPORTED/INFERRED/UNKNOWN · never invent numbers/commits · no secrets (names only) · no commit/push/deploy without approval · no long backtests unasked · don't repeat completed evals as new work · don't present V2 as proof of edge · continue cmds: `git log --oneline -8`, `pytest --collect-only`, `pytest tests/test_exits.py -q`, reports `EXIT_EVAL_V2.md`/`baseline.md`/`research_summary.md`.
