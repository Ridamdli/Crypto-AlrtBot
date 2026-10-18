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
               "x-app-version": "0.0.86", "x-platform": "web",
               "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/154.0.0.0 Safari/537.36")}


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
    def __init__(self, refresh_token: str = "", access_token: str = "",
                 state_path: str = ""):
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.state_path = state_path
        if state_path:
            self._load()

    def _load(self) -> None:
        try:
            import json as _json
            with open(self.state_path, encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, dict):
                self.refresh_token = data.get("refresh_token") or self.refresh_token
                self.access_token = data.get("access_token") or self.access_token
        except (FileNotFoundError, ValueError, OSError):
            pass

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            import os as _os
            from src.utils.atomic import atomic_write_json
            _os.makedirs(_os.path.dirname(self.state_path) or ".", exist_ok=True)
            atomic_write_json(self.state_path, {"refresh_token": self.refresh_token,
                                                "access_token": self.access_token},
                              indent=None)
            try:
                _os.chmod(self.state_path, 0o600)
            except OSError:
                pass
        except OSError as e:
            logger.warning(f"[Recorder] session persist failed: {e}")

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
                if data.get("refreshToken"):
                    # Server rotates: persist immediately, the old token dies.
                    self.refresh_token = data["refreshToken"]
                self._save()
                return True
            return False
        except Exception as e:
            logger.warning(f"[Recorder] refresh failed: {e}")
            return False

    def ensure(self) -> bool:
        """True when a usable access token is in hand. Lazily (re)loads
        persisted state first, so a session file dropped in later (or
        rotated by a sibling run) is picked up without restart."""
        if not self.access_token and not self.refresh_token:
            self._load()
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
