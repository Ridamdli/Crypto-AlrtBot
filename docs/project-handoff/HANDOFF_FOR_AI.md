# HANDOFF_FOR_AI.md — continuation brief for a future coding assistant

Read [CURRENT_STATE.md](CURRENT_STATE.md) for the full snapshot, [ENGINEERING_CONTEXT.md](ENGINEERING_CONTEXT.md) for rationale/debt, [ROADMAP.md](ROADMAP.md) for priorities. This file is the short version.

---

## Objective & constraints

Crypto-AlrtBot = deterministic Binance USDT-perp futures signal bot → Telegram alerts. Paper-only by default. Goals: reliable autonomous signals; auditable deterministic risk; Exit Orchestrators that beat static TP/SL **with proof first**; minimal cost, no paid LLM; never real-money without explicit approval. Hard rules: don't invent results/commits/metrics; don't print secrets (names + presence only); don't commit/push/deploy without authorization; don't run long backtests/sweeps unasked; preserve user work; evidence labels VERIFIED / REPORTED / INFERRED / UNKNOWN.

## Verified state & commit (2026-10-13)

- Branch `master`, HEAD `f2dfde8` ("Unwrap history post update shape"), tree clean. Repo: `C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot`.
- Stack: Python ≥3.10, `requirements.txt` (requests/dotenv/pandas/pydantic/hyperliquid-sdk/eth-account). Entry: `python -m src.service` (dashboard + 15m scheduler loop). Mirror: `python -m mirror`.
- 458 tests collected (`pytest --collect-only`); full suite not run in audit. `tests/test_exits.py` (~999 lines) is the exits spec.
- Railway (verified via API same day): services `crypto-alrtbot` (main) + `pariston-mimic` (start `python -m mirror`). Mirror deployed at HEAD content via `railway up` (GitHub auto-deploy did NOT fire — known issue). Mirror live: UNI TP synced on-chain `8.24844 → 8.114131` same day.
- Research evidence (all git-ignored, local-only): 8-coin grid 2026-07-07→10-05; momentum weakly positive 5/8 (small-n); baseline TRAIN −0.119 / VAL +0.033 / TEST −0.074 expR; exit eval V2 n=188 Δ +0.0289, CIs include zero → **inconclusive, exits stay advisory** (`EXITS_APPLY=false` default).

## Implemented vs planned

- DONE: 15m scan loop, 6 strategies, volume/OI/funding gates, risk engine ($1k/1%/$100 margin/max-lev-10), confidence scoring, Telegram delivery + dead-letter, 6h cooldown dedup, outcome tracking + $1k paper portfolio, dashboard (8 APIs + `/healthz`), Invo paper-share + exit-sync, 5-worker Exit Orchestrators with supervisor/RiskGuard/ledger/harness, exit evals V1+V2, mirror watch + auto-mimic with feed fast-path + history ground-truth TP/SL sync + closes.
- NOT DONE: supervised plans do NOT feed live settlement (the integration gap); coin-specific strategies (0/42 exact); walk-forward/robustness (empty); DATA_MISSING coins unvalidated; RSI effectively dead (1.5R vs 2.0R gate); OI-SHORT + momentum scoring bugs open.

## Critical files

- `src/config.py`, `src/pipeline.py`, `src/strategies/`, `src/engines/` (scoring/risk/oi/volume/funding/btc_regime), `src/models/signal.py` + `lifecycle.py`, `src/scheduler.py`, `src/service.py`, `src/dashboard.py`, `src/tracking/outcome_tracker.py`, `src/backtest/evaluator.py|replay.py|tuner.py`, `src/papertrade/portfolio.py|liquidation.py`, `src/exits/` (config/models/policy/workers/supervisor/risk_guard/monitor/ledger/harness), `src/invo/client.py|exit_sync.py`, `src/utils/telegram_bot.py`, `mirror/auto.py|feed.py|feedmon.py|hl.py|store.py|config.py`, `scripts/eval_exits_v1.py`, `scripts/check_uni.py`, `reports/exit_eval_v2.json`, `reports/baseline.json`.
- Ledgers: `data_store/` (signals/outcomes/paper/exit_decisions/scheduler_state/scan_history/invo_*) + `mirror_data/` (mirrored.json/mirror_audit.jsonl). Historical DB: `data_store/historical/historical_data.db` (~75MB, ignored).

## Exits status & research limits

- Supervisor doctrine: data-first → safety → opposition-never-exits-alone → adjustments (4h cooldown, materiality) → default NO_CHANGE. RiskGuard: safe-not-wise (no size/lev widening, idempotency/stale keys, geometry + economics gates).
- Consumers of plans: offline harness + Invo paper TP/SL sync only. Live paper PnL = baseline plan. Don't claim exits manage live trades.
- Numbers are backtests with exact-fill/no-funding/small-n assumptions — never profitability evidence. Verdict rule: adopt only if Δ>0 with CI excluding zero.

## Known bugs / ops risks

D1 OI trend hardcoded `"up"` (`pipeline.py:265,315`); D2 momentum `price_trend` unset; D3 free +10 structure; D4 dead RSI; D5 unused Entry/SL/TP engines; double `mark_published` (`orchestrator.py:397,406`); uncapped plans/events; `pyproject` missing HL deps; evidence single-copy; Railway flag values unverified from repo.

## Priorities & next action

Roadmap: P0 scoring fixes (D1+D2) + verify Railway flags + deploy path + RSI; P1 exit-integration decision + eval V3 + dashboard surfacing; P2 walk-forward/coin-specific/DATA_MISSING/parity; P3 optional. **Next task: P0.1 scoring-data fix** (wire real trend into OI + scoring, unit tests, full suite green, watch 2–3 live cycles, revert = rollback). Approval required (live behavior). Do NOT do it unasked.

## Continue commands

- `git log --oneline -8`, `git status --short` (state)
- `python -m pytest tests/ --collect-only` (count; full `-q` only if safe/asked)
- `python -m pytest tests/test_exits.py -q` (exits spec)
- `python scripts/check_uni.py` needs Railway env (reads only levels, never secrets)
- Reports: `reports/EXIT_EVAL_V2.md`, `reports/baseline.md`, `reports/research_summary.md`, `reports/DATASET_VALIDATION.md`
- Never repeat completed evals as new work; never present V2 as proof of edge; never deploy.
