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
- [ ] **Task 5.1: Signal Contract Validation** - Implement JSON schema validation to ensure every generated signal matches the strict required schema.
- [ ] **Task 5.2: Telegram Formatter** - Create the UI/Markdown formatter to match the exact `🚀 SIGNAL` template from the PRD.
- [ ] **Task 5.3: Telegram Integration** - Integrate the Telegram Bot API to broadcast signals to a configured channel/chat.
- [ ] **Task 5.4: Signal Persistence Layer** - Implement a local database or JSON logging system to store every generated signal for future auditing.

## Milestone 6: Backtesting Engine & System Tuning
**Objective**: Ensure the system is 100% deterministic, testable, and meets the strict "quality over quantity" mandate.
- [ ] **Task 6.1: Deterministic Replay Harness** - Build a framework to inject historical data timestamps into the pipeline to verify deterministic outputs.
- [ ] **Task 6.2: Core Logic Unit Tests** - Write unit tests for all math components in the Risk, Entry, SL, and TP engines.
- [ ] **Task 6.3: Threshold Tuning** - Run historical simulations to calibrate Volume, OI, and Regime thresholds, targeting an output of 1-3 high-quality signals daily.
