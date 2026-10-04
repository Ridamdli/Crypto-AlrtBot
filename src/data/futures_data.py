from typing import Dict, Any, List
from src.data.binance_client import BinanceFuturesClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class FuturesDataService:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client

    def get_market_context(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches open interest, mark price, and funding rate to form a context snapshot.
        """
        try:
            oi_data = self.client.get_open_interest(symbol)
            mark_data = self.client.get_mark_price(symbol)
            
            # Fetch the latest funding rate
            funding_history = self.client.get_funding_rate(symbol)
            latest_funding = funding_history[-1] if funding_history else {}

            context = {
                "symbol": symbol,
                "open_interest": float(oi_data.get("openInterest", 0)),
                "mark_price": float(mark_data.get("markPrice", 0)),
                "index_price": float(mark_data.get("indexPrice", 0)),
                "last_funding_rate": float(mark_data.get("lastFundingRate", 0)),
                "next_funding_time": mark_data.get("nextFundingTime"),
            }
            logger.info(f"Fetched futures context for {symbol}")
            return context
        except Exception as e:
            logger.error(f"Error fetching market context for {symbol}: {e}")
            raise
