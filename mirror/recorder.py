"""Invo trading session: auto-refreshing trading JWT + record calls.

Holds the operator-exported trading refresh JWT (350-day). Access tokens
(~10 min) rotate silently; every record call retries once after a 401.
All record calls are best-effort by policy: an HL fill stands on its own —
a failed record alerts but never unwinds a position.
"""
import time
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

BASE = "https://api.invoapp.com"
APP_HEADERS = {"x-app-build-number": "84", "x-app-release": "1.1.0",
               "x-app-version": "0.0.86", "x-platform": "web"}


def decode_exp(token: str) -> Optional[int]:
    try:
        import base64 as _b64
        import json as _json
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(seg).decode())
        return int(payload.get("expires") or payload.get("exp") or 0)
    except (ValueError, IndexError, KeyError):
        return None


class TradingSession:
    def __init__(self, refresh_token: str = "", access_token: str = ""):
        self.refresh_token = refresh_token
        self.access_token = access_token

    def _headers(self) -> Dict[str, str]:
        return {"authorization": f"Bearer {self.access_token}",
                "content-type": "application/json", **APP_HEADERS}

    def refresh(self) -> bool:
        """Silent rotation via the trading refresh token. Never raises."""
        if not self.refresh_token:
            return False
        try:
            import urllib.request as _req
            import json as _json
            req = _req.Request(
                f"{BASE}/v1_0/auth/refresh_token", method="GET",
                headers={"authorization": f"Bearer {self.refresh_token}",
                         **APP_HEADERS})
            with _req.urlopen(req, timeout=20) as resp:
                if resp.status != 200:
                    return False
                data = _json.loads(resp.read().decode("utf-8"))
            if data.get("accessToken"):
                self.access_token = data["accessToken"]
                return True
            return False
        except Exception as e:
            logger.warning(f"[Recorder] refresh failed: {e}")
            return False

    def ensure(self) -> bool:
        """True when a usable access token is in hand."""
        exp = decode_exp(self.access_token) if self.access_token else 0
        if exp and exp - time.time() > 60:
            return True
        return self.refresh()

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST with one refresh-and-retry on 401. Raises on failure."""
        import urllib.request as _req
        import urllib.error as _err
        import json as _json
        if not self.ensure():
            raise RuntimeError("no trading session (refresh failed)")
        payload = _json.dumps(body).encode()
        for attempt in (0, 1):
            req = _req.Request(f"{BASE}{path}", data=payload,
                               headers=self._headers(), method="POST")
            try:
                with _req.urlopen(req, timeout=20) as resp:
                    return _json.loads(resp.read().decode("utf-8"))
            except _err.HTTPError as e:
                if e.code == 401 and attempt == 0 and self.refresh():
                    continue
                raise RuntimeError(f"{path} HTTP {e.code}: "
                                   f"{e.read()[:200].decode('utf-8', 'ignore')}")
        raise RuntimeError(f"{path}: retry exhausted")

    def record_open(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/dex/position/create", payload)

    def record_close(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/dex/position/close", payload)

    def trade_updates(self, investments: list) -> Dict[str, Any]:
        return self.post("/dex/trade", {"investments": investments})
