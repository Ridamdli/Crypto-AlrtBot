"""Feed watcher: event-driven Pariston detection via the following feed.

Endpoint verified live with a plain session: POST /v1_0/posts/get_feed
{filter:'following', params:{page,size}} -> {items:[...]}. Each trade post
carries update{ticker, directionLong, leverage, entryPrice, priceTarget,
stopLoss, isOpen, closingPrice, baseId, baseShortId, verifiedTrade,
portfolio{id,...}, owner{id,username}} — everything a mimic needs,
including closes and the record payload's mimicMeta.

First poll indexes existing posts (no retro-mimics); only new posts
after that become signals. Update posts on an already-seen baseId with
changed TP/SL (or isOpen false) become update/close events.
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

FEED_URL = "https://api.invoapp.com/v1_0/posts/get_feed"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/154.0.0.0 Safari/537.36")


def classify_post(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A feed post -> mimic event or None (not a verified trade post)."""
    if not isinstance(post, dict):
        return None
    update = post.get("update")
    if not isinstance(update, dict) or not update.get("ticker"):
        return None
    if not update.get("verifiedTrade"):
        return None
    is_open = update.get("isOpen") is True
    is_closed = update.get("isOpen") is False and update.get("closingPrice") is not None
    changes = update.get("changes") or {}
    if is_closed:
        action = "close"
    elif changes.get("isAdded") is False and is_open:
        action = "increase"
    elif changes.get("isAdded") is not False:
        action = "open"
    else:
        action = "update"
    owner = update.get("owner") or post.get("owner") or {}
    portfolio = update.get("portfolio") or {}
    return {
        "action": action,
        "post_id": post.get("id"),
        "base_id": update.get("baseId"),
        "base_short_id": update.get("baseShortId"),
        "ticker": str(update.get("ticker") or "").upper(),
        "side": "LONG" if update.get("directionLong") else "SHORT",
        "leverage": update.get("leverage", 10),
        "entry": update.get("entryPrice"),
        "tp": update.get("priceTarget"),
        "sl": update.get("stopLoss"),
        "closing_price": update.get("closingPrice"),
        "is_open": is_open,
        "owner_id": owner.get("id"),
        "owner_username": owner.get("username"),
        "portfolio_id": portfolio.get("id"),
    }


def fetch_feed(token: str, page: int = 1, size: int = 20) -> List[Dict[str, Any]]:
    """Read-only: following feed page. Never writes."""
    import urllib.request as _req
    import json as _json
    payload = _json.dumps({"filter": "following",
                           "params": {"page": page, "size": size}}).encode()
    req = _req.Request(FEED_URL, data=payload,
                       headers={"Content-Type": "application/json",
                                "User-Agent": BROWSER_UA,
                                "authorization": f"Bearer {token}",
                                "x-app-version": "0.0.75",
                                "x-platform": "web"})
    with _req.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"feed HTTP {resp.status}")
        data = _json.loads(resp.read().decode("utf-8"))
    items = data.get("items")
    return items if isinstance(items, list) else []


class FeedWatcher:
    """New-post detector with first-poll indexing. Pure + injectable."""

    def __init__(self, fetch_fn: Callable[..., List[Dict[str, Any]]] = None):
        self._fetch = fetch_fn or fetch_feed
        self._seen_posts: set = set()
        self._indexed = False

    def poll(self, token: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Returns (new_events, update_events). First call only indexes."""
        posts = self._fetch(token)
        news, updates = [], []
        for post in posts:
            if not isinstance(post, dict):
                continue
            pid = post.get("id")
            if not pid or pid in self._seen_posts:
                continue
            self._seen_posts.add(pid)
            if not self._indexed:
                continue
            ev = classify_post(post)
            if ev is None:
                continue
            (news if ev["action"] in ("open", "increase") else updates).append(ev)
        self._indexed = True
        return news, updates
