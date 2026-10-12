"""Auto-mimic: feed detection -> capital brain -> HL execution -> record.

Loop per cycle (loop never dies; money moves only when live=True AND
every guard passes AND the HL fill verifies):
  1. FeedWatcher.poll: new OPEN/increase posts by Pariston -> candidates;
     CLOSE posts on mirrored coins -> closes; anything else -> alerts.
  2. Brain (decide_live_size): flat cap + floor + concurrency guards.
  3. HL bridge: set leverage, IOC open, parse fill (raises on error).
  4. Recorder (best-effort): record the copy on Invo with real mimicMeta
     from the feed post. A failed record alerts but never unwinds the fill.

Exit doctrine (adopted from the proven reference build): no independent
TP/SL on live copies — we trust Pariston's entries AND exits. Source TP/SL
edits arrive as feed update posts and are logged/alerted, not traded.

MIMIC_LIVE=false (default) runs the full loop with a fake-or-read-only
posture: detection + sizing + audit, zero orders. Flip only with caps set.
"""
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

from mirror.config import MirrorConfig
from mirror.feedmon import FeedWatcher, classify_post
from mirror.hl import HlBridge
from mirror.recorder import TradingSession
from mirror.store import MirrorStore

logger = get_logger(__name__)

PARISTON_OWNER_ID = "7fd09aef-d45f-47dd-aa7f-72143e7d5a05"
PARISTON_PORTFOLIO_ID = "17cc19aa-d854-4857-9cca-7f05f21ec2da"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass
class MimicConfig:
    live: bool = False
    account_usd: float = 18.38
    max_usd_per_mimic: float = 5.0
    max_pct_per_mimic: float = 25.0
    max_concurrent: int = 2
    min_remaining_usd: float = 3.0
    feed_size: int = 20

    @classmethod
    def from_env(cls) -> "MimicConfig":
        return cls(
            live=os.getenv("MIMIC_LIVE", "false").lower() == "true",
            account_usd=_f("MIMIC_ACCOUNT_USD", 18.38),
            max_usd_per_mimic=_f("MIMIC_MAX_USD", 5.0),
            max_pct_per_mimic=_f("MIMIC_MAX_PCT", 25.0),
            max_concurrent=_i("MIMIC_MAX_CONCURRENT", 2),
            min_remaining_usd=_f("MIMIC_MIN_REMAINING_USD", 3.0),
            feed_size=_i("MIMIC_FEED_SIZE", 20),
        )


def decide_live_size(account_usd: float, committed_usd: float,
                     cfg: MimicConfig) -> tuple:
    """(margin_usd | None, reason). Real-money brain: flat cap + guards.

    Never risks more than max_pct of the account or max_usd flat, never
    drops remaining below the floor (concurrency checked by caller).
    """
    if account_usd is None or account_usd <= 0:
        return None, "skip:bank-unknown"
    remaining = account_usd - committed_usd
    if remaining < cfg.min_remaining_usd:
        return None, f"skip:below-floor-{remaining:.2f}"
    target = min(cfg.max_usd_per_mimic, account_usd * cfg.max_pct_per_mimic / 100.0)
    target = round(target, 2)
    if target <= 0:
        return None, "skip:risk-cap-zero"
    if target > remaining - cfg.min_remaining_usd + 1e-9:
        target = round(remaining - cfg.min_remaining_usd, 2)
        if target <= 0:
            return None, "skip:below-floor-after-cap"
        return target, f"shrunk-to-floor:{target}"
    return target, "full-mimic"


def is_pariston(ev: Dict[str, Any]) -> bool:
    return ev.get("owner_id") == PARISTON_OWNER_ID or \
        ev.get("portfolio_id") == PARISTON_PORTFOLIO_ID


