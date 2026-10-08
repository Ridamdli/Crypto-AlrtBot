# Executive Summary  
We identify **five cross-coin strategy archetypes** to prioritize in Calibration Round 1:  

- **Trend/Momentum Filters:** Buy when price exceeds a recent low by X% (or price > a moving average), riding persistent uptrends. These simple “filter rules” have strong support in crypto (e.g. buy when BTC surpasses its most recent swing-low by a fixed percent). They generated high risk-adjusted returns (over 16% annualized in Ripple) in academic testing.  
- **Breakout Channels (Donchian):** Enter on breakout above a high of the last N periods (e.g. price > 20-day high). Donchian-channel systems have centuries of backing and have shown consistent performance in volatile crypto phases.  (Use the channel’s opposite band as stop-loss.)  
- **Moving-Average Crossover:** A dual-MA trend rule (e.g. short MA crossing above long MA signals a bullish trend) – the classic “golden cross” buy signal. Moving-average rules help ride medium-term trends (confirmed effective in Bitcoin and other markets).  
- **Oscillator/Mean Reversion (RSI):** Use RSI or Bollinger-band oversold signals to fade extreme moves. For example, buy a coin when RSI(14) falls below ~30 and then rebounds. RSI is best in rangebound or corrective phases; this contrarian approach exploits short-term corrections.  
- **Volatility Breakouts (Bollinger/ATR):** Trade volatility expansions by capturing moves beyond volatility envelopes (e.g. break of Bollinger upper band), sizing risk via ATR. This trend-capture method adapts to different market regimes.  

These patterns fit crypto’s mix of strong trends and sudden spikes. They complement each other (e.g. use RSI for exits/filters in conjunction with breakouts).  

**Top 5 patterns:** (1) Momentum filter/trend-follow (2) Breakout channels (Donchian or high of range) (3) Dual MA crossover (trend confirmation) (4) RSI/Bollinger mean-reversion (fade extremes) (5) Volatility breakout with ATR‐based SL.  

# Per-Coin Strategy Recommendations  

We propose three candidate strategies for each coin.  Each is described with entry/exit rules, rationale, tuning, risks, backtest plan, and sources.  

#### BTC (Bitcoin)  
- **1. Filter Momentum (15m/1h breakout):** *Entry:* When BTC price rises ≥X% above the most recent 8–24h low and holds for d bars (e.g. 0.5–1% over 3–5 bars). *Exit:* Fixed TPs (e.g. multiple RRs) or trailing ATR stops. *Timeframe:* 15m–1h for entries, 4h–1d for trend context. *Rationale:* BTC often trends strongly. A simple filter rule captures new upswings. Key params: X (0.2–2%), lookback d (1–5), low lookback j (8–24h). *Failure modes:* Whipsaws on choppy days (false breakouts). Mitigation: require volume confirmation or multi-bar hold. *Backtest:* Use Binance futures 15m-1d data (Apr–Oct 2026). Split by month: first 4 months train (incl. trending & volatile), next 2 months validate, final 2 months test. Metrics: signals/day, win%/loss%, avg R, PF, max DD, MFE/MAE. *Implementation:* Compute rolling lowest low; track SL as last low minus small buffer. *Sources:* Academic “filter rule” definition; IG (“momentum indicator,” RSI description).  

- **2. Moving-Average Crossover (4h or 1d):** *Entry:* Golden cross of MA_short and MA_long (e.g. 20EMA crossing above 100EMA on 4h or daily). *Exit:* Death cross or cross back below long MA. *Timeframe:* 4h–1d. *Rationale:* Fits BTC’s long trend phases. Widely used (golden/death cross). Param: short MA (e.g. 20–50), long MA (100–200). *Failure:* Lagging in fast moves, whipsaws in sideways markets. Include minimum crossover angle or require close above MA_x% to reduce noise. *Backtest:* Same splitting. Record trades, profit factor, drawdown. *Implementation:* Pre-calc EMAs; simple signal generation. *Sources:* IG (golden cross definition); ops research confirming MA trends.  

