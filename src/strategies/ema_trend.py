"""
EMA Trend / Pullback Strategy

Long: Fast EMA > Slow EMA, price pulls back toward fast EMA, then resumes
Short: Fast EMA < Slow EMA, price pulls back toward fast EMA, then resumes

Parameters: fast/slow EMA periods, pullback %, confirmation, ATR stop.
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EMATrendStrategy(BaseStrategy):
    family = "ema_trend"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "fast_period": 20,
        "slow_period": 50,
        "pullback_pct": 0.5,  # 0.5% pullback toward fast EMA
        "confirmation_bars": 1,
        "atr_stop": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "volume_filter": True,
        "volume_ratio_min": 1.3,
        "regime_filter": True,
    }
    
    param_schema = {
        "fast_period": {"type": "int", "min": 5, "max": 50, "description": "Fast EMA period"},
        "slow_period": {"type": "int", "min": 20, "max": 200, "description": "Slow EMA period"},
        "pullback_pct": {"type": "float", "min": 0.1, "max": 2.0, "description": "Pullback percentage toward fast EMA"},
        "confirmation_bars": {"type": "int", "min": 1, "max": 5, "description": "Confirmation bars"},
        "atr_stop": {"type": "bool", "description": "Use ATR-based stop loss"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0, "description": "ATR multiplier for stop"},
        "volume_filter": {"type": "bool", "description": "Require volume expansion"},
        "volume_ratio_min": {"type": "float", "min": 1.0, "max": 3.0, "description": "Minimum volume ratio"},
        "regime_filter": {"type": "bool", "description": "Filter by BTC regime"},
    }
    
    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()
    
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
    
    def _get_swing_extremes(self, df: pd.DataFrame, lookback: int) -> tuple:
        """Get recent swing high/low."""
        lookback = min(lookback, len(df) - 1)
        recent_df = df.iloc[-(lookback + 1):-1]
        return float(recent_df['high'].max()), float(recent_df['low'].min())
    
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
        
        if len(klines_15m) < self.params["slow_period"] + 2:
            return candidates
        
        df_15m = pd.DataFrame(klines_15m)
        df_1h = pd.DataFrame(klines_1h) if klines_1h else pd.DataFrame()
        
        for df in [df_15m, df_1h]:
            if not df.empty:
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
        
        # Calculate EMAs
        fast_ema = self._calculate_ema(df_15m['close'], self.params["fast_period"])
        slow_ema = self._calculate_ema(df_15m['close'], self.params["slow_period"])
        
        fast_ema_current = float(fast_ema.iloc[-1])
        slow_ema_current = float(slow_ema.iloc[-1])
        fast_ema_prev = float(fast_ema.iloc[-2])
        slow_ema_prev = float(slow_ema.iloc[-2])
        
        # Trend direction
        ema_bullish = fast_ema_current > slow_ema_current
        ema_bearish = fast_ema_current < slow_ema_current
        
        # Pullback detection
        pullback_dist = self.params["pullback_pct"] / 100.0
        
        # Volume
        volume_ratio = self._calculate_volume_ratio(df_15m)
        if self.params["volume_filter"] and volume_ratio < self.params["volume_ratio_min"]:
            return []
        
        # ATR
        atr_15m = self._calculate_atr(df_15m, self.params["atr_period"]) if self.params["atr_stop"] else 0
        min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
        
        # Regime filter
        if self.params["regime_filter"]:
            if ema_bullish and btc_regime == "bearish":
                return []
            if ema_bearish and btc_regime == "bullish":
                return []
        
        # LONG: Bullish EMA alignment, pullback to fast EMA
        if ema_bullish:
            # Check if price pulled back toward fast EMA
            pullback_to_fast = (current_price - fast_ema_current) / fast_ema_current
            if -pullback_dist <= pullback_to_fast <= 0:  # Price near fast EMA
                entry_price = current_price
                
                # SL: below recent swing low or fast EMA
                swing_high, swing_low = self._get_swing_extremes(df_15m, 20)
                structural_sl = swing_low * 0.999
                atr_sl = current_price - (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_stop"] else 0
                sl_price = max(structural_sl, current_price - atr_sl) if atr_sl > 0 else structural_sl
                sl_price = min(sl_price, current_price - min_risk_dist)
                
                risk_dist = current_price - sl_price
                if risk_dist > 0:
                    tp1 = current_price + risk_dist * 2.0
                    tp2 = current_price + risk_dist * 3.0
                    tp3 = current_price + risk_dist * 4.0
                    
                    from src.strategies.base import SignalCandidate
                    candidates.append(SignalCandidate(
                        symbol=self.symbol,
                        side="LONG",
                        entry_price=entry_price,
                        stop_loss=sl_price,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        confidence=72,
                        strategy_id=f"ema_trend_{self.params['fast_period']}_{self.params['slow_period']}",
                        strategy_version="1.0.0",
                        timeframe="15m",
                        parameters=self.params.copy(),
                        conditions=[
                            f"EMA bullish: fast({fast_ema_current:.2f}) > slow({slow_ema_current:.2f})",
                            f"Pullback to fast EMA: {pullback_dist:.2%}",
                            f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                        ],
                        invalidation_conditions=[
                            f"Close below {sl_price:.4f}",
                            "EMA crossover reverses",
                            "Volume dries up",
                        ],
                        management_rules=[
                            "TP1: close 50%",
                            "TP2: close 25%",
                            "TP3: close 25%",
                            "After TP1: SL to entry",
                            "After TP2: trailing SL to TP1",
                        ],
                        metadata={
                            "fast_ema": fast_ema_current,
                            "slow_ema": slow_ema_current,
                            "pullback_pct": pullback_dist,
                            "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                            "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        }
                    ))
        
        # SHORT: Bearish EMA alignment, pullback to fast EMA
        if ema_bearish:
            pullback_dist = self.params["pullback_pct"] / 100.0
            pullback_to_fast = (fast_ema_current - current_price) / fast_ema_current
            if -pullback_dist <= pullback_to_fast <= 0:  # Price near fast EMA
                entry_price = current_price
                
                swing_high, swing_low = self._get_swing_extremes(pd.DataFrame(klines_15m), 20)
                structural_sl = swing_high * 1.001
                atr_sl = current_price + (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_stop"] else 0
                sl_price = min(structural_sl, current_price + atr_sl) if atr_sl > 0 else structural_sl
                sl_price = max(sl_price, current_price + min_risk_dist)
                
                risk_dist = sl_price - current_price
                if risk_dist > 0:
                    tp1 = current_price - risk_dist * 2.0
                    tp2 = current_price - risk_dist * 3.0
                    tp3 = current_price - risk_dist * 4.0
                    
                    from src.strategies.base import SignalCandidate
                    candidates.append(SignalCandidate(
                        symbol=self.symbol,
                        side="SHORT",
                        entry_price=entry_price,
                        stop_loss=sl_price,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        confidence=72,
                        strategy_id=f"ema_trend_{self.params['fast_period']}_{self.params['slow_period']}",
                        strategy_version="1.0.0",
                        timeframe="15m",
                        parameters=self.params.copy(),
                        conditions=[
                            f"EMA bearish: fast({fast_ema_current:.2f}) < slow({slow_ema_current:.2f})",
                            f"Pullback to fast EMA: {pullback_dist:.2%}",
                            f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                        ],
                        invalidation_conditions=[
                            f"Close above {sl_price:.4f}",
                            "EMA crossover reverses",
                            "Volume dries up",
                        ],
                        management_rules=[
                            "TP1: close 50%",
                            "TP2: close 25%",
                            "TP3: close 25%",
                            "After TP1: SL to entry",
                            "After TP2: trailing SL to TP1",
                        ],
                        metadata={
                            "fast_ema": fast_ema_current,
                            "slow_ema": slow_ema_current,
                            "pullback_pct": pullback_dist,
                            "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                            "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        }
                    ))
        
        return candidates
    
    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()
    
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
    
    def _get_swing_extremes(self, df: pd.DataFrame, lookback: int) -> tuple:
        lookback = min(lookback, len(df) - 1)
        recent_df = df.iloc[-(lookback + 1):-1]
        return float(recent_df['high'].max()), float(recent_df['low'].min())