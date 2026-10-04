# Crypto Daily Futures Signal Engine

## Product Requirements Document (PRD)

**Version:** 1.0
**Status:** Ready for implementation
**Product Type:** Research / Signal Generation System
**Primary Market:** Crypto perpetual futures
**Primary Exchange:** Binance Futures
**Execution:** Signal-only in V1; no automatic order execution
**Primary Output:** 1–3 high-quality trading setups per day
**Notification:** Telegram
**Core Principle:** Deterministic, testable signal generation; no LLM dependency in the trading-critical path

---

# 1. Executive Summary

The product is a **daily cryptocurrency futures signal generator** that scans the perpetual-futures market, identifies a small number of high-quality LONG/SHORT setups, calculates the complete trade plan, and delivers the result through Telegram.

The system must produce signals in a practical format:

```text
🚀 SIGNAL

COIN: PUMP/USDT
SIDE: LONG

ENTRY: 0.003245

TP1: 0.004560
TP2: 0.005610
TP3: 0.005980

SL: 0.002840

LEVERAGE: 10x
MARGIN: $12

RISK/REWARD: 2.8R
CONFIDENCE: 87/100

CONDITIONS:
✅ 1H bullish structure
✅ 15m volume expansion
✅ BTC bullish regime
✅ Open Interest increasing
✅ Sufficient liquidity

INVALIDATION:
⚠️ 15m close below 0.00310
⚠️ BTC loses regime support
⚠️ Entry condition expires

MANAGEMENT:
→ TP1: close 50%
→ TP2: close 25%
→ TP3: close 25%
→ After TP1: SL → Entry
```

The product is **not** intended to be a complete exchange-trading platform.

Its responsibility ends at:

```text
Market data
    ↓
Scanning
    ↓
Signal detection
    ↓
Validation
    ↓
Entry / SL / TP
    ↓
Risk / Leverage / Margin
    ↓
Ranking
    ↓
Telegram Signal
```

No automatic order placement is required in V1.

---

# 2. Product Vision

Build a small, rigorous signal engine that answers one question each day:

> **"What are the best 1–3 actionable futures setups in the market right now, and under exactly what conditions are they valid?"**

The system should avoid the common failure mode of crypto bots that generate large quantities of low-quality signals.

The desired behavior is:

```text
500+ markets
      ↓
Candidate detection
      ↓
Filtering
      ↓
Risk validation
      ↓
Ranking
      ↓
1–3 final setups
```

Quality is more important than signal frequency.

---

# 3. Goals

## 3.1 Primary Goals

The system must:

1. Scan Binance perpetual futures markets.
2. Analyze multiple timeframes.
3. Detect LONG and SHORT opportunities.
4. Apply market-regime filters.
5. Use volume and Open Interest information.
6. Calculate entry, SL, TP1, TP2 and TP3.
7. Calculate appropriate leverage.
8. Calculate margin / position size from risk constraints.
9. Generate explicit validity conditions.
10. Generate explicit invalidation conditions.
11. Rank candidates and select the best 1–3.
12. Deliver signals to Telegram.
13. Store every generated signal for historical evaluation.
14. Support deterministic backtesting and replay.
15. Validate strategies independently through dedicated research/backtesting tooling.
16. Remain usable without any LLM or external AI API.

---

# 4. Non-Goals

The following are explicitly outside the V1 scope:

* Automatic live order execution
* Copy trading
* Martingale
* Grid trading as the primary strategy
* DCA as the primary strategy
* Portfolio management
* Exchange account management
* Custodial wallets
* Social trading
* Public marketplace
* Full trading dashboard
* AI autonomous agent
* LLM-generated trading decisions
* Claims of guaranteed profitability

The project is a **signal engine**, not an autonomous trading bot.

---

# 5. Product Principles

## 5.1 Deterministic First

The trading-critical path must be reproducible.

Given identical:

```text
market data
configuration
strategy version
timestamp
```

the system must produce the same:

```text
candidate
score
entry
SL
TPs
leverage
margin
decision
```

## 5.2 No Hidden Decisions

Every signal must be explainable through explicit conditions.

Example:

```text
BTC regime: bullish
1H structure: bullish
15m momentum: positive
15m volume: +185%
OI: +8.4%
Liquidity: acceptable
Risk/Reward: 3.1R
```

## 5.3 Risk Before Profit

A setup must not be accepted merely because projected upside is large.

The system must first validate:

```text
Liquidity
Risk
SL distance
Leverage
Margin
R:R
Market conditions
```