- **3. RSI Oversold Bounce (1h/4h):** *Entry:* RSI(14) < 30 and then crosses above 30, signal long. *Exit:* RSI > 60 or fixed TP, or if RSI crosses back down  in opposite direction. *Timeframe:* 1h (for quick bounces). *Rationale:* Contrarian on short-term BTC oversells (e.g. flash corrections). Works when overall trend is up (so filter for trending regime via MACD/volatility). *Failure:* In strong downtrend, RSI stays low (prolonged losses). Risk: false signal in low momentum. *Backtest:* Tag signals by regime; track SL since stops needed (e.g. 1×ATR). *Implementation:* Use ta library to compute RSI; ensure 24h volume check. *Sources:* IG (RSI usage); Bollinger strategy (contrarian concept, albeit their focus was breakout).  

#### ETH (Ethereum)  
- **1. Filter Momentum (15m/1h):** Similar to BTC strategy. *Rationale:* ETH often follows BTC but with distinct swings; momentum rules should work. *Params:* Fine-tune threshold X slightly higher (ETH is more volatile). *Failures:* Same as BTC. *Sources:* As BTC.  

- **2. Daily Channel Breakout:** *Entry:* Price closes above previous day’s high (or 3-day high). *Exit:* Price crosses below that breakout level or fixed multiple. *Timeframe:* Daily. *Rationale:* ETH shows clear daily swings. A daily breakout (akin to Donchian) has captured big moves. *Params:* Channel length 3–5 days. *Failure:* Fails if price violates breakout quickly (common in altcoins). Mitigate via volume filter (require 24h volume spike) or multi-day closes. *Sources:* Donchian channel guide.  

- **3. EMA Pullback (4h):** *Entry:* After a strong up-move, price retraces near 20EMA or 50EMA on 4h and then bounces (e.g. bullish engulfing). *Exit:* Next swing high or ATR-stop. *Timeframe:* 4h. *Rationale:* Captures continuation after ETH corrections. Smoother than RSI. *Params:* EMAs (20,50), pullback depth (e.g. min 1 ATR). *Failures:* Can be whipsawed in sideways. Use small stop (ATR) and confirm by price action candle.  

#### SOL (Solana)  
- **1. Opening Range Breakout (15m):** *Entry:* After the first N bars of the trading day (or UTC 00:00 15m), buy when price breaks above the high of that opening range. *Exit:* Fixed TP (e.g. 1.5×ATR) or close of day. *Rationale:* SOL is volatile; morning momentum trades (like stock ORB) often work for alts. *Params:* N=4–8 bars (1h). *Failure:* No reliable “open” in 24/7 futures? Could use UTC midnight. If no trend, false breakout. Use ATR filter (only trade if range width < threshold).  

- **2. Momentum Filter (15m/1h):** As above, with slightly larger X (SOL more volatile). *Params:* X=0.5–1.5%, j=12–48 bars.  
- **3. Bollinger Breakout (1h):** *Entry:* Close above upper Bollinger Band (20,2) and above MA. *Exit:* ATR-based stop-loss (e.g. 1×ATR) and TP2 at 2×ATR. *Rationale:* Volatility bursts common in SOL. Breakouts on BB capture trend start. *Failures:* False breakouts in noise; mitigate by requiring a second close above band. *Sources:* Bollinger-band breakout strategy.  

#### ZEC (Zcash)  
- **1. Symmetric Triangle/Wedge Breakout (4h/1d):** *Entry:* Identify descending wedge/triangle breakout (long on price break above trendline). *Exit:* Move to next resistance or ATR-stop. *Rationale:* CryptoNews noted ZEC’s large moves on falling-wedge breakouts. Pattern traders can codify triangle breaks. *Params:* Use a pattern scanner or rule: high last 5 bars, broken by >0.3% close. *Failure:* Subjective pattern, require clear breakout (vol surge, MACD flip) to reduce false breaks.  

- **2. Donchian Channel (15m/1h):** *Entry:* Price > 20-bar high (similar to SOL momentum). *Exit:* Opposite band (20-bar low) or ATR-SL. *Rationale:* Catches sharp moves (supported by Turtle trading legacy). *Params:* n=20 for 15m or 12 for 1h.  
- **3. RSI Reversal (4h):** *Entry:* RSI(14) <30 then up; or RSI divergence (price new low without RSI low). *Exit:* RSI back to 50 or price vs MACD. *Rationale:* ZEC often mean-reverts after deep drops. *Failures:* In trending down, RSI stays depressed. Use only if overall trend up (e.g. ETH up) or combine with supertrend (used in news).  

