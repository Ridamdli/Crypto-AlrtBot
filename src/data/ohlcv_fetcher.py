from typing import List, Dict, Any, Optional
from src.data.binance_client import BinanceFuturesClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class OHLCVFetcher:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client

    def fetch_standardized_klines(self, symbol: str, interval: str, limit: int = 500, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches OHLCV data and returns it in a standardized list of dictionaries.
        Binance Kline layout:
        [
            0: Open time,
            1: Open,
            2: High,
            3: Low,
            4: Close,
            5: Volume,
            6: Close time,
            7: Quote asset volume,
            8: Number of trades,
            9: Taker buy base asset volume,
            10: Taker buy quote asset volume,
            11: Ignore.
        ]
        """
        raw_klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit, end_time=end_time)
        standardized_data = []
        for k in raw_klines:
            standardized_data.append({
                "timestamp": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_asset_volume": float(k[7]),
                "number_of_trades": int(k[8]),
                "taker_buy_base_asset_volume": float(k[9]),
                "taker_buy_quote_asset_volume": float(k[10])
            })
        
        logger.info(f"Fetched {len(standardized_data)} {interval} klines for {symbol}")
        return standardized_data
