# Crypto Daily Futures Signal Engine

> A deterministic, locally-runnable signal generator for Binance perpetual futures.  
> Signal-only. No auto-execution. No paid APIs. No LLMs.

---

## What It Does

Scans Binance USDT-margined perpetual futures markets every 15 minutes, identifies 1–3 high-quality LONG/SHORT setups, calculates the complete trade plan, and delivers structured signals via Telegram.

```
500+ markets
    ↓
Volume / OI / BTC-regime filtering
    ↓
Entry / SL / TP calculation
    ↓
Risk validation (R:R, leverage, margin)
    ↓
Confidence scoring & ranking
    ↓
1–3 final signals → Telegram
```

---

## What It Does NOT Do

- ❌ Execute orders automatically
- ❌ Manage exchange accounts
- ❌ Use paid data feeds or SaaS APIs
- ❌ Use LLMs or AI for trading decisions
- ❌ Guarantee profitability
- ❌ Copy-trade or act as a portfolio manager

---

## Architecture

```
src/
├── config.py                  # All thresholds in one place (versioned hash)
├── pipeline.py                # Main signal pipeline (run_pipeline)
├── scheduler.py               # 15m loop scheduler with duplicate prevention
├── main.py                    # Entry point
│
├── data/
│   ├── binance_client.py      # Binance Futures REST client (rate-limited)
│   ├── ohlcv_fetcher.py       # OHLCV fetcher + standardisation
│   ├── timeframe_manager.py   # Multi-timeframe data manager
│   ├── futures_data.py        # OI, funding rate, mark price
│   ├── universe_filter.py     # Dynamic symbol eligibility
│   └── persistence.py         # Local JSON signal storage
│
├── engines/
│   ├── btc_regime.py          # 4H BTC market regime (bullish/bearish/neutral)
│   ├── volume_engine.py       # 15m volume expansion detection
│   ├── oi_engine.py           # Open Interest change + divergence
│   ├── funding_engine.py      # Funding rate penalty
│   ├── entry_engine.py        # Entry models (market, pullback)
│   ├── sl_engine.py           # SL models (ATR, structural)
│   ├── tp_engine.py           # TP1/TP2/TP3 via R-multiples
│   ├── risk_engine.py         # Position size, leverage, margin, R:R
│   ├── scoring_engine.py      # Confidence score 0–100 + ranking
│   └── invalidation_engine.py # Invalidation & management rules
│
├── models/
│   ├── signal.py              # Pydantic SignalModel (schema validation)
│   └── lifecycle.py           # Deterministic signal lifecycle state machine
│
├── backtest/
│   ├── replay.py              # Deterministic historical replay harness
│   ├── evaluator.py           # Historical signal outcome evaluator
│   ├── tuner.py               # Threshold sweep + walk-forward + metrics
│   └── adapters/
│       └── jesse_adapter.py   # Jesse strategy export adapter
│
└── utils/
    ├── logger.py
    ├── telegram_formatter.py
    └── telegram_bot.py

tests/
├── test_engines.py            # 33 core engine tests
└── test_backtest.py           # 75 replay / evaluator / tuner / lifecycle / scheduler tests
```

---

## Installation

**Requirements**: Python 3.10+

```bash
git clone <repo>
cd Crypto-AlrtBot
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
# BINANCE_API_KEY and BINANCE_SECRET are optional for public data endpoints
```

All strategy thresholds live in [`src/config.py`](src/config.py).  
The config is versioned via `AppConfig.compute_hash()` — every signal stores its configuration fingerprint.

Key parameters:

| Parameter | Default | Description |
|:---|:---|:---|
| `volume.min_ratio` | 1.5 | Minimum volume expansion ratio |
| `volume.strong_ratio` | 2.0 | Strong volume bonus threshold |
| `oi.min_change_pct` | 2.0 | Minimum OI % change |
| `risk.min_rr_tp1` | 1.5 | Minimum R:R to TP1 |
| `risk.max_leverage` | 10 | Hard leverage cap |
| `risk.max_risk_pct` | 1.0 | Max account risk per trade (%) |
| `signals.min_confidence` | 60 | Minimum confidence score (0–100) |
| `signals.max_daily` | 3 | Maximum signals per cycle |

---

## Binance Public Data

The system uses only **public** Binance Futures endpoints:

- `/fapi/v1/klines` — OHLCV candles (4H, 1H, 15m)
- `/fapi/v1/ticker/24hr` — 24h volume for universe filter
- `/futures/data/openInterestHist` — historical OI
- `/fapi/v1/premiumIndex` — funding rate & mark price
- `/fapi/v1/exchangeInfo` — symbol information