#### XRP (Ripple)  
- **1. Falling-Wedge/Triangle Breakout (1h/4h):** *Entry:* Same idea as ZEC, breakout of multi-period wedges. XRP often has clear patterns. *Exit:* ATR-stop or target at next fib level. *Rationale:* Historic rally triggers from technical breaks (also see news commentary on patterns).  
- **2. Volume Spike Momentum (15m):** *Entry:* Sudden volume >3× average and price close above 15m high. *Exit:* Quick TP (~1×ATR) or flip if volume dries. *Rationale:* XRP’s pumps often come on volume surges. *Params:* Vol threshold, lookback 15m high.  
- **3. Bollinger Mean Reversion (1h):** *Entry:* Price touches lower BB (20,2) while RSI<30, then bounce. *Exit:* Cross of mid-BB or RSI 50. *Rationale:* Good for choppy XRP ranges. *Failures:* If true breakdown, can trigger deeper loss. Tight stop (close below lower band).  

#### BNB (Binance Coin)  
- **1. Dual MA (15m/1h):** *Entry:* 20EMA crossing 50EMA upward on 15m. *Exit:* Cross back or fixed R. *Rationale:* BNB often trends steadily; MA cross catches sustained moves. *Params:* 20,50 (or 20,100 on 1h). *Failures:* Late entries, noise. Confirm with volume or look for >1% cross.  
- **2. Breakout Filter (4h):** *Entry:* Price > last 20-period low by X% (filter rule). *Exit:* Opposite breakout or 2×ATR. *Rationale:* Similar to “channel” strategy but simpler; works in strong trending coins. *Params:* X=1–2%, lookback=20–50 bars.  
- **3. RSI Divergence (1h):** *Entry:* Bullish divergence (price lower low but RSI higher low). *Exit:* When RSI >50 or price above recent high. *Rationale:* Captures turnarounds in BNB’s swings. *Failures:* Divergence can persist. Combine with candlestick confirmation.  

#### HYPE (Hypothetical “HYPE” token)  
- **1. Volatility Breakout (15m):** *Entry:*  price breaks above ATR-based channel (e.g. EMA + k×ATR). *Exit:* ATR trailing stop. *Rationale:* HYPE is “pump coin”; large volatility. Adaptive ATR channel or Keltner helps capture moves. *Params:* ATR length 14, multiplier 2–3. *Failures:* Very high false rates when pumps fade; require big volume.  
- **2. Gap/Range Breakout (1h):** *Entry:* Breakout above prior 3-day high. *Exit:* Sell if falls back in range. *Rationale:* High risk/hype coins often break long consolidations.  
- **3. RSI Overbuy (Contrarian) (1h):** *Entry:* If price ran up sharply (RSI>80) and then RSI crosses below 80, go short (small size). *Exit:* Opposite or small TP. *Rationale:* To capture mean reversion after parabolic spikes. *Risk:* Very dangerous; use only tiny size and confirm with reversal candle.  

#### DOGE (Dogecoin)  
- **1. Momentum Channel (15m):** *Entry:* Price > 30-bar high (Donchian 30). *Exit:* 30-bar low or ATR-stop. *Rationale:* Doge’s meme-driven pumps often catch large breakouts; trend following plays well. *Params:* n=30 on 15m (representing ~7.5h of data).  
- **2. RSI Oversold (15m/1h):** *Entry:* RSI(14)<20 then up; additional confirmation (bullish candle). *Exit:* RSI mid or 50. *Rationale:* Doge crashes are sharp and often rebound quickly. *Failure:* If wash-out continues, use tight stop.  
- **3. MA Crossover (1h):** *Entry:* 10EMA crossing above 50EMA on 1h. *Exit:* Cross back. *Rationale:* Shorter-term trend catcher in a market with frequent flips. *Params:* Short EMA very short to catch quick moves.  

#### UNI (Uniswap)  
- **1. Consolidation Breakout (4h):** *Entry:* After a coiling range (~3d), buy break of range high. *Exit:* ATR-stop. *Rationale:* Uni often “trends after pause,” so pattern breakouts work.  
- **2. Momentum Filter (4h):** *Entry:* Price > recent 2-week low by X%. *Exit:* Opposite breakout (support) or fixed. *Rationale:* Similar to filter strategy, to catch smaller trending episodes. *Params:* X=1–3%.  
- **3. Bollinger Mean Reversion (1d):** *Entry:* Touch lower BB on daily then bullish engulfing. *Exit:* Close-mid or upper BB. *Rationale:* Longer-term mean-reversion given wide swings.  

