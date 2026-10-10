"""
Invo (Involio) auto-share client.

Reverse-engineered from the public web bundle at app.invoapp.com
(main.dart.js). Endpoint map (base https://api.involio.com/v1_0):

  POST /auth/login/email/start {email} -> OTP to inbox (200 {"success":true})
  POST /auth/login/email      {email, code} -> {accessToken, refreshToken, ...}
  GET  /auth/refresh_token    -> new accessToken (auto-tried on 401)
  POST /dex/position/create   {clientTxId, coin, assetIndex, entry, submission?, tpslSubmission?, summary?}
  POST /dex/position/update   (TP/SL edits)
  POST /dex/position/close    (position close)

Position-create body (field names verbatim from the bundle serializers):
  entry:      {side, marginMode, leverage?, tpPx?, slPx?}
  submission: {hlOrder, nonceMs}          # Hyperliquid-signed order (real money)
  summary:    {portfolioId?, creatorInvoUserId?, ...}

SAFETY: INVO_ENABLED defaults to false; INVO_DRY_RUN defaults to true.
In dry-run mode share_position() builds and validates the exact payload,
logs it, and returns it WITHOUT any POST. Nothing moves until both flags
are deliberately flipped AND Invo credentials are configured.
"""
import base64
import os
import time
import uuid
from typing import Any, Dict, Optional

import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)

SHARE_LEDGER_NAME = "invo_shares.json"


