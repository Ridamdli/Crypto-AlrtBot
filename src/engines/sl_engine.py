from typing import List, Dict, Any
import pandas as pd
from src.utils.logger import get_logger

logger = get_logger(__name__)

class StopLossEngine:
    def __init__(self, atr_period: int = 14, atr_multiplier: float = 1.5):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier

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
            # Simple structural: recent swing low/high over last 10 candles
            recent_df = df.iloc[-10:]
            if side == "LONG":
                return float(recent_df['low'].min()) * 0.999 # slightly below
            else:
                return float(recent_df['high'].max()) * 1.001 # slightly above
        
        else:
            logger.error(f"Unknown SL model: {model}")
            return 0.0
