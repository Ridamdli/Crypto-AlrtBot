"""Hyperliquid execution bridge: agent-key signing, no browser, no taps.

Model (proven by the reference build + our own live capture): the
operator exports the trade-only agent key once; the bot holds it and
signs IOC orders headlessly. The key cannot withdraw — worst case of a
compromise is unwanted trades, bounded by the brain caps + kill switch.

Live surface is intentionally narrow: meta cache, mid, position read,
set leverage, market open, close. Client is injectable so every cycle
runs offline in tests; the live adapter is a thin wrapper over
hyperliquid-python-sdk (verified at live proof, see shortcut note).
"""
import math
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

HL_API = "https://api.hyperliquid.xyz"
INVO_BUILDER = {"b": "0x557edb253b1d7ed5f15b248a5a3fd919fa5d3c81", "f": 35}
MIN_NOTIONAL_USD = 10.0  # HL order minimum; below it we skip, never force
DEFAULT_SLIPPAGE = 0.02


class HlBridge:
    def __init__(self, agent_key: str = "", wallet_address: str = "",
                 client_factory: Optional[Callable[[], Any]] = None):
        self.agent_key = agent_key
        self.wallet_address = wallet_address
        self._factory = client_factory or self._live_client
        self._client = None
        self._meta: Optional[Dict[str, Any]] = None
        self._meta_ts = 0.0

    def _live_client(self):
        # shortcut: adapter shape pinned against hyperliquid-python-sdk at
        # live proof (order-result envelope + builder kwarg). If the SDK
        # drifts, only this factory changes — callers are untouched.
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        from eth_account import Account
        wallet = Account.from_key(self.agent_key)
        return Exchange(wallet, constants.MAINNET_API_URL,
                        account_address=self.wallet_address)

    def _client_or_throw(self):
        if self._client is None:
            if not self.agent_key or not self.wallet_address:
                raise RuntimeError("HL bridge misconfigured: agent key/wallet missing")
            self._client = self._factory()
        return self._client

    # ── market data (public, keyless) ──
    @staticmethod
    def public_post(path: str, body: Dict[str, Any]) -> Any:
        import urllib.request as _req
        import json as _json
        req = _req.Request("https://api.hyperliquid.xyz" + path,
                           data=_json.dumps(body).encode(),
                           headers={"Content-Type": "application/json"})
        with _req.urlopen(req, timeout=20) as resp:
            return _json.loads(resp.read().decode("utf-8"))

    def meta(self) -> Dict[str, Any]:
        """Universe cache (60s): {coin: {assetIndex, szDecimals, maxLeverage}}."""
        if self._meta and time.time() - self._meta_ts < 60:
            return self._meta
        data = self.public_post("/info", {"type": "meta"})
        out = {}
        for i, a in enumerate(data.get("universe", [])):
            out[a["name"]] = {"assetIndex": i,
                              "szDecimals": int(a.get("szDecimals", 2)),
                              "maxLeverage": int(a.get("maxLeverage", 10))}
        self._meta, self._meta_ts = out, time.time()
        return out

    def mid(self, coin: str) -> float:
        mids = self.public_post("/info", {"type": "allMids"})
        px = float(mids.get(coin, 0) or 0)
        if px <= 0:
            raise RuntimeError(f"no mid for {coin}")
        return px

    def position(self, coin: str) -> Optional[Dict[str, Any]]:
        data = self.public_post("/info", {"type": "clearinghouseState",
                                          "user": self.wallet_address})
        for p in data.get("assetPositions", []):
            pos = p.get("position", {})
            if pos.get("coin") == coin and float(pos.get("szi", 0) or 0) != 0:
                return pos
        return None

    def equity(self) -> Optional[float]:
        """Live account value (None when wallet unset/unreadable). Keyless."""
        if not self.wallet_address:
            return None
        try:
            data = self.public_post("/info", {"type": "clearinghouseState",
                                              "user": self.wallet_address})
            return float((data.get("marginSummary") or {}).get("accountValue", 0) or 0)
        except Exception as e:
            logger.warning(f"[HL] equity unreadable: {e}")
            return None

    # ── execution (signed) ──
    def set_leverage(self, coin: str, leverage: int) -> Any:
        ex = self._client_or_throw()
        return ex.update_leverage(leverage, coin, False)

    def market_open(self, coin: str, side: str, margin_usd: float,
                    leverage: int, slippage: float = DEFAULT_SLIPPAGE) -> Dict[str, Any]:
        """Open (or add to) an isolated-margin position. Returns the fill
        record {coin, side, size, avg_px, oid, raw} or raises."""
        ex = self._client_or_throw()
        m = self.meta().get(coin)
        if m is None:
            raise RuntimeError(f"unknown coin {coin}")
        leverage = max(1, min(int(leverage), m["maxLeverage"]))
        self.set_leverage(coin, leverage)
        mid = self.mid(coin)
        notional = margin_usd * leverage
        if notional < MIN_NOTIONAL_USD:
            raise RuntimeError(f"notional ${notional:.2f} under HL $10 minimum")
        dec = m["szDecimals"]
        size = math.floor(notional / mid * 10 ** dec) / 10 ** dec
        if size <= 0:
            raise RuntimeError("size rounds to zero at szDecimals")
        is_buy = side.upper() == "LONG"
        raw = ex.market_open(coin, is_buy, size, None, slippage,
                             builder=INVO_BUILDER)
        fill = self._parse_fill(raw, coin)
        fill.update({"leverage": leverage, "margin_usd": margin_usd,
                     "mid_at_open": mid})
        logger.info(f"[HL] opened {coin} {side} {size} (~${notional:.2f}) "
                    f"{leverage}x avg={fill.get('avg_px')}")
        return fill

    def close(self, coin: str,
              slippage: float = DEFAULT_SLIPPAGE) -> Dict[str, Any]:
        """Full close of one coin. Raises when flat."""
        ex = self._client_or_throw()
        pos = self.position(coin)
        if pos is None:
            raise RuntimeError(f"no open position for {coin}")
        szi = float(pos["szi"])
        raw = ex.market_close(coin, None, None, slippage, None,
                              INVO_BUILDER)
        fill = self._parse_fill(raw, coin)
        logger.info(f"[HL] closed {coin} {szi}")
        return fill

    @staticmethod
    def _parse_fill(raw: Any, coin: str) -> Dict[str, Any]:
        """Normalize the SDK order envelope to a fill record. Raises when
        the response carries an error status instead of a fill."""
        try:
            data = raw.get("response", {}).get("data", {}) if isinstance(raw, dict) else {}
            statuses = data.get("statuses", [])
            if not statuses:
                raise RuntimeError("empty fill statuses")
            first = statuses[0] if statuses else {}
            if "error" in first:
                raise RuntimeError(str(first["error"])[:200])
            filled = first.get("filled", {})
            return {"coin": coin,
                    "size": float(filled.get("totalSz", 0) or 0),
                    "avg_px": filled.get("avgPx"),
                    "oid": filled.get("oid"),
                    "raw": raw}
        except (AttributeError, ValueError, TypeError, IndexError) as e:
            raise RuntimeError(f"unparseable fill envelope: {e}")

    @staticmethod
    def sane_tpsl(side: str, entry_px: float, tp: Any, sl: Any) -> tuple:
        """Validate TP/SL sit on the profit/loss sides. Returns floats or
        raises. Garbage in never becomes resting orders."""
        try:
            tp_f, sl_f = float(tp), float(sl)
        except (TypeError, ValueError):
            raise RuntimeError("tp/sl missing or non-numeric")
        if not all(x > 0 and x == x and abs(x) != float("inf")
                   for x in (tp_f, sl_f, entry_px)):
            raise RuntimeError("tp/sl/entry not finite positive")
        if side.upper() == "LONG":
            if not (sl_f < entry_px < tp_f):
                raise RuntimeError("LONG needs sl < entry < tp")
        else:
            if not (tp_f < entry_px < sl_f):
                raise RuntimeError("SHORT needs tp < entry < sl")
        return tp_f, sl_f

    def place_tpsl(self, coin: str, side: str, size: float,
                   tp_px: float, sl_px: float) -> Dict[str, Any]:
        """Resting reduce-only TP + SL triggers for the full size. Returns
        {tp_oid, sl_oid, tp_cloid, sl_cloid}. Raises on failure."""
        import secrets as _secrets
        ex = self._client_or_throw()
        try:
            from hyperliquid.utils.types import Cloid as _Cloid
        except ImportError:
            _Cloid = None
        is_buy = side.upper() == "SHORT"  # triggers close against the position
        out = {}
        for kind, px in (("tp", float(tp_px)), ("sl", float(sl_px))):
            hex_id = "0x" + _secrets.token_hex(16)
            cloid = _Cloid(hex_id) if _Cloid is not None else hex_id
            raw = ex.order(coin, is_buy, float(size), float(px),
                           {"trigger": {"triggerPx": float(px), "isMarket": True,
                                        "tpsl": kind}},
                           True, cloid, INVO_BUILDER)
            data = raw.get("response", {}).get("data", {}) \
                if isinstance(raw, dict) else {}
            statuses = data.get("statuses", [])
            resting = (statuses[0].get("resting", {}) if statuses else {})
            oid = resting.get("oid")
            if oid is None:
                raise RuntimeError(f"{kind} trigger not resting: "
                                   f"{str(raw)[:160]}")
            out[f"{kind}_oid"], out[f"{kind}_cloid"] = oid, hex_id
        logger.info(f"[HL] tpsl resting {coin} {side} tp={tp_px} sl={sl_px}")
        return out

    def cancel_tpsl(self, coin: str, cloids: List[str]) -> int:
        """Best-effort cancel of resting triggers by cloid. Returns count."""
        ex = self._client_or_throw()
        try:
            from hyperliquid.utils.types import Cloid as _Cloid
        except ImportError:
            _Cloid = None
        n = 0
        for cl in cloids or []:
            try:
                ex.cancel_by_cloid(
                    coin, _Cloid(cl) if _Cloid is not None else cl)
                n += 1
            except Exception as e:
                logger.warning(f"[HL] cancel {coin} {cl[:10]}… failed: {e}")
        return n