## 5.4 Quality Over Quantity

Default target:

```text
1–3 signals/day
```

The system should be allowed to produce:

```text
0 signals
```

when no setup satisfies the required quality threshold.

---

# 6. Target Users

Primary user:

* Individual trader/researcher
* Wants actionable daily setups
* Makes final trading decisions manually
* Wants structured, auditable signals
* Does not want a complicated trading terminal

Secondary users:

* Quant researchers
* Strategy developers
* Developers validating signal models

---

# 7. Signal Contract

Every published signal must follow a versioned schema.

## 7.1 Required Fields

```json
{
  "signal_id": "SIG-20261004-PUMP-001",
  "timestamp": "2026-10-04T20:00:00Z",
  "symbol": "PUMPUSDT",
  "side": "LONG",
  "entry": 0.003245,
  "tp1": 0.004560,
  "tp2": 0.005610,
  "tp3": 0.005980,
  "stop_loss": 0.002840,
  "leverage": 10,
  "margin": 12,
  "position_notional": 120,
  "risk_amount": 1.50,
  "risk_reward_tp1": 1.8,
  "risk_reward_tp2": 3.1,
  "risk_reward_tp3": 3.6,
  "confidence": 87,
  "conditions": [],
  "invalidation_conditions": [],
  "management_rules": []
}
```

## 7.2 Optional Fields

```text
entry_zone_low
entry_zone_high
activation_price
expiry_time
funding_rate
open_interest_change
volume_change
btc_regime
strategy_id
strategy_version
signal_reason
market_regime
liquidity_score
```

---

# 8. Market Universe

## 8.1 Primary Universe

Binance USDT-margined perpetual futures.

The scanner should dynamically discover eligible symbols rather than maintaining a hardcoded list.

## 8.2 Symbol Eligibility Filters

A market may be excluded if:

* Trading is suspended
* Insufficient historical data
* Insufficient liquidity
* Excessive spread
* Abnormal price behavior
* Extremely low volume
* Invalid/missing Open Interest data
* Contract is unavailable for required timeframe
* Market age is below configurable threshold

## 8.3 Dynamic Universe

The system should support:

```text
ALL_ELIGIBLE_SYMBOLS
```

rather than:

```text
BTC
ETH
SOL
...
```

The purpose is to allow discovery of emerging opportunities such as PUMP without manually adding the asset.

---

# 9. Timeframes

The initial system will use:

```text
4H  → Macro market regime
1H  → Trend / structure
15m → Entry setup / momentum / volume
```

Optional future timeframe:

```text
5m → entry refinement
```

The 5m timeframe should not be required in MVP.

---

# 10. Data Layer

## 10.1 Required Data

The system should collect:

### OHLCV

```text
open
high
low
close
volume
timestamp
```

### Futures Data

```text
open interest
funding rate
mark price
contract information
```

### Optional Future Data

```text
order book
bid/ask spread
liquidations
long/short ratio
basis
```

---

# 11. Market Regime Engine

The first major filter is overall market context.

## 11.1 BTC Regime

BTC acts as the primary market-regime reference.

Example model:

```text
BTC 4H bullish
    ↓
Longs preferred
Shorts restricted

BTC 4H bearish
    ↓
Shorts preferred
Longs restricted

BTC 4H neutral
    ↓
Both directions allowed with stricter thresholds
```

The exact regime model must be configurable and backtestable.

Possible components:

```text
EMA relationship
Market structure
Trend slope
ATR regime
Breakout / breakdown
Support / resistance
Momentum
```

The system must never assume that a particular indicator is profitable simply because it is common.

---

# 12. Candidate Detection

The scanner should detect several setup families.

## 12.1 Momentum

Examples:

```text
Price expansion
Volume expansion
Momentum continuation
Breakout
Breakout retest
```

## 12.2 Trend Continuation

Examples:

```text
1H bullish structure
15m pullback
Momentum recovery
Volume confirmation
```

## 12.3 FVG / SMC

Inspired by the FVG-Delta Engine.

Potential components:

```text
Fair Value Gap
Liquidity sweep
Displacement
Structure break
Retest
```

SMC/FVG must remain one strategy family rather than becoming the entire system.

## 12.4 Mean-Reversion Candidate

Optional future strategy family:

```text
Oversold
Support
Volume exhaustion
Momentum reversal
```

No strategy family may enter production merely because it generates attractive historical charts.

---

# 13. Signal Validation Pipeline

Each candidate must pass through the following pipeline:

