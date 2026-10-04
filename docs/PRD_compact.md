# Crypto Daily Futures Signal Engine – Compact PRD

## Executive Summary
A deterministic daily crypto futures signal generator delivering 1‑3 high‑quality LONG/SHORT setups via Telegram. No automatic order execution.

## Vision
Answer one question each day: *What are the best actionable futures setups right now?* Emphasise quality over quantity.

## Primary Goals
- Scan Binance USDT‑margined perpetual futures.
- Multi‑timeframe analysis (4H, 1H, 15m).
- Detect LONG & SHORT opportunities.
- Apply market‑regime, volume, OI filters.
- Compute entry, SL, TP1‑3, leverage, margin, risk.
- Rank and select top 1‑3 signals.
- Deliver signals to Telegram and store for backtesting.

## Non‑Goals (V1)
- Automatic live order execution, copy‑trading, martingale, grid/DCA, portfolio mgmt, custodial wallets, social trading, public marketplace, full trading dashboard, AI autonomous agents, LLM‑driven decisions, profit guarantees.

## Principles
- **Deterministic**: Identical inputs → identical outputs.
- **Explainable**: Every signal must list explicit conditions.
- **Risk‑First**: Validate liquidity, risk, SL, leverage before profit.
- **Quality‑Over‑Quantity**: Aim for 0‑3 signals daily.

## Target Users
- Individual traders/researchers seeking manual, auditable signals.
- Quant researchers & developers validating models.

## Signal Contract (required fields)
```json
{ "signal_id": "SIG-YYYYMMDD-XXX-001", "timestamp": "2026-10-04T20:00:00Z", "symbol": "PUMPUSDT", "side": "LONG", "entry": 0.003245, "tp1": 0.004560, "tp2": 0.005610, "tp3": 0.005980, "stop_loss": 0.002840, "leverage": 10, "margin": 12, "position_notional": 120, "risk_amount": 1.5, "risk_reward_tp1": 1.8, "risk_reward_tp2": 3.1, "risk_reward_tp3": 3.6, "confidence": 87, "conditions": [], "invalidation_conditions": [], "management_rules": [] }
```

## Market Universe
- Binance USDT‑margined perpetual futures.
- Dynamically discover eligible symbols; exclude suspended, low‑liquidity, abnormal spreads, insufficient data, etc.

## Timeframes
- **4H** – Macro regime.
- **1H** – Trend/structure.
- **15m** – Entry & momentum.
- *5m optional for future refinements (not MVP).* 

## Data Layer
- **OHLCV**: open, high, low, close, volume, timestamp.
- **Futures**: open interest, funding rate, mark price, contract info.
- Optional: order book, spread, liquidations, long/short ratio, basis.

## Market Regime Engine
- Primary BTC 4H regime (bullish, bearish, neutral) config‑driven.
- Uses EMA, structure, trend, ATR, breakout, support/resistance, momentum.

## Candidate Detection
- **Momentum**: price/volume expansion, continuation, breakout.
- **Trend Continuation**: 1H bullish, 15m pull‑back, volume confirmation.
- **FVG/SMC**: fair‑value gaps, liquidity sweeps, structure breaks.
- **Mean‑Reversion** (future): oversold/support, volume exhaustion.

## Validation Pipeline (sequential)
`Market Eligibility → BTC Regime → Structure → 15m Confirmation → Volume → OI → Funding → Liquidity → Entry → SL → TP → Risk → Confidence → Ranking`

## Volume Engine
- Deterministic ratio: `volume_ratio = current_15m_volume / rolling_avg_15m_volume`.
- Configurable thresholds (e.g., ≥1.5 for candidate, ≥2.0 for strong).

## Open Interest Engine
- Compute current, previous, % change; optional 15m/1H deltas.
- Interpretation based on price‑OI relationship, backtested.

## Funding Engine
- Use funding rate as context; configurable extremes to adjust confidence.

## Entry Engines (models)
- **Market Entry**: price = current/reference.
- **Pull‑back**: price within retracement zone.
- **Breakout‑Retest**: breakout → retest → entry.
- **FVG Entry**: impulse → FVG → retrace → entry.
- Each returns `entry_price`, `entry_zone`, `activation_condition`, `expiry_condition`.

## Stop‑Loss Engines
- **ATR Stop**: `SL = Entry ± ATR * multiplier`.
- **Structural Stop**: below swing low / above swing high.
- **FVG/Structure Stop**: below/above invalidation zone.
- Choose the most structurally meaningful & risk‑feasible SL.

## Take‑Profit Engines
- Support TP1‑3 via risk multiples, Fibonacci, support/resistance, liquidity zones, ATR targets, prior highs/lows.
- Example defaults: `TP1 = 1R`, `TP2 = 2R`, `TP3 = 3R`.

## Risk/Reward Engine
- LONG: `risk = entry - SL`, `reward = TP - entry`.
- SHORT: `risk = SL - entry`, `reward = entry - TP`.
- Compute R:R for each TP.

---
*All components are configurable and backtestable. The system stops at any pipeline step if criteria fail, yielding zero signals for the day.*
