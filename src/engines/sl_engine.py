from typing import List, Dict, Any
import pandas as pd
from src.utils.logger import get_logger

logger = get_logger(__name__)

class StopLossEngine:
    def __init__(self, atr_period: int = 14, atr_multiplier: float = 1.5, structural_lookback: int = 20):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.structural_lookback = structural_lookback

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=self.atr_period).mean().iloc[-1]
        return float(atr)

    def calculate_sl(self, model: str, entry_price: float, side: str, klines: List[Dict[str, Any]]) -> float:
        """
        Calculate Stop-Loss price.
        models: "atr", "structural"
        """
        df = pd.DataFrame(klines)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)

        if model == "atr":
            atr = self._calculate_atr(df)
            if side == "LONG":
                return entry_price - (atr * self.atr_multiplier)
            else:
                return entry_price + (atr * self.atr_multiplier)

        elif model == "structural":
            # Structural: recent swing low/high over configurable lookback
            lookback = min(self.structural_lookback, len(df) - 1)
            recent_df = df.iloc[-lookback:]
            
            atr = self._calculate_atr(df)
            min_risk_dist = atr * 0.5  # Minimum risk distance = 0.5 ATR
            
            if side == "LONG":
                sl_candidate = float(recent_df['low'].min()) * 0.999  # slightly below swing low
                # Ensure SL is below entry with minimum risk distance
                max_sl = entry_price - min_risk_dist
                return min(sl_candidate, max_sl)
            else:
                sl_candidate = float(recent_df['high'].max()) * 1.001  # slightly above swing high
                # Ensure SL is above entry with minimum risk distance
                min_sl = entry_price + min_risk_dist
                return max(sl_candidate, min_sl)
        
        else:
            logger.error(f"Unknown SL model: {model}")
            return 0.0
