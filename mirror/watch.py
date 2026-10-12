"""Detect-only watcher: the safe half of the true-mimic follower.

Watches Pariston's portfolio every cycle and NOTIFIES on:
- new shared setup (baseId unseen) -> "ready to mimic" with full detail
- setup vanished (= closed on Pariston) -> "source closed" info

Zero trading power by construction: no share/close/modify call exists in
this module. Anything that moves money happens only as an explicit,
per-trade approved UI confirm in an operator session.
Notification sinks: logs always; Telegram when MIRROR_TELEGRAM_* is set.
"""
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

from mirror.config import MirrorConfig
from mirror.feed import fetch_open_positions, fetch_setup_detail
from mirror.store import MirrorStore

logger = get_logger(__name__)


def telegram_notify(bot_token: str, chat_id: str, text: str,
                    timeout_s: int = 15) -> bool:
    """Best-effort Telegram ping. Never raises."""
    if not bot_token or not chat_id:
        return False
    try:
        import json as _json
        import urllib.request as _req
        payload = _json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = _req.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        with _req.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning(f"[Watch] telegram notify failed: {e}")
        return False


class WatcherBot:
    def __init__(
        self,
        cfg: MirrorConfig,
        fetch_positions_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
        fetch_detail_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        notify_fn: Optional[Callable[[str], bool]] = None,
    ):
        self.cfg = cfg
        self.store = MirrorStore(cfg.data_dir)
        self._fetch_positions = fetch_positions_fn or fetch_open_positions
        self._fetch_detail = fetch_detail_fn or fetch_setup_detail
        self._client_factory = client_factory or self._default_client
        self._notify_fn = notify_fn or (lambda text: telegram_notify(
            self._tg_token, self._tg_chat, text))
        self._tg_token = ""
        self._tg_chat = ""

    def _default_client(self):
        from src.invo.client import InvoClient
        return InvoClient(email=self.cfg.email, storage_dir=self.cfg.data_dir)

    def set_telegram(self, bot_token: str, chat_id: str) -> None:
        self._tg_token = bot_token
        self._tg_chat = chat_id

    def _notify(self, text: str) -> None:
        logger.info(f"[Watch] {text}")
        self.store.audit("notify", {"text": text[:300]})
        try:
            self._notify_fn(text)
        except Exception as e:
            logger.warning(f"[Watch] notify sink failed: {e}")

    def run_once(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"new": 0, "closed": 0, "errors": 0}
        try:
            client = self._client_factory()
            client.ensure_auth(email=self.cfg.email,
                               app_password=self.cfg.app_password,
                               device_id=self.cfg.device_id)
            token = client.access_token or ""
            positions = self._fetch_positions(
                client, token, self.cfg.pariston_portfolio_id)
        except Exception as e:
            logger.warning(f"[Watch] cycle skipped: {e}")
            return {**summary, "errors": 1}

        live_ids = set()
        for pos in positions:
            sid = str(pos.get("baseId") or "")
            if not sid:
                continue
            live_ids.add(sid)
            if sid in self.store.seen_ids():
                continue
            self.store.mark_seen(sid)
            summary["new"] += 1
            try:
                setup = self._fetch_detail(client, token, sid)
            except Exception as e:
                setup = {"_detail_error": str(e)[:120]}
            ticker = pos.get("ticker", "?")
            self._notify(
                "Pariston shared a trade — ready to mimic: "
                f"{ticker} {pos.get('side')} {pos.get('leverage')}x "
                f"entry={pos.get('entry')} "
                f"TP={setup.get('tp', setup.get('tp1'))} SL={setup.get('stop_loss')} "
                f"[source={sid}]")

        summary["closed"] = self._report_vanished(live_ids)
        logger.info(f"[Watch] cycle: {summary}")
        return summary

    def _report_vanished(self, live_ids: set) -> int:
        """Seen sources that disappeared from Pariston = closed there."""
        n = 0
        seen = self.store.seen_ids()
        closed_mark = self.store.reported_closed_ids()
        for sid in seen - live_ids - closed_mark:
            n += 1
            self.store.mark_reported_closed(sid)
            self._notify(f"Pariston closed a setup [source={sid}] — "
                         f"mimicked copy (if any) can be closed alongside.")
        return n

    def run_forever(self) -> None:
        logger.info("[Watch] started "
                    f"(poll={self.cfg.poll_s}s, "
                    f"watch={self.cfg.pariston_portfolio_id})")
        while True:
            try:
                self.run_once()
            except Exception as e:  # last-resort guard: the loop never dies
                logger.warning(f"[Watch] cycle crashed (contained): {e}")
            time.sleep(self.cfg.poll_s)