No Binance API key is required for these endpoints.

---

## Telegram Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) → get `TELEGRAM_BOT_TOKEN`
2. Get your chat/channel ID → set `TELEGRAM_CHAT_ID`
3. Add the bot to your channel as an admin

---

## Running the Scanner

**Single scan cycle (then exit):**
```bash
python -m src.scheduler --once
```

**Continuous 15-minute loop:**
```bash
python -m src.scheduler
```

**Deep scan (same logic, explicit flag):**
```bash
python -m src.scheduler --once --deep-scan
```

**Custom interval (e.g. 5 minutes):**
```bash
python -m src.scheduler --interval 300
```

---

## Deployment (Go Live)

You need only two values: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (see Telegram Setup above).

**1. Configure secrets** — copy `.env.example` to `.env` and fill it in. Never commit `.env` (already git-ignored).

**2. Smoke test** — one live scan against public Binance data (no Telegram spam unless a real setup is found):
```bash
python -m src.scheduler --once
```

**3a. Run 24/7 with Docker (recommended for VPS):**
```bash
docker build -t crypto-alrtbot .
docker run -d --restart unless-stopped --name alrtbot --env-file .env crypto-alrtbot
docker logs -f alrtbot
```

**3b. Run 24/7 on Windows (Task Scheduler):**
- Action: `python` with argument `-m src.scheduler`, start dir = project folder.
- Trigger: at startup + repeat safeguard; the loop itself sleeps 15m between scans.

**3c. Run 24/7 on Linux (systemd):**
```ini
[Unit]
Description=Crypto-AlrtBot signal loop
After=network-online.target
[Service]
WorkingDirectory=/opt/Crypto-AlrtBot
EnvironmentFile=/opt/Crypto-AlrtBot/.env
ExecStart=/usr/bin/python3 -m src.scheduler
Restart=always
[Install]
WantedBy=multi-user.target
```

**Admin dashboard** — every deploy also serves a read-only dashboard (same container, `$PORT`):
- Local: `python -m src.dashboard` → http://localhost:8080
- Endpoints: `/` (overview), `/api/status` (workers), `/api/signals` (recent), `/api/analytics` (breakdowns), `/api/outcomes` (live results ledger), `/healthz` (probe)
- **Outcome tracking**: every published signal is re-checked against live candles each cycle with the same evaluator/fees as research; results accumulate in `data_store/outcomes.json` (win rate, net R, profit factor, equity curve on the dashboard). Young signals are never finalized early.
- **Position sizing & liquidation**: signals show size as % of bank (e.g. `$1244.05 (124.4% of bank)`) plus an estimated isolated-margin liquidation price. The evaluator traces liquidation candle-by-candle — if liq is touched before SL/TP, the trade books the full margin loss (`LIQUIDATED`), not the planned −1R. Applies only when leverage+margin are known, so all historical research results are unchanged.
- Set `ADMIN_TOKEN` to require `?token=...` on all pages except `/healthz`
- Combined service entrypoint: `python -m src.service` (dashboard + scheduler loop, shared `data_store`)

**Notes:**
- 0–3 signals per 15-min cycle is normal; most cycles yield 0 (quality over quantity).
- Duplicate suppression: same symbol+side is not re-sent within 6h (`data_store/scheduler_state.json`).
- No Binance API key needed (public endpoints only). No auto-trading — signals only.

---

## Running Replay

Replay injects a historical timestamp into the exact same production pipeline — no future data is ever used.

```python
from src.backtest.replay import ReplayHarness

harness = ReplayHarness()

# Single timestamp
signals = harness.replay_timestamp("2026-09-01T08:00:00Z", symbols=["BTCUSDT", "ETHUSDT"])

# Multiple timestamps
results = harness.replay_range([
    "2026-09-01T00:00:00Z",
    "2026-09-02T00:00:00Z",
    "2026-09-03T00:00:00Z",
])

# Verify determinism
run1 = harness.replay_timestamp("2026-09-01T00:00:00Z")
run2 = harness.replay_timestamp("2026-09-01T00:00:00Z")
ReplayHarness.assert_deterministic(run1, run2)  # raises if any field differs

# Save for later evaluation
harness.save_replay_results(results, "my_replay.json")
```

**Invariant**: `Replay(timestamp=X) == Replay(timestamp=X)` for identical data + config + strategy version.

---

## Running Historical Signal Evaluation

