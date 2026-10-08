"""
Volume-Confirmed Breakout Strategy

Long: Price breaks structural resistance + volume exceeds rolling threshold
Short: Price breaks structural support + volume confirmation

Particularly important for: ADA, AVAX, LINK, DOT, XRP, SOL, DOGE
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VolumeBreakoutStrategy(BaseStrategy):
    family = "volume_breakout"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "structural_lookback": 20,
        "volume_lookback": 20,
        "volume_multiplier": 2.0,
        "breakout_buffer": 0.001,
        "atr_filter": True,
        "atr_period": 14,
        "atr_multiplier": 1.5,
        "regime_filter": True,
    }
    
    param_schema = {
        "structural_lookback": {"type": "int", "min": 10, "max": 50, "description": "Structural levels lookback"},
        "volume_lookback": {"type": "int", "min": 10, "max": 50, "description": "Volume rolling window"},
        "volume_multiplier": {"type": "float", "min": 1.0, "max": 5.0, "description": "Volume expansion threshold"},
        "breakout_buffer": {"type": "float", "min": 0, "max": 0.01, "description": "Breakout buffer percentage"},
        "atr_filter": {"type": "bool", "description": "Use ATR-based stop loss"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 0.5, "max": 3.0, "description": "ATR multiplier for stop"},
        "regime_filter": {"type": "bool", "description": "Filter by BTC regime"},
    }
    
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
    
    def _get_structural_levels(self, df: pd.DataFrame, lookback: int) -> tuple:
        """Get structural resistance and support levels."""
        lookback = min(lookback, len(df) - 1)
        recent_df = df.iloc[-(lookback + 1):-1]  # Exclude current candle
        resistance = float(recent_df['high'].max())
        support = float(recent_df['low'].min())
        return resistance, support
    
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
        
        if len(klines_15m) < self.params["structural_lookback"] + 2:
            return candidates
        
        df_15m = pd.DataFrame(klines_15m)
        if not df_15m.empty:
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df_15m[col] = df_15m[col].astype(float)
        
        # Structural levels
        resistance, support = self._get_structural_levels(df_15m, self.params["structural_lookback"])
        
        # Volume
        volume_ratio = self._calculate_volume_ratio(df_15m, self.params["volume_lookback"])
        
        # ATR
        atr_15m = self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"])
        min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
        
        # Regime filter (handled by pipeline)
        
        buffer = self.params["breakout_buffer"]
        
        candidates = []
        
        # LONG: Breakout above resistance with volume
        if current_price > resistance * (1 + buffer) and volume_ratio >= self.params["volume_multiplier"]:
            entry_price = current_price
            
            # SL: below support or ATR-based
            structural_sl = support * 0.999
            atr_sl = current_price - (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_filter"] else 0
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
                    confidence=75,
                    strategy_id=f"volume_breakout_{self.params['structural_lookback']}",
                    strategy_version="1.0.0",
                    timeframe="15m",
                    parameters=self.params.copy(),
                    conditions=[
                        f"Volume breakout: close > resistance ({resistance:.4f})",
                        f"Volume ratio: {volume_ratio:.2f}x (threshold: {self.params['volume_multiplier']}x)",
                        f"ATR: {self._calculate_atr(pd.DataFrame(klines_15m), self.params['atr_period']):.4f}",
                    ],
                    invalidation_conditions=[
                        f"Close below support ({support:.4f})",
                        "Volume dries up",
                        "BTC regime reverses",
                    ],
                    management_rules=[
                        "TP1: close 50%",
                        "TP2: close 25%",
                        "TP3: close 25%",
                        "After TP1: SL to entry",
                        "After TP2: trailing SL to TP1",
                    ],
                    metadata={
                        "resistance": resistance,
                        "support": support,
                        "volume_ratio": volume_ratio,
                        "volume_multiplier": self.params["volume_multiplier"],
                        "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        "breakout_buffer": buffer,
                    }
                ))
        
        # SHORT: Breakdown below support with volume
        if current_price < support * (1 - buffer) and volume_ratio >= self.params["volume_multiplier"]:
            entry_price = current_price
            
            structural_sl = resistance * 1.001
            atr_sl = current_price + (self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]) * self.params["atr_multiplier"]) if self.params["atr_filter"] else 0
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
                    confidence=75,
                    strategy_id=f"volume_breakout_{self.params['structural_lookback']}",
                    strategy_version="1.0.0",
                    timeframe="15m",
                    parameters=self.params.copy(),
                    conditions=[
                        f"Volume breakout: close < support ({support:.4f})",
                        f"Volume ratio: {volume_ratio:.2f}x (threshold: {self.params['volume_multiplier']}x)",
                        f"ATR: {self._calculate_atr(pd.DataFrame(klines_15m), self.params['atr_period']):.4f}",
                    ],
                    invalidation_conditions=[
                        f"Close above resistance ({resistance:.4f})",
                        "Volume dries up",
                        "BTC regime reverses",
                    ],
                    management_rules=[
                        "TP1: close 50%",
                        "TP2: close 25%",
                        "TP3: close 25%",
                        "After TP1: SL to entry",
                        "After TP2: trailing SL to TP1",
                    ],
                    metadata={
                        "resistance": resistance,
                        "support": support,
                        "volume_ratio": volume_ratio,
                        "volume_multiplier": self.params["volume_multiplier"],
                        "atr": self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"]),
                        "breakout_buffer": buffer,
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