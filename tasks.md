# Project Milestones & Tasks: Crypto Daily Futures Signal Engine

This document outlines the high-level engineering milestones and actionable tasks for the development of the Crypto Daily Futures Signal Engine, based on the V1 PRD.

## Milestone 1: Project Foundation & Data Infrastructure
**Objective**: Establish the core architecture, data ingestion pipeline, and local data structures to support deterministic processing.
- [x] **Task 1.1: Project Setup** - Initialize Python repository, environment variables (`.env`), logging configuration, and standard directory structure (`src/`, `tests/`, `docs/`).
- [x] **Task 1.2: Binance API Client** - Implement a robust client for Binance USDT-margined perpetual futures (handling rate limits, retries, and authentication if needed).
- [x] **Task 1.3: OHLCV Fetcher** - Build the data service to fetch, standardize, and cache historical and current OHLCV data.
- [x] **Task 1.4: Futures Data Service** - Implement fetching for Open Interest, Funding Rates, and Mark Price data.

## Milestone 2: Market Universe & Regime Filtering
**Objective**: Build the dynamic symbol discovery mechanism and the macro market context engine to filter eligible trading environments.
- [x] **Task 2.1: Dynamic Symbol Discovery** - Implement the universe filter to dynamically fetch symbols and exclude based on age, volume, liquidity, and spread thresholds.
- [x] **Task 2.2: Timeframe Manager** - Create a service to synchronize and serve multi-timeframe data (4H, 1H, 15m) consistently.
- [x] **Task 2.3: BTC Regime Engine** - Develop the 4H market regime filter for BTC (bullish, bearish, neutral) using EMAs, market structure, and momentum indicators.

## Milestone 3: Core Signal & Detection Engines
**Objective**: Develop the individual, modular engines responsible for detecting specific setups and calculating precise trade parameters.
- [x] **Task 3.1: Volume Engine** - Implement deterministic volume expansion logic (e.g., `current_15m_volume / rolling_avg_15m_volume`).
- [x] **Task 3.2: Open Interest Engine** - Implement OI percentage change and price/OI divergence detection.
- [x] **Task 3.3: Entry Engine** - Develop entry calculation models (Market, Pullback, Breakout Retest, FVG) returning `entry_price` and `activation_condition`.
- [x] **Task 3.4: Stop-Loss Engine** - Build SL models (ATR-based and Structural support/resistance stops).
- [x] **Task 3.5: Take-Profit Engine** - Build TP calculation logic (TP1, TP2, TP3 based on R-multiples and structural targets).

## Milestone 4: Validation Pipeline & Risk Management
**Objective**: Integrate the isolated engines into a sequential, rigorous pipeline that enforces strict risk limits and filters for quality.
- [x] **Task 4.1: Validation Pipeline Logic** - Construct the sequential flow (`Candidate -> Eligibility -> Regime -> Structure -> 15m Conf -> Volume/OI -> Entry/SL/TP`).
- [x] **Task 4.2: Risk/Reward Engine** - Implement risk parameter calculations (R:R ratios, target margin, leverage constraints, and position notional limits).
- [x] **Task 4.3: Scoring & Ranking** - Develop the confidence scoring mechanism and logic to filter down to the top 1-3 best candidates.
- [x] **Task 4.4: Invalidation Generator** - Write the logic to explicitly generate setup validation and invalidation rules (e.g., "15m close below X").

## Milestone 5: Output, Formatting & Delivery
**Objective**: Guarantee that validated signals adhere to the strict Signal Contract and deliver them to the end user.
- [x] **Task 5.1: Signal Contract Validation** - Implement JSON schema validation to ensure every generated signal matches the strict required schema.
- [x] **Task 5.2: Telegram Formatter** - Create the UI/Markdown formatter to match the exact `🚀 SIGNAL` template from the PRD.
- [x] **Task 5.3: Telegram Integration** - Integrate the Telegram Bot API to broadcast signals to a configured channel/chat.
- [x] **Task 5.4: Signal Persistence Layer** - Implement a local database or JSON logging system to store every generated signal for future auditing.