```text
Candidate
   ↓
Market Eligibility
   ↓
BTC Regime Filter
   ↓
Higher-Timeframe Structure
   ↓
15m Setup Confirmation
   ↓
Volume Filter
   ↓
Open Interest Filter
   ↓
Funding Filter
   ↓
Liquidity Filter
   ↓
Entry Validation
   ↓
SL Validation
   ↓
TP Validation
   ↓
Risk Validation
   ↓
Confidence Score
   ↓
Ranking
```

A candidate may be rejected at any stage.

---

# 14. Volume Engine

The first MVP volume signal should be deterministic.

Example:

```text
current_15m_volume
/
rolling_average_15m_volume
```

Configuration example:

```text
volume_ratio >= 1.5
```

Strong candidate:

```text
volume_ratio >= 2.0
```

The threshold must be configurable.

The engine should store both:

```text
raw_volume
volume_ratio
volume_percentile
```

---

# 15. Open Interest Engine

Open Interest is a core component.

The system should calculate:

```text
OI current
OI previous
OI percentage change
```

and optionally:

```text
OI change over 15m
OI change over 1H
```

Possible interpretation:

```text
Price ↑ + OI ↑
→ bullish participation confirmation

Price ↓ + OI ↑
→ bearish participation confirmation
```

The exact interpretation must be validated through backtesting rather than hardcoded as truth.

---

# 16. Funding Engine

Funding should be used as contextual information.

Possible filters:

```text
normal funding
extreme positive funding
extreme negative funding
funding divergence
```

Example rule:

```text
LONG candidate
AND
funding extremely positive
→ reduce confidence or reject
```

Funding thresholds must be configurable.

---

# 17. Entry Engine

The system should support multiple entry models.

## 17.1 Market Entry

```text
Entry = current/reference price
```

## 17.2 Pullback Entry

```text
Entry = predetermined retracement zone
```

## 17.3 Breakout Retest

```text
Breakout
    ↓
Retest
    ↓
Entry
```

## 17.4 FVG Entry

```text
Impulse
   ↓
FVG
   ↓
Retrace
   ↓
Entry
```

Each entry model must return:

```text
entry_price
entry_zone
activation_condition
expiry_condition
```

---

# 18. Stop-Loss Engine

The engine must support multiple SL models.

## 18.1 ATR Stop

Inspired by TradeClaw:

```text
SL = Entry ± ATR × multiplier
```

## 18.2 Structural Stop

Examples:

```text
below swing low
above swing high
below support
above resistance
```

## 18.3 FVG / Structure Stop

Where applicable:

```text
below invalidation zone
above invalidation zone
```

The engine should choose the candidate SL that is both:

```text
structurally meaningful
AND
risk-feasible
```

---

# 19. Take-Profit Engine

Every published trade should support:

```text
TP1
TP2
TP3
```

Potential target models:

```text
Risk multiples
Fibonacci extensions
Resistance / support
Liquidity zones
ATR targets
Previous highs/lows
```

Example:

```text
TP1 = 1R
TP2 = 2R
TP3 = 3R
```

The final implementation should permit dynamic target selection.

Targets must never be generated merely to produce an attractive chart.

---

# 20. Risk / Reward Engine

For LONG:

```text
risk = entry - SL
reward = TP - entry
```

For SHORT:

```text
risk = SL - entry
reward = entry - TP
```

The engine calculates:

```text
R:R(TP1)
R:R(TP2)
R:R(TP3)
```

Candidate rejection example:

```text
TP1 R:R < minimum threshold
→ reject
```

Thresholds must be configuration-driven.

---

# 21. Leverage Engine

Leverage must be derived from risk constraints rather than randomly fixed.

Inputs:

```text
account equity
risk budget
SL distance
volatility
maximum leverage
minimum margin
exchange constraints
```

Example conceptual relationship:

```text
SL distance
     ↓
maximum acceptable notional
     ↓
position size
     ↓
leverage / margin configuration
```

The system must prevent:

```text
excessive leverage
```

even if an otherwise attractive setup exists.

A maximum leverage cap must be globally configurable.

---

# 22. Position Sizing / Margin Engine

Position sizing must be based on risk.

Example:

```text
Account Equity = $100

Risk Budget = 1%

Maximum Risk = $1

SL Distance = 2.5%

Position Notional ≈ $40
```

With:

```text
10x leverage
```

margin would be approximately:

```text
$4
```

before exchange-specific constraints and fees.

The engine must calculate and store:

```text
risk_amount
position_notional
quantity
margin
leverage
```

The user's desired `$12` margin should be treated as configurable input, not an unconditional rule.