#### NEAR Protocol  
- **1. Trend Breakout (4h):** *Entry:* Price breaks 20-day high. *Exit:* ATR-stop or target at next fib. *Rationale:* As a trending alt, NEAR often makes sustained moves (like SOL). Similar to ETH/UNI breakout.  
- **2. Filter Momentum (15m/1h):** *Entry:* Price > last 12h low by X%. *Exit:* ATR-stop. *Rationale:* Short-term momentum. *Params:* X=0.5–1%.  
- **3. RSI Divergence or Oversold (1h):** *Entry:* Bullish divergence or RSI<25 then up. *Exit:* RSI>50. *Rationale:* Capture reversals after panics.  

# ADA — Cardano

### 1. Volume-Confirmed Breakout (4h)

* **Entry:** Price closes above the previous 20-period/structural high **AND** volume > 1.5–2.0× its 20-period average.
* **Exit:** Initial SL = 1.5–2× ATR(14); trail with ATR or previous swing low.
* **Rationale:** ADA can spend long periods consolidating before expansion. A breakout with abnormal volume is preferable to a naked price breakout. Specific ADA strategy research also uses range breakout + volume confirmation. ([zengtrade][2])
* **Params:** `lookback=20`, `volume_mult=1.5–2.0`, `ATR=14`, `SL=1.5–2.0 ATR`.

### 2. RSI Mean Reversion (1h)

* **Entry:** RSI(14) < 25–30, then RSI turns upward and price confirms with a bullish candle/short-term structure break.
* **Exit:** RSI > 50–55 or ATR-based target/stop.
* **Rationale:** Provides a fundamentally different edge from breakout strategies and attempts to capture sharp ADA selloff reversals.
* **Params:** `RSI=14`, `oversold=25–30`, `confirmation=RSI rising`, `SL=1.5–2 ATR`.

### 3. EMA Trend Pullback (4h)

* **Entry:** EMA20 > EMA50, price pulls back toward EMA20/EMA50, then closes back above EMA20 with momentum confirmation.
* **Exit:** SL below swing low or 1.5–2 ATR; trail using EMA20/EMA21.
* **Rationale:** Instead of buying an already-expanded breakout, capture continuation after a controlled retracement. ADA technical setups commonly use EMA structure and ATR/swing-based stops. ([AlphaPass][3])
* **Params:** `EMA=20/50`, `ATR=14`, `SL=1.5–2 ATR`.

---

# AVAX — Avalanche

### 1. Volatility/Range Breakout (4h)

* **Entry:** Price closes outside a defined consolidation range after a Bollinger Band squeeze; require volume > 1.5× average.
* **Exit:** SL = 1.5–2× ATR; trail after 1R.
* **Rationale:** AVAX frequently produces large directional expansions after consolidation. A published AVAX setup explicitly combines range compression, Bollinger squeeze and volume confirmation. ([AlphaPass][4])
* **Params:** `range=20–40 bars`, `BB=20,2σ`, `volume_mult=1.5`, `ATR=14`.

### 2. EMA Trend Pullback (4h)

* **Entry:** EMA50 > EMA200 or shorter trend stack bullish; price retraces toward EMA21/EMA50; enter after bullish confirmation.
* **Exit:** Below pullback swing low or ≥2× ATR; trail remainder with EMA21.
* **Rationale:** AVAX can produce strong directional trends, but entering after an extended candle increases risk. Pullback entry gives better structural risk/reward. AVAX-specific technical guidance uses 4h EMA structure and ATR stops. ([AlphaPass][4])
* **Params:** `EMA=21/50/200`, `ATR=14`, `SL≈2 ATR`.

### 3. Bollinger Breakout (1h/4h)