## Milestone 6: Backtesting Engine & System Tuning
**Objective**: Ensure the system is 100% deterministic, testable, and meets the strict "quality over quantity" mandate.
- [x] **Task 6.1: Deterministic Replay Harness** - `src/backtest/replay.py` — ReplayHarness wraps production SignalPipeline with historical end_time; `assert_deterministic()` validates Replay(X)==Replay(X); all fields checked.
- [x] **Task 6.2: Historical Signal Outcome Evaluator** - `src/backtest/evaluator.py` — LONG/SHORT TP/SL/expiry/ambiguous-candle resolution; fee/slippage-adjusted net R; configurable AmbiguousResolution policy (CONSERVATIVE/OPTIMISTIC/OPEN_PROXIMITY).
- [x] **Task 6.3: Threshold Tuning** - `src/backtest/tuner.py` — `parameter_sweep()` grid search over existing config fields (dot-notation), `sweep_report_markdown()`, `walk_forward_evaluate()`, `compare_configurations()`, `DatasetSplit` chronological validation; 14-metric `PerformanceMetrics`.

## Milestone 7: Signal Lifecycle Tracking
**Objective**: Track theoretical signal progression through deterministic state machine.
- [x] **Task 7.1: SignalLifecycleTracker** - `src/models/lifecycle.py` — 9 states (GENERATED → TP3_HIT/STOPPED_OUT/EXPIRED/INVALIDATED); explicit legal transition table; idempotent transitions; full history logging; `save()`/`load()` persistence.

## Milestone 8: Daily Scheduler
**Objective**: Lightweight local scheduler with no paid cloud dependencies.
- [x] **Task 8.1: BotScheduler** - `src/scheduler.py` — 15m refresh loop; duplicate suppression (cooldown ledger); exponential backoff + retry for API failures; graceful SIGINT/SIGTERM shutdown; `--once`, `--deep-scan`, `--interval` CLI args.

## Milestone 9: README & Documentation
**Objective**: Comprehensive documentation for all system capabilities.
- [x] **Task 9.1: README.md** - Architecture, installation, configuration, Binance data requirements, Telegram setup, all run commands (scanner/replay/evaluation/tuning/tests), example signal, example evaluation JSON, example sweep report, lifecycle diagram, known limitations, disclaimer.

## Milestone 10: Test Suite Expansion
**Objective**: Full test coverage of all new phases; 0 failures.
- [x] **Task 10.1: Replay tests** - Same timestamp → identical result; end_time forwarded (no future data); count mismatch detected; field mismatch detected; range replay coverage.
- [x] **Task 10.2: Outcome evaluator tests** - LONG TP1/TP2/TP3 progression; LONG SL full loss; LONG TP1+trailing SL; SHORT entry/TP/SL; expiry; ambiguous OHLC (CONSERVATIVE/OPTIMISTIC/OPEN_PROXIMITY); batch evaluation; empty klines.
- [x] **Task 10.3: Tuner tests** - All-winners/all-losers/mixed metrics; signals_per_day; worst_losing_streak; non-entered exclusion; DatasetSplit chronology validation; sweep sorting/param application/immutability/error handling; walk-forward; markdown report.
- [x] **Task 10.4: Lifecycle tests** - All 7 valid state paths; 5 invalid transitions rejected; idempotency (no state change, no history append); history length; `to_dict()` contract.
- [x] **Task 10.5: Scheduler tests** - Duplicate within cooldown suppressed; expired cooldown allowed; different side not a duplicate; invalid signal not published; API failure returns empty.
- [x] **Task 10.6: Risk engine math** - Position notional; margin = notional/leverage; hard leverage cap; risk_amount = account×pct; SHORT R:R geometry.

**Final test count: 108 tests, 0 failures.**

