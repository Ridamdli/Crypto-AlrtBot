"""
Momentum Filter Strategy

Long: Price has moved X% above rolling recent low AND trend conditions satisfied
Short: Price has moved X% below rolling recent high AND trend conditions satisfied

Includes ATR filter, volume filter, and trend confirmation.
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MomentumFilterStrategy(BaseStrategy):
    family = "momentum"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "lookback": 20,
        "threshold_pct": 0.02,  # 2% expansion
        "confirmation_bars": 1,
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.0,
        "volume_filter": True,
        "volume_ratio_min": 1.5,
        "trend_filter": True,
        "trend_lookback": 10,
    }
    
    param_schema = {
        "lookback": {"type": "int", "min": 5, "max": 50, "description": "Momentum lookback period"},
        "threshold_pct": {"type": "float", "min": 0.005, "max": 0.10, "description": "Minimum expansion percentage"},
        "confirmation_bars": {"type": "int", "min": 1, "max": 5, "description": "Confirmation bars required"},
        "atr_filter": {"type": "bool", "description": "Enable ATR-based stop loss"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0, "description": "ATR multiplier for stop"},
        "volume_filter": {"type": "bool", "description": "Require volume expansion"},
        "volume_ratio_min": {"type": "float", "min": 1.0, "max": 3.0, "description": "Minimum volume ratio"},
        "trend_filter": {"type": "bool", "description": "Require trend alignment"},
        "trend_lookback": {"type": "int", "min": 5, "max": 30, "description": "Trend lookback period"},
    }
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        """Calculate Average True Range."""
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        """Calculate current volume / rolling average volume."""
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
    def _get_trend(self, df: pd.DataFrame, lookback: int) -> str:
        """Determine trend direction over lookback period."""
        if len(df) < lookback + 1:
            return "neutral"
        current = df.iloc[-1]['close']
        past = df.iloc[-(lookback + 1)]['close']
        if current > past:
            return "up"
        elif current < past:
            return "down"
        return "neutral"
    
    def _get_recent_extremes(self, df: pd.DataFrame, lookback: int) -> tuple:
        """Get recent highest high and lowest low."""
        lookback = min(lookback, len(df) - 1)
        recent_df = df.iloc[-(lookback + 1):-1]  # Exclude current candle
        highest = float(recent_df['high'].max())
        lowest = float(recent_df['low'].min())
        return highest, lowest
    
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
        
        if len(klines_15m) < self.params["lookback"] + 2:
            return candidates
        
        df_15m = pd.DataFrame(klines_15m)
        df_1h = pd.DataFrame(klines_1h) if klines_1h else pd.DataFrame()
        
        for df in [df_15m, df_1h]:
            if not df.empty:
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
        
        # Get recent extremes
        highest, lowest = self._get_recent_extremes(df_15m, self.params["lookback"])
        
        # Trend direction
        trend_15m = self._get_trend(df_15m, self.params["trend_lookback"])
        trend_1h = self._get_trend(df_1h, self.params["trend_lookback"]) if not df_1h.empty else "neutral"
        
        # ATR
        atr_15m = self._calculate_atr(df_15m, self.params["atr_period"]) if self.params["atr_filter"] else 0
        
        # Volume
        volume_ratio = self._calculate_volume_ratio(df_15m)
        
        # Volume filter
        if self.params["volume_filter"] and volume_ratio < self.params["volume_ratio_min"]:
            return []
        
        # Trend filter
        if self.params["trend_filter"]:
            trend_aligned_long = trend_15m == "up" and trend_1h in ["up", "neutral"]
            trend_aligned_short = trend_15m == "down" and trend_1h in ["down", "neutral"]
        else:
            trend_aligned_long = True
            trend_aligned_short = True
        
        atr_stop_dist = self._calculate_atr(df_15m, self.params["atr_period"]) * self.params["atr_multiplier"] if self.params["atr_filter"] else 0
        min_risk_dist = self._calculate_atr(df_15m, self.params["atr_period"]) * 0.5 if self._calculate_atr(df_15m, self.params["atr_period"]) > 0 else current_price * 0.005
        
        candidates = []
        
        # LONG: Price expanded above recent low
        if trend_aligned_long:
            expansion_pct = (current_price - lowest) / lowest if lowest > 0 else 0
            if expansion_pct >= self.params["threshold_pct"]:
                entry_price = current_price
                
                # Structural SL: recent swing low
                structural_sl = min(df_15m.iloc[-(self.params["trend_lookback"]):]['low'].min(), lowest) * 0.999
                atr_sl = current_price - self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"] if self.params["atr_filter"] else 0
                sl_price = max(structural_sl, current_price - atr_sl) if atr_sl > 0 else structural_sl
                sl_price = min(sl_price, current_price - min_risk_dist)
                
                risk_dist = current_price - sl_price
                if risk_dist > 0:
                    tp1 = current_price + risk_dist * 2.0
                    tp2 = current_price + risk_dist * 3.0
                    tp3 = current_price + risk_dist * 4.0
                    
                    highest_lookback, _ = self._get_recent_extremes(pd.DataFrame(klines_15m), self.params["lookback"])
                    
                    candidates.append(SignalCandidate(
                        symbol=self.symbol,
                        side="LONG",
                        entry_price=current_price,
                        stop_loss=sl_price,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        confidence=70,
                        strategy_id=f"momentum_{self.params['lookback']}",
                        strategy_version="1.0.0",
                        timeframe="15m",
                        parameters=self.params.copy(),
                        conditions=[
                            f"Momentum expansion: {expansion_pct:.2%} above recent low",
                            f"Trend: {trend_15m} (15m), {trend_1h} (1h)",
                            f"Volume ratio: {volume_ratio:.2f}x",
                        ],
                        invalidation_conditions=[
                            f"Close below swing low ({sl_price:.4f})",
                            "Trend reverses",
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
                            "expansion_pct": expansion_pct,
                            "highest": highest,
                            "lowest": lowest,
                            "trend_15m": trend_15m,
                            "trend_1h": trend_1h,
                            "volume_ratio": volume_ratio,
                            "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        }
                    ))
        
        # SHORT: Price expanded below recent high
        if trend_aligned_short:
            expansion_pct = (highest - current_price) / highest if highest > 0 else 0
            if expansion_pct >= self.params["threshold_pct"]:
                entry_price = current_price
                
                structural_sl = max(pd.DataFrame(klines_15m).iloc[-(self.params["trend_lookback"]):]['high'].max(), highest) * 1.001
                atr_sl = current_price + self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"] if self.params["atr_filter"] else 0
                sl_price = min(structural_sl, current_price + atr_sl) if atr_sl > 0 else structural_sl
                sl_price = max(sl_price, current_price + min_risk_dist)
                
                risk_dist = sl_price - current_price
                if risk_dist > 0:
                    tp1 = current_price - risk_dist * 2.0
                    tp2 = current_price - risk_dist * 3.0
                    tp3 = current_price - risk_dist * 4.0
                    
                    _, lowest_lookback = self._get_recent_extremes(pd.DataFrame(klines_15m), self.params["lookback"])
                    
                    candidates.append(SignalCandidate(
                        symbol=self.symbol,
                        side="SHORT",
                        entry_price=current_price,
                        stop_loss=sl_price,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        confidence=70,
                        strategy_id=f"momentum_{self.params['lookback']}",
                        strategy_version="1.0.0",
                        timeframe="15m",
                        parameters=self.params.copy(),
                        conditions=[
                            f"Momentum expansion: {expansion_pct:.2%} below recent high",
                            f"Trend: {trend_15m} (15m), {trend_1h} (1h)",
                            f"Volume ratio: {volume_ratio:.2f}x",
                        ],
                        invalidation_conditions=[
                            f"Close above swing high ({sl_price:.4f})",
                            "Trend reverses",
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
                            "expansion_pct": expansion_pct,
                            "highest": highest,
                            "lowest": lowest,
                            "trend_15m": trend_15m,
                            "trend_1h": trend_1h,
                            "volume_ratio": volume_ratio,
                            "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        }
                    ))
        
        return candidates
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        """Calculate Average True Range."""
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0