"""
Donchian Channel Breakout Strategy

Long:  close > previous N-period highest high
Short: close < previous N-period lowest low

Uses previous-period channel values to avoid look-ahead bias.
Includes ATR filter, volume confirmation, and regime filter.
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DonchianBreakoutStrategy(BaseStrategy):
    family = "donchian"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "lookback": 20,
        "breakout_buffer": 0.001,  # 0.1% buffer
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "volume_confirmation": True,
        "volume_ratio_min": 1.5,
        "regime_filter": True,
    }
    
    param_schema = {
        "lookback": {"type": "int", "min": 10, "max": 50, "description": "Channel lookback period"},
        "breakout_buffer": {"type": "float", "min": 0, "max": 0.01, "description": "Buffer above/below channel"},
        "atr_filter": {"type": "bool", "description": "Enable ATR-based stop loss filter"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0, "description": "ATR multiplier for stop"},
        "volume_confirmation": {"type": "bool", "description": "Require volume expansion"},
        "volume_ratio_min": {"type": "float", "min": 1.0, "max": 3.0, "description": "Minimum volume ratio"},
        "regime_filter": {"type": "bool", "description": "Filter by BTC regime"},
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
    
    def _calculate_donchian_channels(self, df: pd.DataFrame, lookback: int) -> tuple:
        """
        Calculate Donchian channels using previous-period values.
        Returns (upper_channel, lower_channel) for the previous completed period.
        """
        # Use previous completed candles (exclude current forming candle)
        lookback = min(lookback, len(df) - 1)
        recent_df = df.iloc[-(lookback + 1):-1]  # Exclude current candle
        
        upper = float(recent_df['high'].max())
        lower = float(recent_df['low'].min())
        
        return upper, lower
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        """Calculate current volume / rolling average volume."""
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
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
        df_4h = pd.DataFrame(klines_4h) if klines_4h else pd.DataFrame()
        
        # Convert to float
        for df in [df_15m, df_1h, df_4h]:
            if not df.empty:
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
        
        # Calculate Donchian channels on 15m (entry timeframe)
        upper_15m, lower_15m = self._calculate_donchian_channels(df_15m, self.params["lookback"])
        
        # Calculate ATR
        atr_15m = self._calculate_atr(df_15m, self.params["atr_period"])
        
        # Volume ratio
        volume_ratio = self._calculate_volume_ratio(df_15m)
        
        # Regime filter
        if self.params["regime_filter"]:
            # For LONG, prefer bullish/neutral regime
            # For SHORT, prefer bearish/neutral regime
            pass  # Handled by pipeline
        
        # Volume confirmation
        if self.params["volume_confirmation"] and volume_ratio < self.params["volume_ratio_min"]:
            return []
        
        # ATR for stop loss
        atr_stop_dist = atr_15m * self.params["atr_multiplier"] if self.params["atr_filter"] else 0
        
        buffer = self.params["breakout_buffer"]
        
        # LONG setup: price breaks above upper channel
        if current_price > upper_15m * (1 + buffer):
            # Calculate entry (slight pullback or market)
            entry_price = current_price
            
            # Stop loss: max of structural (lower channel) and ATR-based
            structural_sl = lower_15m * 0.999
            atr_sl = current_price - atr_stop_dist if atr_stop_dist > 0 else 0
            sl_price = max(structural_sl, atr_sl) if atr_stop_dist > 0 else structural_sl
            
            # Ensure SL is below entry with minimum distance
            min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
            sl_price = min(sl_price, current_price - min_risk_dist)
            
            # Risk validation
            risk_dist = current_price - sl_price
            if risk_dist <= 0:
                return []
            
            # TP levels based on R-multiples
            tp1 = current_price + risk_dist * 2.0
            tp2 = current_price + risk_dist * 3.0
            tp3 = current_price + risk_dist * 4.0
            
            candidate = SignalCandidate(
                symbol=self.symbol,
                side="LONG",
                entry_price=entry_price,
                stop_loss=sl_price,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                confidence=75,  # Will be scored by scoring engine
                strategy_id=f"donchian_{self.params['lookback']}",
                strategy_version="1.0.0",
                timeframe="15m",
                parameters=self.params.copy(),
                conditions=[
                    f"Donchian breakout: close > upper channel ({upper_15m:.4f})",
                    f"Volume ratio: {volume_ratio:.2f}x",
                    f"ATR: {atr_15m:.4f}",
                ],
                invalidation_conditions=[
                    f"Close below lower channel ({lower_15m:.4f})",
                    "BTC regime reverses",
                    "Volume confirmation fails",
                ],
                management_rules=[
                    "TP1: close 50%",
                    "TP2: close 25%",
                    "TP3: close 25%",
                    "After TP1: SL to entry",
                    "After TP2: SL to TP1",
                ],
                metadata={
                    "upper_channel": upper_15m,
                    "lower_channel": lower_15m,
                    "atr": atr_15m,
                    "volume_ratio": volume_ratio,
                    "breakout_buffer": buffer,
                }
            )
            return [candidate]
        
        # SHORT setup: price breaks below lower channel
        if current_price < lower_15m * (1 - buffer):
            entry_price = current_price
            
            # Stop loss: min of structural (upper channel) and ATR-based
            structural_sl = upper_15m * 1.001
            atr_sl = current_price + atr_stop_dist if atr_stop_dist > 0 else 0
            sl_price = min(structural_sl, atr_sl) if atr_stop_dist > 0 else structural_sl
            
            min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
            sl_price = max(sl_price, current_price + min_risk_dist)
            
            risk_dist = sl_price - current_price
            if risk_dist <= 0:
                return []
            
            tp1 = current_price - risk_dist * 2.0
            tp2 = current_price - risk_dist * 3.0
            tp3 = current_price - risk_dist * 4.0
            
            candidate = SignalCandidate(
                symbol=self.symbol,
                side="SHORT",
                entry_price=entry_price,
                stop_loss=sl_price,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                confidence=75,
                strategy_id=f"donchian_{self.params['lookback']}",
                strategy_version="1.0.0",
                timeframe="15m",
                parameters=self.params.copy(),
                conditions=[
                    f"Donchian breakout: close < lower channel ({lower_15m:.4f})",
                    f"Volume ratio: {volume_ratio:.2f}x",
                    f"ATR: {atr_15m:.4f}",
                ],
                invalidation_conditions=[
                    f"Close above upper channel ({upper_15m:.4f})",
                    "BTC regime reverses",
                    "Volume confirmation fails",
                ],
                management_rules=[
                    "TP1: close 50%",
                    "TP2: close 25%",
                    "TP3: close 25%",
                    "After TP1: SL to entry",
                    "After TP2: SL to TP1",
                ],
                metadata={
                    "upper_channel": upper_15m,
                    "lower_channel": lower_15m,
                    "atr": atr_15m,
                    "volume_ratio": volume_ratio,
                    "breakout_buffer": buffer,
                }
            )
            return [candidate]
        
        return []