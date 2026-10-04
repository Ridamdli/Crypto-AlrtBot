from typing import List, Dict, Any
import pandas as pd
from src.utils.logger import get_logger

logger = get_logger(__name__)

class VolumeEngine:
    def __init__(self, rolling_window: int = 20, min_ratio: float = 1.5):
        self.rolling_window = rolling_window
        self.min_ratio = min_ratio

    def analyze_volume(self, klines_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculates volume expansion ratio based on 15m data.
        """
        if len(klines_15m) < self.rolling_window + 1:
            logger.warning("Not enough data to analyze volume.")
            return {"ratio": 0.0, "is_expanded": False}

        df = pd.DataFrame(klines_15m)
        df['volume'] = df['volume'].astype(float)
        
        # Calculate rolling average volume, excluding the very last incomplete candle
        # Assuming the last candle in klines_15m might be currently forming,
        # we can compare the last closed candle or the current forming one depending on logic.
        # For determinism, let's look at the last fully closed candle (index -2 if -1 is open, 
        # but since we fetch historical, we just use the last complete one).
        
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(self.rolling_window+1):-1]['volume'].mean()

        if rolling_avg == 0:
            ratio = 0.0
        else:
            ratio = current_vol / rolling_avg

        return {
            "current_volume": current_vol,
            "rolling_avg_volume": rolling_avg,
            "ratio": ratio,
            "is_expanded": ratio >= self.min_ratio
        }