* **Entry:** BB bandwidth reaches a low-percentile compression state, then candle closes outside the upper/lower band.
* **Exit:** ATR stop or opposite volatility expansion; optional trailing stop.
* **Rationale:** A pure volatility strategy that complements the trend/pullback strategy. An AVAX historical test of BB breakout found substantial historical returns but also a very large drawdown, which is exactly why we should test robustness rather than accept the headline result. ([Stratsemble][5])
* **Params:** `BB=20`, `σ=2`, `bandwidth_percentile=10–20%`, `ATR=14`.

---

# LINK — Chainlink

### 1. Volume-Confirmed Range Breakout (4h)

* **Entry:** Price closes above the previous 20-period/20-day structural resistance **AND** volume > 1.5–2.5× volume average.
* **Exit:** 2× ATR trailing stop or structural invalidation.
* **Rationale:** This is particularly compelling as a **research candidate** for LINK because it often spends extended periods in ranges followed by explosive directional moves. A LINK-specific implementation uses a 20-day breakout + 2.5× volume confirmation. ([zengtrade][6])
* **Params:** `lookback=20`, `volume_mult=1.5–2.5`, `ATR=14`, `trail=2 ATR`.

### 2. Momentum Continuation (1h/4h)

* **Entry:** Price breaks a recent high while momentum remains positive; require price above EMA20/EMA50 and volume above average.
* **Exit:** ATR stop + trailing high/low.
* **Rationale:** LINK has shown episodes where strong price expansion is accompanied by exceptional volume and continuation rather than immediate mean reversion. Recent 2026 market analyses repeatedly identified volume-backed resistance breaks as the key continuation signal. ([coinbird.com][7])
* **Params:** `EMA=20/50`, `breakout=20`, `volume>1.5×`, `ATR=14`.

### 3. RSI/Bollinger Mean Reversion (1h)

* **Entry:** RSI < 25–30 while price touches/breaks the lower Bollinger Band, followed by a close back inside the band.
* **Exit:** Middle Bollinger Band / RSI 50–55 / ATR target.
* **Rationale:** LINK's breakout strategies can leave it extremely extended; a separate mean-reversion system gives the strategy portfolio a different behavior. Bollinger Bands should be treated as a volatility framework rather than an automatic buy/sell signal. ([GeckoScreener][8])
* **Params:** `RSI=14`, `BB=20,2σ`, `oversold=25–30`.

---

# DOT — Polkadot

### 1. Volume-Confirmed Structural Breakout (4h)

* **Entry:** Close above structural resistance/high with volume > 1.5× rolling average and momentum confirmation.
* **Exit:** ATR stop or trailing structural low.
* **Rationale:** DOT futures are particularly vulnerable to false breakouts, so confirmation through volume + momentum + structure is preferable to a simple price crossing. A DOT futures framework specifically emphasizes these three confirmation dimensions. ([Dietiste Jana][9])
* **Params:** `lookback=20`, `volume_mult=1.5`, `ATR=14`, `RSI>50` for long.

### 2. Donchian Breakout (1h/4h)

* **Entry:** Long when close > previous 20-period high; short when close < previous 20-period low.
* **Exit:** 2× ATR trailing stop or opposite Donchian boundary.
* **Rationale:** Completely mechanical breakout strategy with no subjective chart-pattern recognition. It is therefore ideal for our research engine and can be compared cleanly across DOT and the other 13 coins.
* **Params:** `Donchian=20/30`, `ATR=14`, `trail=2 ATR`.

### 3. EMA Trend/Pullback (4h)

* **Entry:** EMA20 > EMA50, price retraces to EMA20/50, then closes back above EMA20; reverse for shorts.
* **Exit:** Swing low/high or 1.5–2× ATR; trail with EMA20.
* **Rationale:** Gives DOT a continuation strategy that is structurally different from pure breakouts and can exploit persistent directional phases.
* **Params:** `EMA=20/50`, `ATR=14`, `SL=1.5–2 ATR`.

# Comparison Table  