---

# 23. Confidence Scoring

Inspired by the confidence-oriented signal generators researched.

Every candidate receives a score from:

```text
0–100
```

Example:

```text
BTC regime                  20
1H structure                15
15m momentum                15
Volume                      15
Open Interest               15
Funding                      5
Liquidity                   5
Risk/Reward                 10
--------------------------------
TOTAL                      100
```

This is only an initial weighting proposal.

Weights must be configurable and experimentally validated.

## Signal Thresholds

Example:

```text
< 60
Reject

60–74
Watchlist

75–84
Valid candidate

85–100
Strong candidate
```

Thresholds must be configurable.

---

# 24. Signal Ranking

After filtering:

```text
500+ markets
      ↓
Candidates
      ↓
Valid candidates
      ↓
Score
      ↓
Rank
      ↓
Top 1–3
```

The ranking system should consider:

```text
confidence
R:R
liquidity
volatility
market regime alignment
setup quality
signal freshness
```

Correlation should also be considered.

Example:

```text
BTC LONG
ETH LONG
SOL LONG
```

may represent essentially the same market exposure.

The system should optionally penalize highly correlated candidates.

---

# 25. Invalidation Engine

This is a first-class component.

Every published signal must answer:

> "What makes this setup no longer valid?"

Examples:

```text
15m close below structural support
BTC changes regime
OI collapses
Volume confirmation disappears
Entry is not reached within X candles
Price moves too far beyond entry before activation
Funding becomes extreme
Liquidity deteriorates
```

The output must contain human-readable conditions.

Example:

```text
INVALIDATION:

❌ 15m close below 0.00310
❌ BTC loses 4H bullish structure
❌ Entry not triggered within 6 candles
```

---

# 26. Signal Lifecycle

Signals have explicit states:

```text
GENERATED
    ↓
WAITING_FOR_ENTRY
    ↓
ACTIVE
    ↓
TP1_HIT
    ↓
TP2_HIT
    ↓
TP3_HIT
```

Alternative paths:

```text
GENERATED
    ↓
INVALIDATED

ACTIVE
    ↓
STOPPED_OUT
```

Or:

```text
WAITING_FOR_ENTRY
    ↓
EXPIRED
```

All state transitions must be stored.

---

# 27. Position Management Rules

The system does not execute trades, but signals should contain management instructions.

Default example:

```text
TP1 → close 50%
TP2 → close 25%
TP3 → close 25%

After TP1:
SL → Entry
```

Allocation must be configurable.

Example:

```text
50 / 25 / 25
40 / 30 / 30
33 / 33 / 34
```

The output should clearly distinguish:

```text
recommended management
```

from:

```text
actual executed management
```

because V1 has no execution engine.

---

# 28. Daily Scheduling

The system should support a daily primary scan.

Default:

```text
Daily scan
```

Optional intraday refresh:

```text
15m scanner
```

Recommended V1 behavior:

```text
Daily deep scan
+
15m candidate refresh
```

This allows the system to remain "daily signal oriented" while not relying on stale market data.

---

# 29. Telegram Output

Telegram is the primary user interface.

The message must be:

* concise
* structured
* readable on mobile
* consistent
* machine-generated
* versioned

## Example

```text
🚀 #1 FUTURES SIGNAL

PUMP/USDT
LONG

ENTRY
0.003245

TP1
0.004560

TP2
0.005610

TP3
0.005980

SL
0.002840

LEVERAGE
10x

MARGIN
$12

RISK
$1.50

R:R
1.8R / 3.1R / 3.6R

CONFIDENCE
87/100

CONDITIONS
✅ BTC bullish regime
✅ 1H bullish structure
✅ 15m volume expansion
✅ OI increasing
✅ Liquidity acceptable

INVALIDATION
❌ 15m close < 0.00310
❌ BTC regime failure
❌ Setup expires after 6 candles

MANAGEMENT
→ TP1 50%
→ TP2 25%
→ TP3 25%
→ TP1 hit → SL to entry
```

---

# 30. Storage

Every candidate and every published signal must be stored.

## Signal Record

```text
signal_id
timestamp
symbol
side
strategy_id
strategy_version

entry
tp1
tp2
tp3
stop_loss

leverage
margin
position_notional
risk_amount

confidence
risk_reward

btc_regime
volume_ratio
oi_change
funding_rate

conditions
invalidation_conditions
management_rules

status
expiry_time

created_at
updated_at
```

---

# 31. Historical Evaluation

Every signal becomes a future test case.

The evaluator should record:

```text
entry_triggered
entry_price_actual
time_to_entry

tp1_hit
tp2_hit
tp3_hit
sl_hit

which happened first
maximum_favorable_excursion
maximum_adverse_excursion

holding_time
fees
funding
slippage_estimate

realized_R
```

This allows analysis such as:

```text
Signal count
Win rate
TP1 hit rate
TP2 hit rate
TP3 hit rate
SL rate
Expected R
Average R
Profit factor
Maximum drawdown
Average holding time
```

---

# 32. Testing Architecture

The signal generator and test systems must remain logically independent.

```text
                SIGNAL ENGINE
                     │
                     ▼
              Signal Dataset
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Jesse     perpsignal   Manifold-BT
          │          │          │
          └──────────┼──────────┘
                     ▼
              Validation Report
```

---

# 33. Jesse — Primary Strategy Validation

Jesse should be used as the main general-purpose backtesting environment.

Use cases:

```text
strategy backtesting
out-of-sample testing
walk-forward research
parameter optimization
Monte Carlo testing
rule significance analysis
```

The signal engine remains independent.

Jesse is a **validator**, not the source of truth for signal logic.

Reference:

<https://github.com/jesse-ai/jesse>

---

# 34. perpsignal — Perpetual Futures Validation

Use `perpsignal` for research involving:

```text
perpetual futures
funding
fees
stops
targets
leverage
Open Interest
```

Primary purpose:

```text
Does the proposed signal logic remain valid
when perpetual-futures mechanics are included?
```

Reference from the systematic-trading research:

<https://github.com/wangzhe3224/awesome-systematic-trading>

---

# 35. Manifold-BT — Stress Validation

Use Manifold-BT for:

```text
large parameter sweeps
walk-forward testing
Monte Carlo
slippage assumptions
fill assumptions
robustness analysis
```

Purpose:

```text
Is the strategy robust,
or did we simply find a lucky parameter combination?
```

---

# 36. Validation Protocol

No strategy should be considered "validated" from one backtest.

Minimum validation process:

```text
1. Historical in-sample research
2. Out-of-sample test
3. Walk-forward analysis
4. Parameter perturbation
5. Fee/funding inclusion
6. Slippage assumptions
7. Monte Carlo
8. Regime-specific evaluation
9. Paper signal period
```

---

# 37. Dataset Splitting

Historical data should be separated chronologically.

Example:

```text
TRAIN
    ↓
VALIDATION
    ↓
TEST
```

Never randomly shuffle time-series data across these groups.

Example:

```text
2022–2024 → Train
2025       → Validation
2026       → Test
```

Exact periods should be chosen based on data availability.

---

# 38. Anti-Overfitting Rules

The system must explicitly defend against:

```text
parameter overfitting
indicator stacking
look-ahead bias
survivorship bias
future leakage
selection bias
```

Rules:

* No future candles may be available to the signal decision.
* Indicator calculations must use only information available at signal time.
* Candidate ranking must use only known information.
* Delisted/inactive symbols must be handled carefully in historical research.
* Backtests must include realistic fees and funding.
* Optimized parameters must be evaluated on unseen data.

---

# 39. Paper Validation

Before live use:

```text
Generate signals
      ↓
Do NOT trade automatically
      ↓
Track theoretical result
      ↓
Compare expected vs actual
```

Recommended metrics:

```text
Signal frequency
Entry hit rate
TP1 hit rate
TP2 hit rate
TP3 hit rate
SL rate
Expected R
Average R
Worst losing streak
Maximum drawdown
Average signal duration
```

A paper-trading period is required before any optional future execution layer.

---

# 40. Architecture

Recommended architecture:

```text
                ┌───────────────────┐
                │ Binance Data      │
                │ Market / Futures  │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Data Normalizer   │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Indicator Engine  │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Market Regime     │
                │ Engine            │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Candidate Scanner │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Signal Validator  │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Entry / SL / TP   │
                │ Engine            │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Risk / Leverage   │
                │ Engine            │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ Confidence /      │
                │ Ranking           │
                └─────────┬─────────┘
                          │
                    Top 1–3
                          │
               ┌──────────┴─────────┐
               ▼                    ▼
        ┌─────────────┐      ┌─────────────┐
        │ Telegram    │      │ Signal DB   │
        └─────────────┘      └─────────────┘
```

---

# 41. Suggested Technology Stack

## Backend

```text
Python 3.x
```

Reason:

* ecosystem for market data
* pandas / NumPy
* TA libraries
* research tooling
* easy Telegram integration
* easy interoperability with backtesting frameworks

