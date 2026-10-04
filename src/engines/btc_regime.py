from typing import List, Dict, Any
import pandas as pd
from src.utils.logger import get_logger

logger = get_logger(__name__)

class BTCRegimeEngine:
    def __init__(self, ema_fast: int = 20, ema_slow: int = 50):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def evaluate_regime(self, klines_4h: List[Dict[str, Any]]) -> str:
        """
        Evaluate market regime based on BTC 4H data using simple EMAs.
        Returns: "bullish", "bearish", or "neutral".
        """
        if len(klines_4h) < self.ema_slow:
            logger.warning("Not enough data to evaluate BTC regime.")
            return "neutral"

        # Convert to pandas DataFrame for easy calculation
        df = pd.DataFrame(klines_4h)
        df['close'] = df['close'].astype(float)

        # Calculate EMAs
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()

        last_row = df.iloc[-1]
        
        # Basic condition: Fast EMA > Slow EMA -> Bullish
        if last_row['ema_fast'] > last_row['ema_slow']:
            # Could add price > EMA for stronger confirmation
            if last_row['close'] > last_row['ema_fast']:
                return "bullish"
            return "neutral"
        else:
            if last_row['close'] < last_row['ema_fast']:
                return "bearish"
            return "neutral"