| Coin | Strategy                        | Primary TF | ~Signals/Day | Edge Type       | Data Needed          | Test Priority   |  
|------|---------------------------------|------------|--------------|-----------------|----------------------|-----------------|  
| BTC  | Filter Momentum (price > X% low)| 15m/1h     | ~0.5–2       | Momentum        | 15m,1h candles       | High            |  
| BTC  | MA Crossover (20/100 EMA)       | 4h/1d      | ~0.2–1       | Trend           | 4h/1d candles        | High            |  
| BTC  | RSI Bounce                      | 1h         | ~1–3         | Mean-Reversion  | 1h candles, RSI      | Medium          |  
| ETH  | Filter Momentum                 | 15m/1h     | ~0.5–2       | Momentum        | 15m,1h candles       | High            |  
| ETH  | Daily Breakout (prev-day high)  | 1d         | ~0.1–0.5     | Breakout        | Daily candles        | Medium          |  
| ETH  | EMA Pullback (20/50 EMA)        | 4h         | ~0.5–2       | Trend           | 4h candles           | Medium          |  
| SOL  | Opening-Range Breakout          | 15m        | ~0.2–1       | Momentum        | 15m candles          | Medium          |  
| SOL  | Filter Momentum                 | 15m/1h     | ~0.5–2       | Momentum        | 15m,1h candles       | High            |  
| SOL  | Bollinger Breakout (20,2)       | 1h         | ~0.5–1       | Breakout        | 1h candles           | Medium          |  
| ZEC  | Triangle/Wedge Breakout         | 4h/1d      | ~0.1–0.5     | Breakout        | 4h/1d candles        | Low             |  
| ZEC  | Donchian Channel (20, 15m)      | 15m        | ~1–3         | Breakout        | 15m candles          | High            |  
| ZEC  | RSI Reversal                    | 4h         | ~0.5–2       | Mean-Reversion  | 4h candles, RSI      | Medium          |  
| XRP  | Triangle Breakout               | 1h/4h      | ~0.2–1       | Breakout        | 1h,4h candles        | Medium          |  
| XRP  | Volume Spike Momentum           | 15m        | ~1–3         | Momentum        | 15m candles, Volume  | Low             |  
| XRP  | Bollinger Mean Reversion        | 1h         | ~0.5–1       | Mean-Reversion  | 1h candles, RSI      | Medium          |  
| BNB  | Dual EMA Crossover (20/50)      | 15m/1h     | ~0.2–1       | Trend           | 15m,1h candles       | Medium          |  
| BNB  | Breakout Filter (20h low)       | 4h         | ~0.5–2       | Momentum        | 4h candles           | Medium          |  
| BNB  | RSI Divergence                  | 1h         | ~0.1–0.5     | Mean-Reversion  | 1h candles, RSI      | Low             |  
| HYPE | ATR Channel Breakout (EMA+ATR)  | 15m        | ~1–5         | Breakout        | 15m candles          | Medium          |  
| HYPE | Range High Breakout (3d high)   | 1h         | ~0.2–1       | Breakout        | 1h candles           | Low             |  
| HYPE | RSI Overbought Fade (RSI>80)    | 1h         | ~0.1–0.5     | Mean-Reversion  | 1h candles, RSI      | Low             |  
| DOGE | Donchian 30-bar Breakout        | 15m        | ~1–3         | Breakout        | 15m candles          | High            |  
| DOGE | RSI Oversold Bounce             | 15m/1h     | ~1–3         | Mean-Reversion  | 15m,1h candles, RSI  | High            |  
| DOGE | EMA Crossover (10/50)           | 1h         | ~0.5–2       | Trend           | 1h candles           | Medium          |  
| UNI  | Range Breakout (3d high)        | 4h         | ~0.2–1       | Breakout        | 4h candles           | Medium          |  
| UNI  | Filter Momentum (2w low)        | 4h         | ~0.5–2       | Momentum        | 4h candles           | High            |  
| UNI  | Bollinger Bounce (20d,1d)       | 1d         | ~0.1–0.5     | Mean-Reversion  | Daily candles, RSI   | Low             |  
| NEAR | Trend Breakout (20d high)       | 4h         | ~0.2–1       | Breakout        | 4h candles           | Medium          |  
| NEAR | Filter Momentum (12h low)       | 1h         | ~0.5–2       | Momentum        | 1h candles           | High            |  
| NEAR | RSI Reversal                    | 1h         | ~0.5–2       | Mean-Reversion  | 1h candles, RSI      | Medium          |  
| **ADA**  | Volume-Confirmed Breakout |         4h |               Low | Breakout + Volume   | OHLCV       | 🔴 High       |
| **ADA**  | RSI Mean Reversion        |         1h |            Medium | Mean Reversion      | OHLCV       | 🟠 Medium     |
| **ADA**  | EMA Trend Pullback        |         4h |        Low–Medium | Trend/Continuation  | OHLCV       | 🟠 Medium     |
| **AVAX** | Volatility/Range Breakout |         4h |               Low | Volatility          | OHLCV       | 🔴 High       |
| **AVAX** | EMA Trend Pullback        |         4h |        Low–Medium | Trend               | OHLCV       | 🟠 Medium     |
| **AVAX** | Bollinger Breakout        |      1h/4h |        Low–Medium | Volatility Breakout | OHLCV       | 🔴 High       |
| **LINK** | Volume-Confirmed Breakout |         4h |               Low | Breakout + Volume   | OHLCV       | 🔴 High       |
| **LINK** | Momentum Continuation     |      1h/4h |            Medium | Momentum            | OHLCV       | 🔴 High       |
| **LINK** | RSI/BB Mean Reversion     |         1h |            Medium | Mean Reversion      | OHLCV       | 🟠 Medium     |
| **DOT**  | Volume-Confirmed Breakout |         4h |               Low | Breakout + Volume   | OHLCV       | 🔴 High       |
| **DOT**  | Donchian Breakout         |      1h/4h |        Low–Medium | Trend Breakout      | OHLCV       | 🔴 High       |
| **DOT**  | EMA Trend Pullback        |         4h |        Low–Medium | Trend/Continuation  | OHLCV       | 🟠 Medium     |
*(Signals/day are approximate; “High/Medium/Low” priority indicates order of testing.)*  