## Data / Storage

MVP:

```text
PostgreSQL
```

or SQLite for local prototyping.

PostgreSQL is preferred once historical evaluation and signal tracking become important.

## Scheduling

Use a lightweight scheduler or cron-compatible process.

## Notifications

```text
Telegram Bot API
```

## Testing

```text
pytest
```

## Configuration

Use version-controlled configuration files.

Example:

```yaml
universe:
  exchange: binance_futures

timeframes:
  regime: 4h
  trend: 1h
  entry: 15m

signals:
  max_daily: 3
  min_confidence: 75

risk:
  max_account_risk: 0.01
  max_leverage: 10

targets:
  count: 3
```

---

# 42. Module Structure

Suggested project structure:

```text
crypto-signal-engine/
│
├── app/
│   ├── config/
│   ├── data/
│   ├── indicators/
│   ├── regime/
│   ├── scanners/
│   ├── strategies/
│   ├── signals/
│   ├── risk/
│   ├── ranking/
│   ├── notifications/
│   ├── evaluation/
│   └── storage/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── fixtures/
│
├── backtests/
│
├── configs/
│
├── scripts/
│
├── docs/
│
├── pyproject.toml
└── README.md
```

---

# 43. Strategy Interface

Every strategy should implement a common interface.

Conceptually:

```python
class Strategy:
    def scan(self, market_data):
        ...

    def validate(self, candidate, context):
        ...

    def build_signal(self, candidate, context):
        ...

    def score(self, signal, context):
        ...
```

This allows:

```text
FVG Strategy
Momentum Strategy
Breakout Strategy
Trend Continuation Strategy
```

to coexist without changing the core engine.

---

# 44. Strategy Versioning

Every generated signal must contain:

```text
strategy_id
strategy_version
configuration_hash
```

Example:

```text
strategy_id = momentum_volume_01
version = 1.3.0
config_hash = abc123...
```

This allows historical attribution.

When performance changes, we know which strategy version produced the signals.

---

# 45. LLM Policy

LLM usage is optional.

The LLM must **not** be responsible for:

```text
Entry
SL
TP
Leverage
Position size
Market condition
Signal acceptance
```

The LLM may later assist with:

```text
natural-language explanation
Telegram formatting
research summaries
post-signal analysis
```

The signal remains deterministic.

---

# 46. Error Handling

A signal must not be published if critical data is unavailable.

Examples:

```text
Missing OHLCV
Missing OI
Invalid funding data
Insufficient candles
Invalid price
Invalid contract metadata
Impossible TP/SL geometry
R:R below threshold
Insufficient liquidity
```

Instead:

```text
REJECT SIGNAL
```

and store the rejection reason.

---

# 47. Observability

The system should log:

```text
scan started
symbols scanned
candidates found
candidates rejected
rejection reasons
final candidates
published signals
Telegram success/failure
data provider errors
```

Metrics:

```text
symbols_scanned
candidate_count
valid_signal_count
signals_published
data_errors
telegram_errors
```

---

# 48. Security

The MVP should require no trading API key.

If future execution is added:

* read-only API keys should be the default
* execution keys must be isolated
* secrets must never be committed
* leverage limits must be enforced in code
* kill switch must exist
* execution must be disabled by default

V1 should ideally have:

```text
NO PRIVATE EXCHANGE CREDENTIALS
```

at all.

---

# 49. Configuration Philosophy

Almost everything should be configurable.

Examples:

```text
minimum volume ratio
minimum confidence
minimum R:R
OI threshold
funding threshold
max leverage
risk per signal
maximum daily signals
TP allocation
signal expiry
BTC regime model
strategy weights
```

Avoid hardcoding strategy assumptions.

---

# 50. Core Acceptance Criteria

V1 is accepted only when:

### Scanner

* [ ] Dynamically discovers eligible Binance perpetual markets.
* [ ] Retrieves required OHLCV data.
* [ ] Retrieves Open Interest.
* [ ] Retrieves funding data.
* [ ] Handles unavailable/invalid data safely.

### Strategy

* [ ] Supports at least one LONG strategy.
* [ ] Supports at least one SHORT strategy.
* [ ] Uses 4H / 1H / 15m context.
* [ ] Includes BTC regime filtering.
* [ ] Includes volume filtering.
* [ ] Includes OI filtering.
* [ ] Includes configurable confidence scoring.

### Signal

