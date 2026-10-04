import requests
import time
from typing import Dict, Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

class BinanceFuturesClient:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, retries: int = 3) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        for attempt in range(retries):
            try:
                response = self.session.request(method, url, params=params, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    logger.error(f"Max retries reached for {endpoint}")
                    raise
                time.sleep(1) # simple backoff

    def get_exchange_info(self) -> Dict[str, Any]:
        """Fetch exchange information (symbols, rules)."""
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def get_klines(self, symbol: str, interval: str, limit: int = 500, end_time: Optional[int] = None) -> list:
        """Fetch OHLCV data for a symbol."""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time:
            params["endTime"] = end_time
        return self._request("GET", "/fapi/v1/klines", params=params)
    
    def get_open_interest(self, symbol: str) -> Dict[str, Any]:
        """Fetch current open interest."""
        return self._request("GET", "/fapi/v1/openInterest", params={"symbol": symbol})
    
    def get_open_interest_hist(self, symbol: str, period: str = "15m", limit: int = 30) -> list:
        """Fetch historical open interest."""
        return self._request("GET", "/futures/data/openInterestHist", params={"symbol": symbol, "period": period, "limit": limit})

    
    def get_funding_rate(self, symbol: str) -> list:
        """Fetch funding rate history."""
        return self._request("GET", "/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 100})
    
    def get_mark_price(self, symbol: str) -> Dict[str, Any]:
        """Fetch mark price and current funding rate."""
        return self._request("GET", "/fapi/v1/premiumIndex", params={"symbol": symbol})

    def get_ticker_24h(self) -> List[Dict[str, Any]]:
        """Fetch 24hr ticker price change statistics for all symbols."""
        return self._request("GET", "/fapi/v1/ticker/24hr")