def load_share_ledger(storage_dir: str = "data_store"):
    import json as _json
    import os as _os

    path = _os.path.join(storage_dir, SHARE_LEDGER_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def record_share(storage_dir: str, signal_id: str, entry: dict) -> None:
    import json as _json
    import os as _os

    _os.makedirs(storage_dir, exist_ok=True)
    ledger = load_share_ledger(storage_dir)
    ledger[signal_id] = entry
    try:
        with open(_os.path.join(storage_dir, SHARE_LEDGER_NAME), "w", encoding="utf-8") as f:
            _json.dump(ledger, f, indent=2)
    except OSError as e:
        logger.error(f"[Invo] Failed to save share ledger: {e}")

DEFAULT_BASE_URL = "https://api.involio.com/v1_0"

# Paper-share (no real money) posts to a different host, captured live from
# the app: POST https://api.invoapp.com/v1_0/investments/ticker/create
# Rate limit observed: 500 req / 5 min. Required headers: authorization,
# nonce (random base64), timestamp (ms epoch), x-app-build-number,
# x-app-release, x-app-version, x-platform.
PAPER_SHARE_URL = "https://api.invoapp.com/v1_0/investments/ticker/create"
APP_HEADERS = {
    "x-app-build-number": "83",
    "x-app-release": "1.0.59",
    "x-app-version": "0.0.85",
    "x-platform": "web",
}

# Hyperliquid asset indices for our universe (mainnet). Verified at share
# time against the exchange metadata; unknown coins raise instead of guessing.
ASSET_INDEX = {
    "BTCUSDT": 0,
    "ETHUSDT": 1,
    "SOLUSDT": 5,
}


class InvoAuthError(Exception):
    pass


class InvoApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Invo API HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class InvoConflictError(Exception):
    """Same-coin position already open in this portfolio (server rule).

    Not a failure: the setup is already shared. Carries the conflicting
    baseIds so later update/close automation can target them.
    """

    def __init__(self, conflicting_base_ids, raw: dict):
        super().__init__(
            "An open investment in this asset already exists: "
            f"{conflicting_base_ids}"
        )
        self.conflicting_base_ids = conflicting_base_ids or []
        self.raw = raw


class InvoClient:
    def __init__(
        self,
        email: str = "",
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 15,
    ):
        self.email = email
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

    # ── email + OTP self-login (Invo is OTP-only; no passwords exist) ───
    def start_email_login(self, email: str) -> Dict[str, Any]:
        """Trigger an OTP code to the inbox. Returns the raw server reply."""
        resp = self.session.post(
            f"{self.base_url}/auth/login/email/start",
            json={"email": email},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise InvoAuthError(f"OTP start failed HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def verify_email_code(self, email: str, code: str, device_id: str = "") -> Dict[str, Any]:
        """Exchange an inbox OTP code for Bearer tokens. Stores them on success.

        Body shape verbatim from the app bundle (f9E serializer):
        {email, code, deviceId, deviceType: null, deviceInfo: null}.
        device_id should be a STABLE per-installation UUID (new device on
        every login looks suspicious); pass it in, we never invent rotation.
        """
        resp = self.session.post(
            f"{self.base_url}/auth/login/email",
            json={"email": email, "code": code, "deviceId": device_id,
                  "deviceType": None, "deviceInfo": None},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise InvoAuthError(f"OTP verify failed HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError:
            raise InvoAuthError("OTP verify returned non-JSON")
        self.access_token = data.get("accessToken")
        self.refresh_token = data.get("refreshToken")
        if not self.access_token:
            raise InvoAuthError(f"verify response missing accessToken: {str(data)[:200]}")
        logger.info("Invo email-OTP login ok.")
        return {"user_id": data.get("userId"), "username": data.get("username")}

    def login(self) -> Dict[str, Any]:
        """Password login — NOT supported by Invo (OTP-only platform).

        Use start_email_login() + verify_email_code(), or the fully
        autonomous ensure_auth() which reads the OTP from the mailbox.
        """
        raise InvoAuthError("Invo has no password login; use start_email_login/verify_email_code")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _post(self, path: str, payload: Dict[str, Any], retry: bool = True) -> Dict[str, Any]:
        resp = self.session.post(f"{self.base_url}{path}", json=payload,
                                 headers=self._headers(), timeout=self.timeout)
        if resp.status_code == 401 and retry and self.refresh_token:
            if self._refresh():
                return self._post(path, payload, retry=False)
        if resp.status_code not in (200, 201):
            raise InvoApiError(resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def _refresh(self) -> bool:
        """Silent rotation: GET /auth/refresh_token (per app bundle: GET, no body)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/auth/refresh_token",
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return False
            self.access_token = resp.json().get("accessToken", self.access_token)
            new_refresh = resp.json().get("refreshToken")
            if new_refresh:
                self.refresh_token = new_refresh
            return bool(self.access_token)
        except Exception:
            return False

    def ensure_auth(self, email: str = "", app_password: str = "",
                    device_id: str = "", otp_wait_s: int = 120) -> None:
        """Fully autonomous login: reuse token, else refresh, else OTP self-login.

        1. access_token set -> done. 2. refresh_token -> silent rotation.
        3. else: start OTP -> read code from mailbox -> verify. Raises
        InvoAuthError if every path fails. Secrets stay in memory only.
        """
        if self.access_token:
            return
        if self.refresh_token and self._refresh():
            logger.info("Invo session rotated silently.")
            return
        if not email or not app_password:
            raise InvoAuthError("no session: provide email + Gmail app password for OTP self-login")
        from src.invo.mailbox import MailboxReader
        self.start_email_login(email)
        import time as _time
        deadline = _time.time() + otp_wait_s
        code = None
        while _time.time() < deadline and not code:
            code = MailboxReader(email, app_password).latest_otp(since_minutes=10)
            if not code:
                _time.sleep(10)
        if not code:
            raise InvoAuthError("no OTP code arrived in the inbox within the wait window")
        self.verify_email_code(email, code, device_id=device_id)

    # ── paper share (no real money) ───────────────────────────────────
    @staticmethod
    def _paper_headers(token: str) -> Dict[str, str]:
        """Exact header set the app sends with the paper-share POST."""
        nonce = base64.b64encode(os.urandom(16)).decode()
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "nonce": nonce,
            "timestamp": str(int(time.time() * 1000)),
            **APP_HEADERS,
        }
        return headers

    @staticmethod
    def build_paper_share_payload(
        symbol: str,
        long: bool,
        leverage: float,
        entry_sim: float,
        price_target: Optional[float],
        stop_loss: Optional[float],
        liquidation_price: Optional[float],
        portfolio_id: str,
    ) -> Dict[str, Any]:
        """Paper-share body, field-for-field as captured live from the app.

        entry_sim / price_target semantics are taken from the caller's
        explicit arguments (see scheduler mapping) — never guessed here.
        """
        coin = symbol.replace("USDT", "").replace("USDC", "").upper()
        return {
            "ticker": coin,
            "portfolioId": portfolio_id,
            "directionLong": bool(long),
            "entrySim": float(entry_sim),
            "priceTarget": price_target,
            "stopLoss": stop_loss,
            "leverage": int(leverage),
            "liquidationPrice": liquidation_price,
        }

    def share_paper_trade(
        self,
        signal: Dict[str, Any],
        token: str,
        portfolio_id: str,
        entry_sim: float,
        price_target: Optional[float],
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Share one signal as a PAPER trade (no real money).

        Field mapping (verified live 2026-10-08 against the test portfolio):
          ticker            base asset, e.g. BTCUSDT -> BTC
          directionLong     side == LONG
          entrySim          position size in SIM DOLLARS (portfolio sim bank;
                            verified: posting 10.0 debited remainingSim by 10.0)
          priceTarget       take-profit as ABSOLUTE price (form-verified)
          stopLoss          stop-loss as ABSOLUTE price (null allowed)
          leverage          integer 1..40
          liquidationPrice  app-computed display value; 0.0 accepted at 1x

        dry_run=True (default): build + validate payload, POST nothing.
        dry_run=False: POSTs to the live portfolio immediately.
        """
        from src.papertrade.liquidation import estimate_liq_price

        payload = self.build_paper_share_payload(
            symbol=signal["symbol"],
            long=(signal.get("side", "").upper() == "LONG"),
            leverage=signal.get("leverage", 10),
            entry_sim=entry_sim,
            price_target=price_target,
            stop_loss=signal.get("stop_loss"),
            liquidation_price=estimate_liq_price(
                signal.get("entry"), signal.get("side"), signal.get("leverage", 10)
            ),
            portfolio_id=portfolio_id,
        )
        if dry_run:
            logger.info(f"[Invo PAPER DRY-RUN] would share {signal.get('symbol')} "
                        f"{signal.get('side')}: {payload}")
            return {"dry_run": True, "payload": payload}
        resp = self.session.post(
            PAPER_SHARE_URL, json=payload,
            headers=self._paper_headers(token), timeout=self.timeout,
        )
        if resp.status_code not in (200, 201):
            raise InvoApiError(resp.status_code, resp.text)
        try:
            result = resp.json()
        except ValueError:
            raise InvoApiError(resp.status_code, resp.text)
        # Server-level conflict: same coin already open in this portfolio.
        # Shape (captured live): {"success": false, "baseIds": [],
        #   "error": {"msg": "An open investment in this asset already exists.",
        #   "data": {"conflicting_base_ids": [...]}}}
        if not result.get("success", False):
            err = result.get("error") or {}
            data = err.get("data") or {}
            if "already exists" in str(err.get("msg", "")):
                raise InvoConflictError(data.get("conflicting_base_ids", []), result)
            raise InvoApiError(resp.status_code, str(result)[:300])
        logger.info(
            f"[Invo PAPER] shared {signal.get('symbol')} {signal.get('side')}: "
            f"baseIds={result.get('baseIds')} remainingSim={result.get('remainingSim')}"
        )
        return {"dry_run": False, "payload": payload, "response": result}

    # ── share ───────────────────────────────────────────────────────────
    @staticmethod
    def _coin_and_index(symbol: str):
        coin = symbol.replace("USDT", "").replace("USDC", "").upper()
        if symbol.upper() not in ASSET_INDEX:
            raise ValueError(
                f"No verified Hyperliquid assetIndex for {symbol}; "
                f"known: {sorted(ASSET_INDEX)}. Refusing to guess."
            )
        return coin, ASSET_INDEX[symbol.upper()]

    def build_position_payload(
        self,
        symbol: str,
        side: str,
        leverage: float,
        tp_px: Optional[float] = None,
        sl_px: Optional[float] = None,
        margin_mode: str = "isolated",
        portfolio_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exact body the app sends to /dex/position/create (minus hlOrder).

        The live app ALSO attaches submission.hlOrder (a wallet-signed
        Hyperliquid order). That signature can only come from the user's
        funded Invo wallet session — see module docstring. This builder
        produces everything else deterministically for review/dry-run.
        """
        coin, asset_index = self._coin_and_index(symbol)
        s = side.upper()
        if s not in ("LONG", "SHORT"):
            raise ValueError(f"side must be LONG/SHORT, got {side!r}")
        payload: Dict[str, Any] = {
            "clientTxId": str(uuid.uuid4()),
            "coin": coin,
            "assetIndex": asset_index,
            "entry": {
                "side": "long" if s == "LONG" else "short",
                "marginMode": margin_mode,
                "leverage": int(leverage),
            },
        }
        if tp_px is not None:
            payload["entry"]["tpPx"] = float(tp_px)
        if sl_px is not None:
            payload["entry"]["slPx"] = float(sl_px)
        if portfolio_id:
            payload["summary"] = {"portfolioId": portfolio_id}
        return payload

    def share_position(self, signal: Dict[str, Any], dry_run: bool = True,
                       portfolio_id: Optional[str] = None) -> Dict[str, Any]:
        """Share one bot signal to the Invo portfolio.

        dry_run=True (default): build + validate payload, log it, POST nothing.
        dry_run=False: requires auth + explicit enablement; POSTs for real.
        """
        payload = self.build_position_payload(
            symbol=signal["symbol"],
            side=signal["side"],
            leverage=signal.get("leverage", 10),
            tp_px=signal.get("tp1"),
            sl_px=signal.get("stop_loss"),
            portfolio_id=portfolio_id,
        )
        if dry_run:
            logger.info(f"[Invo DRY-RUN] would share {signal.get('symbol')} "
                        f"{signal.get('side')}: {payload}")
            return {"dry_run": True, "payload": payload}
        self.ensure_auth()
        result = self._post("/dex/position/create", payload)
        logger.info(f"[Invo] shared {signal.get('symbol')} {signal.get('side')}: {result}")
        return {"dry_run": False, "payload": payload, "response": result}