* [ ] Generates entry.
* [ ] Generates TP1.
* [ ] Generates TP2.
* [ ] Generates TP3.
* [ ] Generates SL.
* [ ] Generates leverage.
* [ ] Generates margin / position size.
* [ ] Generates conditions.
* [ ] Generates invalidation conditions.
* [ ] Generates management rules.

### Ranking

* [ ] Can rank multiple candidates.
* [ ] Can return top 1–3.
* [ ] Can return zero signals.

### Notifications

* [ ] Telegram output is formatted consistently.
* [ ] Every signal includes a unique ID.
* [ ] Every signal is persisted.

### Evaluation

* [ ] Historical replay works.
* [ ] Signal outcomes can be calculated.
* [ ] Fees/funding are represented.
* [ ] Walk-forward validation is possible.
* [ ] Results can be exported.

---

# 51. Development Roadmap

## Phase 0 — Research Foundation

Deliver:

```text
market data loader
historical dataset
data validation
configuration system
```

No signal publishing.

---

## Phase 1 — Scanner

Deliver:

```text
Binance Futures universe
15m / 1H / 4H data
BTC regime
volume
OI
funding
candidate detection
```

Output:

```text
candidate list
```

---

## Phase 2 — Signal Engine

Deliver:

```text
Entry
SL
TP1
TP2
TP3
R:R
```

No Telegram yet.

---

## Phase 3 — Risk Engine

Deliver:

```text
leverage
position size
margin
risk
```

with hard safety limits.

---

## Phase 4 — Ranking

Deliver:

```text
confidence scoring
candidate ranking
top 1–3 selection
correlation filtering
```

---

## Phase 5 — Telegram

Deliver:

```text
Telegram notifications
signal lifecycle updates
```

---

## Phase 6 — Historical Replay

For every historical timestamp:

```text
simulate scanner
generate signal
save signal
evaluate outcome
```

This phase is critical.

---

## Phase 7 — Jesse Validation

Port the strategy into Jesse or create a compatible validation adapter.

Run:

```text
backtest
out-of-sample
walk-forward
optimization
Monte Carlo
```

---

## Phase 8 — Perpetual Futures Validation

Validate using perpetual-specific assumptions:

```text
fees
funding
leverage
SL/TP
OI context
```

---

## Phase 9 — Stress Testing

Use Manifold-BT or equivalent tooling for:

```text
slippage variation
parameter perturbation
Monte Carlo
large parameter sweeps
```

---

## Phase 10 — Paper Signals

Run continuously without execution.

Compare:

```text
predicted signal
vs
actual market outcome
```

---

# 52. Performance Gates

A strategy should not be promoted simply because:

```text
total return > 0
```

Minimum research review should include:

```text
Expected R
Profit factor
Max drawdown
Sharpe / Sortino where appropriate
Win rate
Average win
Average loss
TP1/TP2/TP3 rates
Worst losing streak
Trade frequency
Regime-specific performance
```

A strategy should also remain acceptable when reasonable changes are made to:

```text
fees
slippage
entry assumptions
TP allocation
parameters
market period
```

---

# 53. Production Readiness Criteria

The system becomes "paper-production ready" only when:

```text
✓ deterministic
✓ reproducible
✓ monitored
✓ historical evaluation complete
✓ out-of-sample tested
✓ stress tested
✓ Telegram stable
✓ signal lifecycle tracked
```

It does **not** mean the strategy is guaranteed profitable
---

# 54. Inspiration / Research Sources

These projects are references, not dependencies to blindly copy.

### Freqtrade

<https://github.com/freqtrade/freqtrade>
Use for:

```text
strategy architecture
futures
backtesting
risk / position concepts
```

### Jesse

<https://github.com/jesse-ai/jesse>
Use for:

```text
backtesting
optimization
walk-forward
Monte Carlo
strategy validation
```

### CryptoSignal

<https://github.com/cryptosignal/crypto-signal>
Use for:

```text
technical indicators
scanner concepts
signal aggregation
```

### OctoBot

<https://github.com/Drakkar-Software/OctoBot>
Use for:

```text
modular architecture
exchange integration ideas
```

### TradeClaw

`naimkatiman/tradeclaw`
Use for:

```text
ATR SL
Fibonacci targets
multi-TP signal structure
```

### FVG-Delta Signal Engine

`starchild-ai-agent/community-skills`
Use for:

```text
FVG/SMC concepts
SL-based leverage ideas
Telegram signal format
```

### Crypto Sentinel

`lagarcess/crypto-signals`
Use for:

```text
entry / TP / SL
risk-aware signals
notifications
```

### KuCoin EMA Scanner