```python
from src.backtest.evaluator import SignalOutcomeEvaluator, AmbiguousResolution

evaluator = SignalOutcomeEvaluator(
    max_entry_bars=6,           # bars to wait for entry
    max_holding_bars=100,       # max bars in trade
    ambiguous_resolution=AmbiguousResolution.CONSERVATIVE,  # SL-first on ambiguous candles
    taker_fee_pct=0.0004,       # 0.04% Binance taker fee
    slippage_pct=0.0002,        # 0.02% slippage estimate
)

# Load a historical signal + subsequent 15m candles
signal = {"side": "LONG", "entry": 100.0, "tp1": 103.0, "tp2": 106.0, "tp3": 109.0, "stop_loss": 97.0, ...}
subsequent_klines = [...]  # list of OHLCV dicts after signal publication

result = evaluator.evaluate_signal(signal, subsequent_klines)
print(result)
# {
#   "entry_triggered": True,
#   "tp1_hit": True, "tp2_hit": False, "tp3_hit": False, "sl_hit": False,
#   "first_event": "TP1",
#   "realized_r": 1.5, "net_realized_r": 1.493,
#   "mfe_r": 1.5, "mae_r": 0.1,
#   "holding_bars": 8,
#   "status": "TP1_HIT"
# }

# Persist outcomes
evaluator.save_evaluations([result], "evaluations_2026_09.json")
```

**Ambiguous OHLC resolution policies:**

| Policy | Behaviour |
|:---|:---|
| `CONSERVATIVE` (default) | SL hit first — risk-first, never silently assume the win |
| `OPTIMISTIC` | TP hit first |
| `OPEN_PROXIMITY` | Whichever of TP/SL is closer to the candle open is hit first |

---

## Running Threshold Tuning

> **Anti-overfitting**: always sweep on TRAIN data only. Use VALIDATION to select. Touch UNSEEN TEST exactly once.

```python
from src.backtest.tuner import ThresholdTuner, DatasetSplit
from src.config import AppConfig

# 1. Enforce chronological splits — no shuffling
split = DatasetSplit(
    train_timestamps=[...],       # earliest
    validation_timestamps=[...],
    test_timestamps=[...],        # latest — touch once at the very end
)
split.validate_chronology()       # raises if any overlap or inversion

# 2. Parameter sweep on TRAIN only
tuner = ThresholdTuner()

def get_train_evaluations(cfg):
    # Re-run replay + evaluate with this config on TRAIN timestamps
    ...

results = tuner.parameter_sweep(
    base_config=AppConfig(),
    param_grid={
        "volume.min_ratio":   [1.5, 2.0, 2.5],
        "oi.min_change_pct":  [1.0, 2.0, 3.0],
        "risk.min_rr_tp1":    [1.5, 2.0],
    },
    evaluations_provider=get_train_evaluations,
    num_days=30.0,
    sort_by="expected_r",   # objective: robust risk-adjusted performance
)

# 3. Print report
print(tuner.sweep_report_markdown(results, title="Volume/OI Sweep – TRAIN"))

# 4. Walk-forward robustness check
wf_results = tuner.walk_forward_evaluate(all_evaluations, n_windows=3, train_ratio=0.6)

# 5. Compare baseline vs candidate
baseline_m = tuner.calculate_metrics(baseline_evals)
candidate_m = tuner.calculate_metrics(candidate_evals)
print(tuner.compare_configurations(baseline_m, candidate_m))
```

**Report metrics:**

| Metric | Description |
|:---|:---|
| `signals_total` | Total signals generated |
| `signals_per_day` | Average daily output |
| `entry_rate` | % of signals where entry was triggered |
| `tp1_hit_rate` | % of entered trades hitting TP1 |
| `tp2_hit_rate` | % of entered trades hitting TP2 |
| `tp3_hit_rate` | % of entered trades hitting TP3 |
| `sl_rate` | % of entered trades stopped out |
| `win_rate` | % of entered trades with positive net R |
| `average_r` | Mean net R per entered trade |
| `expected_r` | `(WR × avg_win) − (1−WR × avg_loss)` |
| `profit_factor` | Sum of gains / sum of losses |
| `max_drawdown_r` | Maximum cumulative R drawdown |
| `worst_losing_streak` | Worst consecutive losing run |
| `avg_holding_bars` | Average bars held per trade |

---

## Running Tests

```bash
python -m pytest tests/ -v
```

Current status: **158 tests, 0 failures**

```
tests/test_engines.py             —  33 tests  (core engine math)
tests/test_backtest.py            —  89 tests  (replay, evaluator, tuner, lifecycle, scheduler)
tests/test_strategies.py          —  24 tests  (strategy families + registry)
tests/test_dataset_validation.py  —  12 tests  (dataset alignment, cache correctness, no-lookahead)
```