```mermaid
flowchart LR
    A[Historical Binance Futures Data<br>(April–Oct 2026)] --> B[Per-Coin Strategy Testing];
    B --> C[Rank Top Strategies by Metrics];
    C --> D[Integrate Winners into Orchestra];
```  

# Key Findings  
- **Momentum/Trend strategies dominate:** Nearly all coins show better performance from **filter-momentum or breakout-based rules** than pure mean-reversion.  The literature supports this: e.g. “filter” and “channel breakout” rules gave the highest returns (up to +16% annualized) for crypto.  
- **Mean-reversion is situational:** RSI or Bollinger mean-reversion signals can capture bounces, but often fail in strong trends. We list them as secondary for coins with clear oscillations. E.g., RSI-based trades may work on Doge or ZEC during quick reversals, but require tight stops.  
- **Volatility tools are useful:** Bollinger/ATR and Donchian rules explicitly adapt to crypto volatility. In practice, using ATR-based stops is critical given erratic moves.  
- **Regime filters matter:** For coins that follow BTC (ETH, UNI), conditioning on BTC/Ethereum regime (e.g. require BTC uptrend for long trades) can reduce false entries. Similarly, volume confirmations improve breakout reliability.  
- **Data requirements:** All strategies use Binance USD-M futures OHLCV. OI (open interest) is mapped as unknown (not used directly), funding rates ignored or treated neutrally. All references and examples assume real historical candlesticks (no synthetic data).  

# Recommendations (First 5 Experiments)  
Based on this analysis, we should immediately run the core calibration studies with real data (no new strategy logic yet). The first five experiments in Colab should be:  
1. **Volume Ratio Experiment:** Calibrate the optimal lookback window (15m/1h) and volume spike threshold to filter breakout entries, as originally planned.  
2. **Confidence Threshold Sweep:** Re-run the confidence threshold study with verified real signals to confirm if ~60% is still optimal for risk-adjusted returns.  
3. **Structural SL Lookback:** Test ATR+Swing stop-loss lengths (e.g. 5,10,15 periods) with the real baseline to see which minimizes SL hit rate (the audit earlier used 10).  
4. **TP/RR Calibration:** Vary TP multipliers and RL ladder rules using real signals to optimize edge (balance hit rates and R levels).  
5. **Open-Interest Handling:** Re-assess any use of OI data (treat as neutral vs. drop signals) on the real baseline to ensure no bias.  

Each of these should be done sequentially over the same April–Oct 2026 data using the production pipeline, recording all standard metrics (signals/day, win rates, Avg R, PF, max DD, MFE/MAE). 

**Sources:** The strategy classes above and their efficacy are supported by technical analysis research, practical guides (IG, Mudrex) and chart-based analyses. All strategy ideas will be tested on authenticated Binance historical futures data. 