`Darthreign/KuCoin-EMA-Scanner`
Use for:

```text
scanner
entry/SL/TP1/TP2/TP3
position sizing
```

### FullAutomated Trading Bot

`Aksee123/FullAutomated_Trading_Bot`
Use for:

```text
shared signal generator
confidence scoring
```

### AutoSignalTrader

`AviyaX/AutoSignalTrader`
Use for:

```text
multi-TP position allocation
```

### Neko Futures Trader

`lukmanc405/neko-futures-trader`
Use for:

```text
BTC regime
volume
RSI
MACD filters
```

### perpsignal / systematic-trading research

Primary reference collection:
<https://github.com/wangzhe3224/awesome-systematic-trading>
---

# 55. Six-Month Premortem

Assume that six months after launch the project failed and produced no useful results.

## Failure Mode 1 — Overfitted Strategy

**Cause:**
Too many indicators, thresholds, and parameters were optimized until historical data looked excellent.
**Warning signs:**

```text
Great backtest
Weak out-of-sample
Performance collapses with tiny parameter changes
```

**Mitigation:**

```text
Walk-forward
Out-of-sample
Parameter perturbation
Monte Carlo
Simple strategy baseline
```

---

## Failure Mode 2 — Too Many Filters

**Cause:**
The engine combines BTC + RSI + MACD + EMA + OI + volume + funding + FVG + multiple confirmations until almost every historical signal becomes artificially "perfect."
**Warning signs:**

```text
Very few trades
Extremely high historical win rate
Removing one filter destroys performance
```

**Mitigation:**
Every filter must prove incremental value.
Test:

```text
Base
+
one filter
+
another filter
```

rather than adding everything simultaneously
---

## Failure Mode 3 — Signal Looks Good, Execution Reality Doesn't

**Cause:**
Backtest assumes:

```text
perfect entry
perfect fill
zero slippage
```

while live price can move through the entry zone.
**Warning signs:**

```text
Many missed entries
Real fills worse than backtest
Small-cap contracts behave differently
```

**Mitigation:**
Include:

```text
slippage
spread assumptions
entry tolerance
realistic fill models
```

---

## Failure Mode 4 — Signal Frequency Becomes Addictive

**Cause:**
The system is changed to generate more signals because zero/one signal days feel disappointing.
**Warning signs:**

```text
1–3/day
→ 5/day
→ 15/day
→ noisy Telegram channel
```

**Mitigation:**
Keep:

```text
0 signals
```

as a valid outcome
---

## Failure Mode 5 — Leverage Masks Poor Strategy Quality

**Cause:**
A mediocre setup appears attractive because leverage makes the profit number larger.
**Warning signs:**

```text
"10x gives +30%"
```

instead of:

```text
"The setup produced +3R"
```

**Mitigation:**
Evaluate strategy quality primarily in:

```text
R
risk-adjusted return
drawdown
```

not nominal leveraged profit
---

## Failure Mode 6 — The System Becomes a Giant Trading Platform

**Cause:**
The project gradually accumulates:

```text
dashboard
AI agents
execution
copy trading
multiple exchanges
mobile app
portfolio manager
```

before the signal itself is proven.
**Warning signs:**

```text
Thousands of lines of infrastructure
Almost no validated signal performance
```

**Mitigation:**
Protect the core product:

```text
Scanner
+
Signal
+
Risk
+
Validation
+
Telegram
```

Everything else waits
---

# 56. Final Product Definition

The product is successful when a user can receive a message such as:

```text
🚀 PUMP/USDT — LONG
Entry: 0.003245
TP1:   0.004560
TP2:   0.005610
TP3:   0.005980
SL:    0.002840
Leverage: 10x
Margin: $12
Confidence: 87/100
Conditions:
✓ BTC bullish
✓ 1H bullish structure
✓ 15m volume expansion
✓ OI increasing
✓ Liquidity acceptable
Invalidation:
✗ 15m close below 0.00310
✗ BTC regime failure
Management:
→ 50% / 25% / 25%
→ SL to entry after TP1
```

and the system has a complete historical record showing:

```text
Why the signal was generated
Why it was ranked highly
What conditions were active
What happened afterward
What the theoretical result was
How robust the setup is
```

The core principle is:

```text
DO NOT BUILD A BOT THAT TRADES.
BUILD A SYSTEM THAT EARNS THE RIGHT TO SEND A SIGNAL.
```

Only after the signal engine demonstrates robust performance in historical, out-of-sample, stress, and paper evaluation should an execution layer even be considered.