def build_record_open(ev: Dict[str, Any], coin: str, asset_index: int,
                      side: str, leverage: int, qty_before: str,
                      qty_after: str, hl_order: Any, hl_response: Any,
                      nonce_ms: int) -> Dict[str, Any]:
    """Invo copy record, field-for-field like the app's own confirm."""
    return {
        "clientTxId": f"entry:{uuid.uuid4().hex[:8]}",
        "coin": coin,
        "assetIndex": asset_index,
        "entry": {"side": side.lower(), "marginMode": "isolated",
                  "leverage": int(leverage), "tpPx": None, "slPx": None},
        "submission": {"hlOrder": hl_order, "nonceMs": nonce_ms,
                       "hlResponse": hl_response},
        "summary": {"qtyBefore": str(qty_before), "qtyAfter": str(qty_after),
                    "intendedLeverage": int(leverage)},
        "mimicMeta": {
            "portfolioId": ev.get("portfolio_id"),
            "creatorInvoUserId": ev.get("owner_id"),
            "initialSourcePaperUpdateId": ev.get("post_id"),
            "sourcePaperTradeBaseId": ev.get("base_id"),
        },
    }


class AutoBot:
    def __init__(self, mirror_cfg: MirrorConfig, mimic_cfg: MimicConfig,
                 hl: Optional[HlBridge] = None,
                 session: Optional[TradingSession] = None,
                 watcher: Optional[FeedWatcher] = None,
                 notify_fn=None):
        self.mirror_cfg = mirror_cfg
        self.cfg = mimic_cfg
        self.store = MirrorStore(mirror_cfg.data_dir)
        self.hl = hl
        self.session = session
        self.watcher = watcher or FeedWatcher()
        self._notify_fn = notify_fn or (lambda t: logger.info(f"[Auto] {t}"))

    def _notify(self, text: str) -> None:
        self.store.audit("notify", {"text": text[:300]})
        try:
            self._notify_fn(text)
        except Exception as e:
            logger.warning(f"[Auto] notify sink failed: {e}")

    def _account_usd(self) -> float:
        """Live HL equity first (dynamic — tracks deposits/PnL by itself);
        env value only as fallback before the wallet is configured."""
        if self.hl is not None:
            try:
                live = self.hl.equity()
                if live is not None and live > 0:
                    return live
            except Exception:
                pass
        return self.cfg.account_usd

    def _client(self):
        from src.invo.client import InvoClient
        client = InvoClient(email=self.mirror_cfg.email,
                            storage_dir=self.mirror_cfg.data_dir)
        client.ensure_auth(email=self.mirror_cfg.email,
                           app_password=self.mirror_cfg.app_password,
                           device_id=self.mirror_cfg.device_id)
        return client

    def _token(self) -> str:
        if self.mirror_cfg.email and self.mirror_cfg.app_password:
            try:
                import time
                from mirror.recorder import decode_exp
                client = self._client()
                # Invo answers some endpoints with 403 (not 401) on expired
                # tokens — and nothing clears those. So validate proactively:
                # near-expiry forces the refresh/OTP chain instead of reuse.
                exp = decode_exp(client.access_token or "") or 0
                if exp - time.time() < 180:
                    client.access_token = None
                client.ensure_auth(email=self.mirror_cfg.email,
                                   app_password=self.mirror_cfg.app_password,
                                   device_id=self.mirror_cfg.device_id)
                return client.access_token or ""
            except Exception as e:
                logger.warning(f"[Auto] feed login failed: {e}")
                return ""
        return os.getenv("MIMIC_FEED_TOKEN", "")

    def committed_usd(self) -> float:
        total = 0.0
        for e in self.store.recent_audit(5000):
            d = e.get("detail") or {}
            try:
                if e.get("event") == "mimicked":
                    total += float(d.get("margin_usd", 0))
                elif e.get("event") == "mimic_closed":
                    total -= float(d.get("margin_usd", 0))
            except (TypeError, ValueError):
                continue
        return max(0.0, round(total, 2))

    def open_count(self) -> int:
        events = self.store.recent_audit(5000)
        return (sum(1 for e in events if e.get("event") == "mimicked") -
                sum(1 for e in events if e.get("event") == "mimic_closed"))

    def mirrored_coins(self) -> Dict[str, Dict[str, Any]]:
        """coin -> {margin_usd, source} for open mimics."""
        out: Dict[str, Dict[str, Any]] = {}
        for e in self.store.recent_audit(5000):
            d = e.get("detail") or {}
            if e.get("event") == "mimicked" and d.get("coin"):
                out[d["coin"]] = {"margin_usd": d.get("margin_usd", 0),
                                  "source": d.get("source")}
            elif e.get("event") == "mimic_closed" and d.get("coin") in out:
                del out[d["coin"]]
        return out

    def reconcile_ledger(self) -> int:
        """Self-heal: ledger-open coins flat on HL (manual closes, liquidations)
        are marked closed so caps/concurrency reflect reality. Returns count."""
        if self.hl is None:
            return 0
        healed = 0
        for coin, info in list(self.mirrored_coins().items()):
            try:
                if self.hl.position(coin) is None:
                    self.store.audit("mimic_closed", {
                        "coin": coin, "margin_usd": info.get("margin_usd", 0),
                        "note": "reconciled-flat"})
                    healed += 1
                    logger.info(f"[Auto] reconciled flat {coin}")
            except Exception as e:
                logger.warning(f"[Auto] reconcile check failed: {e}")
        return healed

    def bootstrap_from_sims(self, client, token: str) -> Dict[str, Any]:
        """One-shot: mimic Pariston's ALREADY-open positions (feed only
        emits new posts, so startup would otherwise miss them). Each open
        not already mirrored becomes one capped mimic; seen-marked so a
        restart never re-fires. Returns a summary."""
        from mirror.feed import fetch_open_positions
        summary: Dict[str, Any] = {"mimicked": 0, "skipped": 0, "errors": 0}
        try:
            positions = fetch_open_positions(
                client, token, PARISTON_PORTFOLIO_ID)
        except Exception as e:
            logger.warning(f"[Auto] bootstrap unreadable: {e}")
            summary["errors"] = 1
            return summary
        mirrored = self.mirrored_coins()
        for pos in positions:
            base = str(pos.get("baseId") or "")
            if not base or f"sims-{base}" in self.store.seen_ids():
                continue
            ticker = str(pos.get("ticker") or "")
            if ticker in mirrored:
                self.store.mark_seen(f"sims-{base}")
                continue
            if self.hl is not None:
                try:
                    if self.hl.position(ticker) is not None:
                        # Wallet already exposed (manual or older copy):
                        # never blind-add, alert instead.
                        self.store.mark_seen(f"sims-{base}")
                        self._notify(f"SKIP bootstrap {ticker}: wallet "
                                     f"already holds it (manual review).")
                        summary["skipped"] += 1
                        continue
                except Exception as e:
                    logger.warning(f"[Auto] position check failed: {e}")
            self.store.mark_seen(f"sims-{base}")
            ev = {"action": "open", "post_id": f"sims-{base}",
                  "base_id": base, "base_short_id": "",
                  "ticker": ticker, "side": pos.get("side", "SHORT"),
                  "leverage": pos.get("leverage", 10),
                  "owner_id": PARISTON_OWNER_ID,
                  "portfolio_id": PARISTON_PORTFOLIO_ID}
            try:
                self._mimic_open(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"[Auto] bootstrap mimic failed: {e}")
        logger.info(f"[Auto] bootstrap: {summary}")
        return summary

    def run_once(self, token: str = "") -> Dict[str, Any]:
        summary: Dict[str, Any] = {"mimicked": 0, "closed": 0,
                                   "skipped": 0, "alerts": 0, "errors": 0}
        try:
            token = token or self._token()
            news, updates = self.watcher.poll(token)
        except Exception as e:
            logger.warning(f"[Auto] feed unreachable, cycle skipped: {e}")
            return {**summary, "errors": 1}
        for ev in news:
            if not is_pariston(ev):
                continue
            try:
                self._mimic_open(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                self._notify(f"MIMIC FAILED {ev.get('ticker')}: {e}")
                logger.warning(f"[Auto] mimic failed: {e}")
        for ev in updates:
            if not is_pariston(ev):
                continue
            try:
                self._follow_update(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"[Auto] update follow failed: {e}")
        logger.info(f"[Auto] cycle: {summary}")
        return summary

    def _mimic_open(self, ev: Dict[str, Any], summary: Dict[str, Any]) -> None:
        coin = (ev.get("ticker") or "").replace("USDT", "").replace("USDC", "")
        side = str(ev.get("side") or "LONG").upper()
        leverage = int(ev.get("leverage") or 10)
        if not coin:
            return
        if self.open_count() >= self.cfg.max_concurrent:
            summary["skipped"] += 1
            self._notify(f"SKIP {coin}: max-concurrent-{self.cfg.max_concurrent}")
            return
        margin, reason = decide_live_size(self._account_usd(),
                                          self.committed_usd(), self.cfg)
        if margin is None:
            summary["skipped"] += 1
            self._notify(f"SKIP {coin}: {reason}")
            return
        self._notify(f"Pariston {coin} {side} {leverage}x -> mimic ${margin} "
                     f"(acct ${self._account_usd():.2f}) "
                     f"[{reason}] {'LIVE' if self.cfg.live else 'DRY-RUN'}")
        if not self.cfg.live or self.hl is None:
            summary["skipped"] += 1
            self.store.audit("mimic_dry_run",
                             {"source": ev.get("base_id"), "coin": coin,
                              "side": side, "leverage": leverage,
                              "margin_usd": margin})
            return
        before = self.hl.position(coin)
        qty_before = before["szi"] if before else "0"
        fill = self.hl.market_open(coin, side, margin, leverage)
        summary["mimicked"] += 1
        detail = {"source": ev.get("base_id"), "coin": coin, "side": side,
                  "leverage": fill.get("leverage", leverage),
                  "margin_usd": margin,
                  "avg_px": fill.get("avg_px"), "oid": fill.get("oid")}
        self.store.audit("mimicked", detail)
        self._notify(f"MIMICKED {coin} {side} ${margin} @ {fill.get('avg_px')} "
                     f"oid={fill.get('oid')}")
        if self.session is not None:
            try:
                meta = self.hl.meta().get(coin, {})
                rec = build_record_open(
                    ev, coin, meta.get("assetIndex", 0), side, leverage,
                    qty_before, fill.get("size", 0),
                    fill.get("raw"), fill.get("raw"), int(time.time() * 1000))
                res = self.session.record_open(rec)
                self.store.audit("recorded",
                                 {"coin": coin, "ok": bool(res.get("success"))})
            except Exception as e:
                self._notify(f"RECORD FAILED {coin} (fill stands): {e}")
                logger.warning(f"[Auto] record failed: {e}")

    def _follow_update(self, ev: Dict[str, Any], summary: Dict[str, Any]) -> None:
        coin = (ev.get("ticker") or "").replace("USDT", "").replace("USDC", "")
        if not coin:
            return
        if ev.get("action") == "close":
            copies = self.mirrored_coins()
            if coin not in copies:
                return
            if not self.cfg.live or self.hl is None:
                summary["alerts"] += 1
                self._notify(f"Pariston closed {coin} — close your copy "
                             f"(dry-run: no action taken).")
                return
            fill = self.hl.close(coin)
            summary["closed"] += 1
            self.store.audit("mimic_closed",
                             {"coin": coin,
                              "margin_usd": copies[coin]["margin_usd"]})
            self._notify(f"CLOSED {coin} alongside Pariston.")
            if self.session is not None:
                try:
                    meta = self.hl.meta().get(coin, {})
                    self.session.record_close({
                        "clientTxId": f"close:{uuid.uuid4().hex[:8]}",
                        "baseShortId": copies[coin].get("source") or coin,
                        "assetIndex": meta.get("assetIndex", 0),
                        "submission": {"hlOrder": fill.get("raw"),
                                       "nonceMs": int(time.time() * 1000),
                                       "hlResponse": fill.get("raw")},
                        "summary": {"qtyBefore": "?", "qtyAfter": "0"}})
                except Exception as e:
                    logger.warning(f"[Auto] close-record failed: {e}")
            return
        # TP/SL edits and size adds on the source: logged + alerted.
        # Doctrine: no independent TP/SL on live copies; entries+closes only.
        summary["alerts"] += 1
        self._notify(f"Pariston updated {coin} ({ev.get('action')} "
                     f"tp={ev.get('tp')} sl={ev.get('sl')}) — logged, no action.")

    def run_forever(self, token: str = "") -> None:
        logger.info("[Auto] started "
                    f"(live={self.cfg.live}, poll={self.mirror_cfg.poll_s}s)")
        try:
            client = self._client()
            self.reconcile_ledger()
            self.bootstrap_from_sims(client, client.access_token or "")
        except Exception as e:
            logger.warning(f"[Auto] startup bootstrap skipped: {e}")
        while True:
            try:
                self.run_once(token)
            except Exception as e:
                logger.warning(f"[Auto] cycle crashed (contained): {e}")
            time.sleep(self.mirror_cfg.poll_s)
