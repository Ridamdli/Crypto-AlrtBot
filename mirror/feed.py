"""Pariston watch: read the source portfolio + full setup detail. Read-only.

Two calls, both verified live cross-account:
- list_paper_investments(pariston_id) -> open positions (baseId, ticker,
  side, leverage, entry). No TP/SL at this level.
- fetch_position_history(base_id) -> newest history item with priceTarget,
  stopLoss, entryPrice, leverage, directionLong, isOpen.

normalize_position / to_share_setup are pure (unit-tested); the fetchers
are thin wrappers over InvoClient so cycles stay offline-testable.
"""
from typing import Any, Dict, List

PARISTON_PORTFOLIO_ID = "17cc19aa-d854-4857-9cca-7f05f21ec2da"


def normalize_position(inv: Dict[str, Any]) -> Dict[str, Any]:
    """Slim sims-level row -> canonical watch record."""
    long = bool(inv.get("directionLong", False))
    return {
        "baseId": str(inv.get("baseId") or ""),
        "ticker": str(inv.get("ticker") or "").upper(),
        "side": "LONG" if long else "SHORT",
        "leverage": inv.get("leverage", 10),
        "entry": inv.get("entryPrice"),
        "entrySim": inv.get("entrySim"),
    }


def to_share_setup(detail: Dict[str, Any]) -> Dict[str, Any]:
    """History detail -> paper-share-ready setup (symbol/side/lv/entry/TP/SL)."""
    ticker = str(detail.get("ticker") or "").upper()
    symbol = ticker if ticker.endswith("USDT") else (ticker + "USDT" if ticker else "")
    long = bool(detail.get("directionLong", False))
    return {
        "symbol": symbol,
        "side": "LONG" if long else "SHORT",
        "leverage": detail.get("leverage", 10),
        "entry": detail.get("entryPrice"),
        "stop_loss": detail.get("stopLoss"),
        "tp": detail.get("priceTarget"),
    }


def fetch_open_positions(client, token: str,
                         portfolio_id: str) -> List[Dict[str, Any]]:
    snap = client.list_paper_investments(token, portfolio_id)
    invs = snap.get("investments") if isinstance(snap, dict) else None
    if not isinstance(invs, list):
        return []
    out = []
    for inv in invs:
        if not isinstance(inv, dict) or not inv.get("baseId"):
            continue
        out.append(normalize_position(inv))
    return out


def fetch_setup_detail(client, token: str, base_id: str) -> Dict[str, Any]:
    detail = client.fetch_position_history(base_id, token)
    if not isinstance(detail, dict) or not detail.get("ticker"):
        return {}
    return to_share_setup(detail)
