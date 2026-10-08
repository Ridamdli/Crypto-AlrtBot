from typing import Dict, List, Any, Optional
import concurrent.futures
from src.data.ohlcv_fetcher import OHLCVFetcher
from src.utils.logger import get_logger

logger = get_logger(__name__)

class TimeframeManager:
    def __init__(self, fetcher: OHLCVFetcher, timeframes: List[str] = None):
        self.fetcher = fetcher
        self.timeframes = timeframes or ["15m", "1h", "4h"]

    def fetch_multi_timeframe(self, symbol: str, limit: int = 200, end_time: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Synchronously fetch multi-timeframe OHLCV data.
        Returns a dictionary keyed by timeframe.
        """
        results = {}
        logger.info(f"Fetching multi-timeframe data for {symbol} ({', '.join(self.timeframes)})")
        
        def fetch_tf(tf):
            return tf, self.fetcher.fetch_standardized_klines(symbol, tf, limit, end_time=end_time)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.timeframes)) as executor:
            future_to_tf = {executor.submit(fetch_tf, tf): tf for tf in self.timeframes}
            for future in concurrent.futures.as_completed(future_to_tf):
                tf = future_to_tf[future]
                try:
                    res_tf, data = future.result()
                    results[res_tf] = data
                except Exception as exc:
                    logger.error(f"{symbol} timeframe {tf} generated an exception: {exc}")
                    
        return results