---

## Example Signal

```
🚀 SIGNAL #1

COIN: PUMPUSDT
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
✅ BTC regime: bullish
✅ 15m volume ratio: 2.14x (threshold 1.5x)
✅ OI signal: bullish (+6.30%)
✅ Funding rate: 0.0100%
✅ R:R TP1/TP2/TP3: 2.8R / 4.2R / 5.6R

INVALIDATION:
⚠️ 15m close below 0.00310
⚠️ BTC loses regime support

MANAGEMENT:
→ TP1: close 50%
→ TP2: close 25%
→ TP3: close 25%
→ After TP1: SL → Entry
```

---

## Example Historical Evaluation Result

```json
{
  "signal_id": "SIG-20260901-PUMPUSDT-A1B2C3",
  "symbol": "PUMPUSDT",
  "side": "LONG",
  "entry_triggered": true,
  "actual_entry_price": 0.003245,
  "entry_timestamp": 1756684800000,
  "candles_to_entry": 2,
  "tp1_hit": true,
  "tp2_hit": false,
  "tp3_hit": false,
  "sl_hit": false,
  "first_event": "TP1",
  "mfe_r": 1.5,
  "mae_r": 0.1,
  "holding_bars": 8,
  "realized_r": 1.5,
  "net_realized_r": 1.493,
  "fees_cost_r": 0.007,
  "status": "TP1_HIT"
}
```

---

## Example Threshold-Tuning Result

```
# Volume/OI Sweep – TRAIN (30 days)

| Rank | Parameters                           | win_rate | expected_r | profit_factor | max_drawdown_r |
| :--- | :---                                 | :---:    | :---:      | :---:         | :---:          |
|    1 | volume.min_ratio=2.0, oi.min_change_pct=2.0 | 0.62 | 0.41 | 1.85 | 3.2 |
|    2 | volume.min_ratio=2.5, oi.min_change_pct=2.0 | 0.65 | 0.38 | 1.72 | 2.1 |
|    3 | volume.min_ratio=1.5, oi.min_change_pct=3.0 | 0.55 | 0.29 | 1.43 | 4.8 |
```

> Top rank = highest `expected_r` on TRAIN partition.  
> Validate on VALIDATION partition before accepting. Test on UNSEEN TEST exactly once.

---

## Signal Lifecycle

```
GENERATED
    ↓
WAITING_FOR_ENTRY
    ↓
ACTIVE
    ↓
TP1_HIT → TP2_HIT → TP3_HIT   ← clean win
    ↓
STOPPED_OUT                     ← trailing stop hit on remainder

WAITING_FOR_ENTRY → EXPIRED
WAITING_FOR_ENTRY → INVALIDATED
ACTIVE → STOPPED_OUT
ACTIVE → INVALIDATED
```

Lifecycle tracking is **theoretical** — it tracks what would have happened to the signal, not actual positions.

---

## Known Limitations

1. **Entry model is pullback-only** (market entry also supported). FVG/SMC entry is a future extension.
2. **Structural SL** is determined from recent swing lows/highs on 15m — not multi-timeframe structure.
3. **No 5m timeframe** — excluded from V1 per PRD.
4. **OI and funding** are fetched live for each symbol; historical OI availability depends on Binance retention window (~30 days).
5. **Ambiguous OHLC candles**: resolution is configurable but no intra-bar tick data is available — all policies are approximations.
6. **No walk-forward auto-tuning**: the tuner provides the tooling; the researcher drives the workflow.
7. **Scheduler is single-threaded** — not suitable for scanning thousands of symbols in real-time.
8. **No Jesse integration in production** — Jesse adapter is export-only for independent offline validation.

---

## Recommended Next Steps

1. **Run historical replay** over 60–90 days of TRAIN data and evaluate outcomes.
2. **Run `parameter_sweep`** on TRAIN partition; validate top-N on VALIDATION.
3. **Run UNSEEN TEST exactly once** with the selected configuration.
4. **Extend entry models** (FVG, breakout retest) and re-evaluate.
5. **Add 5m entry refinement** timeframe.
6. **Jesse adapter**: export replay signals to Jesse strategy for independent cross-validation.

---

## Disclaimer

> **This software generates trading signals for informational and research purposes only.**  
> It does not constitute financial advice.  
> Past performance in backtesting does not guarantee future results.  
> Cryptocurrency futures trading carries significant risk of loss.  
> Use at your own risk.
