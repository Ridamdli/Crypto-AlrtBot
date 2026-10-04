from typing import List, Dict, Any
from src.data.binance_client import BinanceFuturesClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class UniverseFilter:
    def __init__(self, client: BinanceFuturesClient, min_volume_usdt: float = 10_000_000):
        self.client = client
        self.min_volume_usdt = min_volume_usdt

    def get_eligible_symbols(self) -> List[str]:
        """
        Dynamically fetch symbols and exclude based on state and volume thresholds.
        """
        logger.info("Fetching exchange info to build market universe...")
        exchange_info = self.client.get_exchange_info()
        
        # Filter for TRADING USDT-margined perpetuals
        eligible_symbols = set()
        for symbol_info in exchange_info.get("symbols", []):
            if (symbol_info.get("contractType") == "PERPETUAL" and
                symbol_info.get("quoteAsset") == "USDT" and
                symbol_info.get("status") == "TRADING"):
                eligible_symbols.add(symbol_info["symbol"])

        logger.info(f"Found {len(eligible_symbols)} TRADING USDT perpetuals. Filtering by volume...")
        
        # Filter by 24h volume
        tickers = self.client.get_ticker_24h()
        filtered_symbols = []
        for ticker in tickers:
            symbol = ticker["symbol"]
            if symbol in eligible_symbols:
                quote_volume = float(ticker.get("quoteVolume", 0))
                if quote_volume >= self.min_volume_usdt:
                    filtered_symbols.append(symbol)

        logger.info(f"Final universe size: {len(filtered_symbols)} symbols (Volume >= {self.min_volume_usdt} USDT)")
        return filtered_symbols
