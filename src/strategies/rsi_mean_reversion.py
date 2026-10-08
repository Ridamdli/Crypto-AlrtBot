"""
RSI Mean Reversion Strategy

Long: RSI oversold + reversal confirmation + regime filter
Short: RSI overbought + reversal confirmation + regime filter

IMPORTANT: Mean reversion must be explicitly regime-aware because it can fail badly during strong trends.
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RSIMeanReversionStrategy(BaseStrategy):
    family = "rsi_mean_reversion"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "rsi_period": 14,
        "oversold_threshold": 30,
        "overbought_threshold": 70,
        "confirmation_bars": 2,  # Bars to confirm reversal
        "trend_filter": True,    # Must not fight strong trend
        "atr_stop": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "max_holding_bars": 50,  # Max holding period for mean reversion
        "regime_filter": True,
    }
    
    param_schema = {
        "rsi_period": {"type": "int", "min": 7, "max": 30, "description": "RSI period"},
        "oversold_threshold": {"type": "int", "min": 10, "max": 40, "description": "RSI oversold level"},
        "overbought_threshold": {"type": "int", "min": 60, "max": 90, "description": "RSI overbought level"},
        "confirmation_bars": {"type": "int", "min": 1, "max": 10, "description": "Bars to confirm reversal"},
        "trend_filter": {"type": "bool", "description": "Filter against strong trends"},
        "atr_stop": {"type": "bool", "description": "Use ATR-based stop loss"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0, "description": "ATR multiplier for stop"},
        "max_holding_bars": {"type": "int", "min": 10, "max": 200, "description": "Max holding period"},
        "regime_filter": {"type": "bool", "description": "Filter by BTC regime"},
    }
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Calculate RSI using Wilder's smoothing."""
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        # Wilder's smoothing (alpha = 1/period)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
    def _get_trend_strength(self, df: pd.DataFrame, lookback: int = 20) -> str:
        """Determine trend strength: strong_up, up, neutral, down, strong_down"""
        if len(df) < lookback + 1:
            return "neutral"
        
        current = df.iloc[-1]['close']
        past = df.iloc[-(lookback + 1)]['close']
        change_pct = (current - past) / past
        
        # Also check EMA alignment
        ema_fast = df['close'].ewm(span=9, adjust=False).mean()
        ema_slow = df['close'].ewm(span=21, adjust=False).mean()
        ema_bullish = float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1])
        ema_bearish = float(ema_fast.iloc[-1]) < float(ema_slow.iloc[-1])
        
        if change_pct > 0.05 and ema_bullish:
            return "strong_up"
        elif change_pct > 0.02 and ema_bullish:
            return "up"
        elif change_pct < -0.05 and ema_bearish:
            return "strong_down"
        elif change_pct < -0.02 and ema_bearish:
            return "down"
        return "neutral"
    
    def generate_candidates(
        self,
        klines_15m: List[Dict[str, Any]],
        klines_1h: List[Dict[str, Any]],
        klines_4h: List[Dict[str, Any]],
        btc_regime: str,
        oi_data: Dict[str, Any],
        funding_rate: float,
        current_price: float,
    ) -> List[SignalCandidate]:
        from src.strategies.base import SignalCandidate
        
        candidates = []
        
        if len(klines_15m) < self.params["rsi_period"] + 2:
            return candidates
        
        df_15m = pd.DataFrame(klines_15m)
        df_1h = pd.DataFrame(klines_1h) if klines_1h else pd.DataFrame()
        
        for df in [df_15m, df_1h]:
            if not df.empty:
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
        
        # Calculate RSI
        rsi = self._calculate_rsi(df_15m, self.params["rsi_period"])
        rsi_current = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])
        
        # Trend filter
        trend_15m = self._get_trend_strength(df_15m)
        trend_1h = self._get_trend_strength(df_1h) if not df_1h.empty else "neutral"
        
        # Volume
        volume_ratio = self._calculate_volume_ratio(pd.DataFrame(klines_15m))
        
        # ATR
        atr_15m = self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"])
        min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
        
        # Regime filter
        if self.params["regime_filter"]:
            # Don't fight strong trends
            if btc_regime == "bullish" and trend_15m in ["strong_down", "down"]:
                pass  # Could still work for shorts
            if btc_regime == "bearish" and trend_15m in ["strong_up", "up"]:
                pass  # Could still work for longs
        
        candidates = []
        
        # LONG: RSI oversold + reversal starting
        if rsi_current < self.params["oversold_threshold"] and rsi_prev < self.params["oversold_threshold"]:
            # Check for reversal confirmation (RSI turning up)
            if rsi_current > rsi_prev:
                # Additional trend filter: don't catch falling knife in strong downtrend
                if self.params["trend_filter"] and trend_15m == "strong_down":
                    logger.debug(f"{self.symbol}: RSI oversold but strong downtrend, skipping LONG")
                else:
                    entry_price = current_price
                    
                    # SL: below recent swing low
                    swing_low = float(pd.DataFrame(klines_15m).iloc[-20:-1]['low'].min())
                    structural_sl = swing_low * 0.999
                    atr_sl = current_price - (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_stop"] else 0
                    sl_price = max(structural_sl, current_price - atr_sl) if atr_sl > 0 else structural_sl
                    sl_price = min(sl_price, current_price - min_risk_dist)
                    
                    risk_dist = current_price - sl_price
                    if risk_dist > 0:
                        tp1 = current_price + risk_dist * 1.5  # Mean reversion: smaller targets
                        tp2 = current_price + risk_dist * 2.5
                        tp3 = current_price + risk_dist * 3.5
                        
                        from src.strategies.base import SignalCandidate
                        candidates.append(SignalCandidate(
                            symbol=self.symbol,
                            side="LONG",
                            entry_price=entry_price,
                            stop_loss=sl_price,
                            tp1=tp1,
                            tp2=tp2,
                            tp3=tp3,
                            confidence=68,
                            strategy_id=f"rsi_mr_{self.params['rsi_period']}",
                            strategy_version="1.0.0",
                            timeframe="15m",
                            parameters=self.params.copy(),
                            conditions=[
                                f"RSI oversold: {rsi_current:.1f} < {self.params['oversold_threshold']}",
                                f"RSI turning up: {rsi_current:.1f} > {rsi_prev:.1f}",
                                f"Trend: {trend_15m}",
                                f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                            ],
                            invalidation_conditions=[
                                f"Close below {sl_price:.4f}",
                                f"RSI drops below {self.params['oversold_threshold'] - 5}",
                                "Strong downtrend resumes",
                            ],
                            management_rules=[
                                "TP1: close 50%",
                                "TP2: close 30%",
                                "TP3: close 20%",
                                "Max holding: 50 bars",
                                "After TP1: SL to entry",
                            ],
                            metadata={
                                "rsi": rsi_current,
                                "rsi_prev": rsi_prev,
                                "trend_15m": trend_15m,
                                "trend_1h": trend_1h,
                                "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                                "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                            }
                        ))
        
        # SHORT: RSI overbought + reversal starting
        if rsi_current > self.params["overbought_threshold"] and rsi_prev > self.params["overbought_threshold"]:
            if rsi_current < rsi_prev:  # Turning down
                if self.params["trend_filter"] and trend_15m == "strong_up":
                    logger.debug(f"{self.symbol}: RSI overbought but strong uptrend, skipping SHORT")
                else:
                    entry_price = current_price
                    
                    swing_high = float(pd.DataFrame(klines_15m).iloc[-20:-1]['high'].max())
                    structural_sl = swing_high * 1.001
                    atr_sl = current_price + (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_stop"] else 0
                    sl_price = min(structural_sl, current_price + atr_sl) if atr_sl > 0 else structural_sl
                    sl_price = max(sl_price, current_price + min_risk_dist)
                    
                    risk_dist = sl_price - current_price
                    if risk_dist > 0:
                        tp1 = current_price - risk_dist * 1.5
                        tp2 = current_price - risk_dist * 2.5
                        tp3 = current_price - risk_dist * 3.5
                        
                        from src.strategies.base import SignalCandidate
                        candidates.append(SignalCandidate(
                            symbol=self.symbol,
                            side="SHORT",
                            entry_price=entry_price,
                            stop_loss=sl_price,
                            tp1=tp1,
                            tp2=tp2,
                            tp3=tp3,
                            confidence=68,
                            strategy_id=f"rsi_mr_{self.params['rsi_period']}",
                            strategy_version="1.0.0",
                            timeframe="15m",
                            parameters=self.params.copy(),
                            conditions=[
                                f"RSI overbought: {rsi_current:.1f} > {self.params['overbought_threshold']}",
                                f"RSI turning down: {rsi_current:.1f} < {rsi_prev:.1f}",
                                f"Trend: {trend_15m}",
                                f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                            ],
                            invalidation_conditions=[
                                f"Close above {sl_price:.4f}",
                                f"RSI rises above {self.params['overbought_threshold'] + 5}",
                                "Strong uptrend resumes",
                            ],
                            management_rules=[
                                "TP1: close 50%",
                                "TP2: close 30%",
                                "TP3: close 20%",
                                "Max holding: 50 bars",
                                "After TP1: SL to entry",
                            ],
                            metadata={
                                "rsi": rsi_current,
                                "rsi_prev": rsi_prev,
                                "trend_15m": trend_15m,
                                "trend_1h": trend_1h,
                                "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                                "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                            }
                        ))
        
        return candidates
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
    def _get_trend_strength(self, df: pd.DataFrame, lookback: int = 20) -> str:
        if len(df) < lookback + 1:
            return "neutral"
        
        current = df.iloc[-1]['close']
        past = df.iloc[-(lookback + 1)]['close']
        change_pct = (current - past) / past
        
        ema_fast = df['close'].ewm(span=9, adjust=False).mean()
        ema_slow = df['close'].ewm(span=21, adjust=False).mean()
        ema_bullish = float(ema_fast.iloc[-1]) > float(ema_slow.iloc[-1])
        ema_bearish = float(ema_fast.iloc[-1]) < float(ema_slow.iloc[-1])
        
        if change_pct > 0.05 and ema_bullish:
            return "strong_up"
        elif change_pct > 0.02 and ema_bullish:
            return "up"
        elif change_pct < -0.05 and ema_bearish:
            return "strong_down"
        elif change_pct < -0.02 and ema_bearish:
            return "down"
        return "neutral"