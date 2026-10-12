"""Mimic preflight: 10-point readiness check. Read-only, never trades.

Run:  python scripts/mimic_preflight.py
Env:  HL_AGENT_KEY, HL_WALLET_ADDRESS, INVO_TRADING_REFRESH (optional),
      MIMIC_* caps, MIRROR_* login (feed check).
"""
import json
import sys
import time

sys.path.insert(0, ".")


def check(name, fn):
    try:
        detail = fn()
        return {"name": name, "status": "ok", "detail": detail}
    except Exception as e:
        return {"name": name, "status": "fail", "detail": str(e)[:160]}


def main():
    import os
    from mirror.hl import HlBridge
    from mirror.recorder import TradingSession, decode_exp

    checks = []
    agent_key = os.getenv("HL_AGENT_KEY", "")
    wallet = os.getenv("HL_WALLET_ADDRESS", "")
    trading_refresh = os.getenv("INVO_TRADING_REFRESH", "")

    checks.append(check("env_agent_key", lambda: (
        f"set ({agent_key[:6]}...)" if agent_key else (_ for _ in ()).throw(
            RuntimeError("missing HL_AGENT_KEY")))))
    checks.append(check("env_wallet", lambda: (
        f"set ({wallet[:8]}...)" if wallet else (_ for _ in ()).throw(
            RuntimeError("missing HL_WALLET_ADDRESS")))))
    checks.append(check("env_trading_refresh", lambda: (
        "set (recording enabled)" if trading_refresh else (_ for _ in ()).throw(
            RuntimeError("missing INVO_TRADING_REFRESH (records disabled)")))))

    def _hl_balance():
        if not wallet:
            raise RuntimeError("no wallet")
        data = HlBridge.public_post("/info", {"type": "clearinghouseState",
                                              "user": wallet})
        eq = float((data.get("marginSummary") or {}).get("accountValue", 0) or 0)
        n = sum(1 for p in data.get("assetPositions", [])
                if float((p.get("position") or {}).get("szi", 0) or 0) != 0)
        if eq < 5:
            raise RuntimeError(f"equity ${eq:.2f} too low (min ~$10)")
        return f"equity ${eq:.2f}, {n} open"
    checks.append(check("hl_balance", _hl_balance))

    def _hl_meta():
        data = HlBridge.public_post("/info", {"type": "meta"})
        n = len(data.get("universe", []))
        mids = HlBridge.public_post("/info", {"type": "allMids"})
        if n == 0 or not mids.get("BTC"):
            raise RuntimeError("meta/mids unreachable")
        return f"{n} assets, BTC ${float(mids['BTC']):.0f}"
    checks.append(check("hl_market_data", _hl_meta))

    def _hl_signer():
        if not agent_key or not wallet:
            raise RuntimeError("no creds")
        from eth_account import Account
        acct = Account.from_key(agent_key)
        return f"agent {acct.address[:10]}... loads"
    checks.append(check("hl_agent_key_loads", _hl_signer))

    def _trading_session():
        if not trading_refresh:
            raise RuntimeError("no refresh token")
        s = TradingSession(refresh_token=trading_refresh)
        if not s.refresh():
            raise RuntimeError("refresh rejected")
        exp = decode_exp(s.access_token) or 0
        left = int(exp - time.time())
        if left <= 0:
            raise RuntimeError("refreshed token already expired")
        return f"trading JWT valid {left // 60}min"
    checks.append(check("trading_session", _trading_session))

    def _feed():
        from src.invo.client import InvoClient
        from mirror.feedmon import fetch_feed
        email = os.getenv("MIRROR_INVO_EMAIL", "")
        pw = os.getenv("MIRROR_INVO_GMAIL_APP_PASSWORD", "")
        dev = os.getenv("MIRROR_INVO_DEVICE_ID", "")
        data_dir = os.getenv("MIRROR_DATA_DIR", "mirror_data")
        if not (email and pw):
            raise RuntimeError("no feed login (set MIRROR_INVO_*)")
        c = InvoClient(email=email, storage_dir=data_dir)
        c.ensure_auth(email=email, app_password=pw, device_id=dev)
        posts = fetch_feed(c.access_token or "", size=5)
        return f"feed OK, {len(posts)} posts on page 1"
    checks.append(check("feed_login", _feed))

    ok = sum(1 for c in checks if c["status"] == "ok")
    fail = [c for c in checks if c["status"] != "ok"]
    print(json.dumps({"summary": {"ok": ok, "fail": len(fail),
                                  "ready": not fail},
                      "checks": checks}, indent=1))
    sys.exit(0 if not fail else 1)


if __name__ == "__main__":
    main()
