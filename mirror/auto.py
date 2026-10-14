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
edits arrive as feed update posts (fast path) AND via per-coin history
detail (ground truth: feed redacts TP/SL for non-owners, sims rows carry
none). Both replace our resting triggers.

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
    max_concurrent: int = 20
    min_remaining_usd: float = 3.0
    feed_size: int = 20

    @classmethod
    def from_env(cls) -> "MimicConfig":
        return cls(
            live=os.getenv("MIMIC_LIVE", "false").lower() == "true",
            account_usd=_f("MIMIC_ACCOUNT_USD", 18.38),
            max_usd_per_mimic=_f("MIMIC_MAX_USD", 5.0),
            max_pct_per_mimic=_f("MIMIC_MAX_PCT", 25.0),
            max_concurrent=_i("MIMIC_MAX_CONCURRENT", 20),
            min_remaining_usd=_f("MIMIC_MIN_REMAINING_USD", 3.0),
            feed_size=_i("MIMIC_FEED_SIZE", 20),
        )


def decide_live_size(account_usd: float, committed_usd: float,
                     cfg: MimicConfig, n_opens: int = 1) -> tuple:
    """(margin_usd | None, reason). Real-money brain: flat cap + guards.

    The per-mimic target splits the book across Pariston's open count so
    every setup gets mirrored (no slot lottery): min(flat cap, pct cap,
    90% of equity / opens). Floor + HL-minimum guards still apply.
    """
    if account_usd is None or account_usd <= 0:
        return None, "skip:bank-unknown"
    remaining = account_usd - committed_usd
    if remaining < cfg.min_remaining_usd:
        return None, f"skip:below-floor-{remaining:.2f}"
    target = min(cfg.max_usd_per_mimic,
                 account_usd * cfg.max_pct_per_mimic / 100.0,
                 account_usd * 0.9 / max(int(n_opens or 1), 1))
    target = round(target, 2)
    if target <= 0:
        return None, "skip:risk-cap-zero"
    if target > remaining - cfg.min_remaining_usd + 1e-9:
        target = round(remaining - cfg.min_remaining_usd, 2)
        if target <= 0:
            return None, "skip:below-floor-after-cap"
        return target, f"shrunk-to-floor:{target}"
    return target, "full-mimic"


def _signal_ts(ts: Any) -> float:
    """ISO-8601 UTC string -> epoch seconds (0 when unparsable)."""
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


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
        self._dash = {"at": 0.0, "signals": []}
        self._pariston_opens = 1
        from mirror.eventlog import EventLog
        self.events = EventLog(mirror_cfg.data_dir)

    def _ev(self, level: str, event: str, detail=None) -> None:
        try:
            self.events.log(level, event, detail)
        except Exception:
            pass

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
                                  "source": d.get("source"),
                                  "side": d.get("side"),
                                  "opened_ts": e.get("ts", 0)}
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
                    self._ev("INFO", "reconciled",
                             {"coin": coin, "note": "flat-on-HL"})
                    healed += 1
                    logger.info(f"[Auto] reconciled flat {coin}")
            except Exception as e:
                logger.warning(f"[Auto] reconcile check failed: {e}")
        return healed

    def bootstrap_from_sims(self, client, token: str) -> Dict[str, Any]:
        """Sync Pariston's ALREADY-open positions (feed only emits new
        posts). Cap/floor/minimum skips stay unmarked and retry on later
        syncs; mimicked/dry/wallet-held outcomes are terminal for their
        base id. A live coin on his board that we neither mirror nor hold
        gets one fresh attempt per sync even if previously skipped, so a
        resolved capital skip (floor lowered, slot freed) can never bury
        a coin. Returns a summary."""
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
        self._pariston_opens = max(len(positions), 1)
        for pos in positions:
            base = str(pos.get("baseId") or "")
            if not base:
                continue
            ticker = str(pos.get("ticker") or "")
            if ticker in mirrored:
                self.store.mark_seen(f"sims-{base}")
                continue
            hl_holds = False
            if self.hl is not None:
                try:
                    hl_holds = self.hl.position(ticker) is not None
                except Exception as e:
                    logger.warning(f"[Auto] position check failed: {e}")
            if hl_holds:
                # Wallet already exposed (manual or older copy):
                # never blind-add, alert instead.
                self.store.mark_seen(f"sims-{base}")
                self._notify(f"SKIP bootstrap {ticker}: wallet "
                             f"already holds it (manual review).")
                summary["skipped"] += 1
                continue
            if self.cfg.live and f"sims-{base}" in self.store.seen_ids():
                self.store.unmark_seen(f"sims-{base}")
            if f"sims-{base}" in self.store.seen_ids():
                continue
            ev = {"action": "open", "post_id": f"sims-{base}",
                  "base_id": base, "base_short_id": "",
                  "ticker": ticker, "side": pos.get("side", "SHORT"),
                  "leverage": pos.get("leverage", 10),
                  "owner_id": PARISTON_OWNER_ID,
                  "portfolio_id": PARISTON_PORTFOLIO_ID}
            try:
                outcome = self._mimic_open(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"[Auto] bootstrap mimic failed: {e}")
                continue
            # Cap/floor/minimum skips are retried on later syncs (slots free
            # up, equity resolves); everything else is terminal for this
            # base id.
            if outcome != "cap-skipped":
                self.store.mark_seen(f"sims-{base}")
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
            self._ev("ERROR", "feed_unreachable", {"error": str(e)[:160]})
            return {**summary, "errors": 1}
        for ev in news:
            if not is_pariston(ev):
                continue
            try:
                self._mimic_open(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                self._notify(f"MIMIC FAILED {ev.get('ticker')}: {e}")
                self._ev("ERROR", "mimic_failed",
                         {"ticker": ev.get("ticker"), "error": str(e)[:160]})
                logger.warning(f"[Auto] mimic failed: {e}")
        for ev in updates:
            if not is_pariston(ev):
                continue
            try:
                self._follow_update(ev, summary)
            except Exception as e:
                summary["errors"] += 1
                self._ev("ERROR", "update_failed",
                         {"ticker": ev.get("ticker"), "error": str(e)[:160]})
                logger.warning(f"[Auto] update follow failed: {e}")
        if any(summary[k] for k in ("mimicked", "closed", "errors", "alerts")):
            self._ev("INFO" if not summary["errors"] else "ERROR",
                     "cycle", dict(summary))
        logger.info(f"[Auto] cycle: {summary}")
        return summary

    def _mimic_open(self, ev: Dict[str, Any], summary: Dict[str, Any]) -> str:
        """Attempt one mimic. Returns the outcome: 'mimicked', 'dry',
        'cap-skipped' (retryable when slots free up or capital resolves),
        'empty' (no coin), or 'hl-skipped' (terminal: bank-unknown or
        zeroed risk caps — operator/config errors, never retried).
        Raises on execution failure."""
        coin = (ev.get("ticker") or "").replace("USDT", "").replace("USDC", "")
        side = str(ev.get("side") or "LONG").upper()
        leverage = int(ev.get("leverage") or 10)
        if not coin:
            return "empty"
        if self.open_count() >= self.cfg.max_concurrent:
            summary["skipped"] += 1
            self._notify(f"SKIP {coin}: max-concurrent-{self.cfg.max_concurrent}")
            self._ev("WARN", "skip", {"coin": coin,
                                      "reason": f"max-concurrent-{self.cfg.max_concurrent}"})
            return "cap-skipped"
        margin, reason = decide_live_size(self._account_usd(),
                                          self.committed_usd(), self.cfg,
                                          self._pariston_opens)
        if margin is None:
            summary["skipped"] += 1
            self._notify(f"SKIP {coin}: {reason}")
            self._ev("WARN", "skip", {"coin": coin, "reason": reason})
            return "cap-skipped" if "floor" in reason or "concurrent" in reason else "hl-skipped"
        # HL $10 order minimum: meet it within caps+floor, else skip loudly.
        import math as _math
        if margin * leverage < 10.5:
            need = _math.ceil(10.5 / leverage * 100) / 100
            room = self._account_usd() - self.committed_usd() - \
                self.cfg.min_remaining_usd
            cap = min(self.cfg.max_usd_per_mimic,
                      self._account_usd() * self.cfg.max_pct_per_mimic / 100.0)
            if need <= min(cap, room):
                margin, reason = round(need, 2), "bumped-to-hl-min"
            else:
                summary["skipped"] += 1
                self._notify(f"SKIP {coin}: under-hl-minimum "
                             f"(${margin * leverage:.2f} notional)")
                self._ev("WARN", "skip",
                         {"coin": coin, "reason": "under-hl-minimum"})
                # Transient: equity frees up as positions close, so the
                # bootstrap must retry this base id on later syncs.
                return "cap-skipped"
        self._notify(f"Pariston {coin} {side} {leverage}x -> mimic ${margin} "
                     f"(acct ${self._account_usd():.2f}) "
                     f"[{reason}] {'LIVE' if self.cfg.live else 'DRY-RUN'}")
        if not self.cfg.live or self.hl is None:
            summary["skipped"] += 1
            self.store.audit("mimic_dry_run",
                             {"source": ev.get("base_id"), "coin": coin,
                              "side": side, "leverage": leverage,
                              "margin_usd": margin})
            return "dry"
        before = self.hl.position(coin)
        qty_before = before["szi"] if before else "0"
        fill = self.hl.market_open(coin, side, margin, leverage)
        summary["mimicked"] += 1
        detail = {"source": ev.get("base_id"), "coin": coin, "side": side,
                  "leverage": fill.get("leverage", leverage),
                  "margin_usd": margin,
                  "avg_px": fill.get("avg_px"), "oid": fill.get("oid")}
        self.store.audit("mimicked", detail)
        self._ev("INFO", "mimicked", {"coin": coin, "side": side,
                                      "margin_usd": margin,
                                      "avg_px": fill.get("avg_px"),
                                      "oid": fill.get("oid")})
        self._notify(f"MIMICKED {coin} {side} ${margin} @ {fill.get('avg_px')} "
                     f"oid={fill.get('oid')}")
        self._attach_tpsl(coin, side, ev)
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
                self._ev("WARN", "record_failed",
                         {"coin": coin, "error": str(e)[:160]})
                logger.warning(f"[Auto] record failed: {e}")
        return "mimicked"

    def _resolve_local_tpsl(self, ticker: str, side: str,
                            entry: float, open_ts: float = 0) -> tuple:
        """Pariston's TP/SL for an open copy. Primary: live main-bot
        dashboard signals — same coin+side, newest signal within
        [open-3h, open+15m], entry within 2% as sanity. Fallback: local
        outcome ledger (same keys, entry within 0.5%). No match, no
        triggers — arming wrong levels is worse than arming none."""
        if entry is None or entry <= 0:
            return None, None
        symbol = ticker if ticker.endswith("USDT") else ticker + "USDT"
        best, best_key = None, None
        try:
            for rec in self._dashboard_signals():
                if rec.get("symbol") != symbol or \
                        str(rec.get("side", "")).upper() != side.upper():
                    continue
                sts = _signal_ts(rec.get("timestamp"))
                if open_ts and sts:
                    if not (open_ts - 3 * 3600 <= sts <= open_ts + 900):
                        continue
                try:
                    dist = abs(float(rec.get("entry", 0)) - entry) / entry
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
                if dist > 0.02:
                    continue
                tp = rec.get("tp", rec.get("tp1"))
                sl = rec.get("stop_loss")
                if tp is None or sl is None:
                    continue
                key = sts or 0
                if best_key is None or key > best_key:
                    best, best_key = (tp, sl), key
            if best:
                return best
        except Exception as e:
            logger.warning(f"[Auto] dashboard resolve failed: {e}")
        try:
            for rec in self._ledger_signals(symbol):
                if str(rec.get("side", "")).upper() != side.upper():
                    continue
                try:
                    dist = abs(float(rec.get("entry", 0)) - entry) / entry
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
                if dist <= 0.005:
                    tp = rec.get("tp", rec.get("tp1"))
                    sl = rec.get("stop_loss")
                    if tp is not None and sl is not None:
                        return tp, sl
        except Exception:
            pass
        return None, None

    def _dashboard_signals(self) -> List[Dict[str, Any]]:
        """Live signals from the main bot dashboard (60s cache). Empty when
        unconfigured — never raises into the caller (handled there)."""
        import time as _time
        import urllib.request as _req
        import urllib.parse as _parse
        import json as _json
        base = os.getenv("MIMIC_MAIN_DASHBOARD_URL", "").rstrip("/")
        token = os.getenv("MIMIC_MAIN_DASHBOARD_TOKEN", "")
        if not base or not token:
            return []
        if _time.time() - self._dash["at"] < 60 and self._dash["signals"]:
            return self._dash["signals"]
        url = base + "/api/signals?limit=100&token=" + _parse.quote(token)
        req = _req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _req.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        sigs = data if isinstance(data, list) else []
        self._dash.update(at=_time.time(), signals=sigs)
        return sigs

    def _ledger_signals(self, symbol: str) -> List[Dict[str, Any]]:
        import json as _json
        import os as _os
        path = _os.path.join(os.getenv("MIMIC_MAIN_DATA_DIR", "data_store"),
                             "outcomes.json")
        with open(path, encoding="utf-8") as f:
            ledger = _json.load(f)
        if not isinstance(ledger, dict):
            return []
        return [r for r in ledger.values()
                if isinstance(r, dict) and r.get("symbol") == symbol]

    def _backfill_tpsl(self) -> int:
        """Attach triggers to mirrored coins missing them (sims-sourced
        opens carry no TP/SL; feed posts do). Returns count attached."""
        n = 0
        for coin, info in list(self.mirrored_coins().items()):
            if self._tpsl_of(coin):
                continue
            try:
                pos = self.hl.position(coin) if self.hl else None
                if pos is None:
                    continue
                side = "LONG" if float(pos.get("szi", 0) or 0) > 0 else "SHORT"
                tp, sl = self._resolve_local_tpsl(
                    coin, side, float(pos.get("entryPx", 0) or 0),
                    info.get("opened_ts", 0))
                if tp is None or sl is None:
                    continue
                self._attach_tpsl(coin, side, {"tp": tp, "sl": sl})
                if self._tpsl_of(coin):
                    n += 1
            except Exception as e:
                logger.warning(f"[Auto] tpsl backfill failed {coin}: {e}")
        return n

    def _tpsl_of(self, coin: str) -> List[str]:
        """Latest resting trigger cloids for a coin (empty when none)."""
        cloids: List[str] = []
        for e in self.store.recent_audit(5000):
            if e.get("event") == "tpsl_placed" and \
                    (e.get("detail") or {}).get("coin") == coin:
                cloids = (e.get("detail") or {}).get("cloids", []) or []
            elif e.get("event") in ("tpsl_cleared", "mimic_closed") and \
                    (e.get("detail") or {}).get("coin") == coin:
                cloids = []
        return cloids

    def _last_tpsl_levels(self, coin: str) -> tuple:
        """Last synced (tp, sl) for a coin, or (None, None)."""
        tp, sl = None, None
        for e in self.store.recent_audit(5000):
            d = e.get("detail") or {}
            if e.get("event") == "tpsl_placed" and d.get("coin") == coin:
                tp, sl = d.get("tp"), d.get("sl")
            elif e.get("event") in ("tpsl_cleared", "mimic_closed") \
                    and d.get("coin") == coin:
                tp, sl = None, None
        return tp, sl

    @staticmethod
    def _levels_moved(old: Any, new: Any) -> bool:
        try:
            if old is None or new is None:
                return old != new
            return abs(float(old) - float(new)) > 1e-9
        except (TypeError, ValueError):
            return old != new

    def _sync_edits(self, client, token: str) -> int:
        """Ground-truth TP/SL sync: per mirrored coin, read Pariston's live
        levels via history detail and replace resting triggers on change.
        This is what catches edits the feed redacts (e.g. UNI TP move).
        Best-effort per coin; never raises. Returns count synced."""
        if not self.cfg.live or self.hl is None:
            return 0
        from mirror.feed import fetch_source_levels
        copies = self.mirrored_coins()
        n = 0
        for coin, info in list(copies.items()):
            base = str(info.get("source") or "")
            if not base:
                continue
            try:
                lv = fetch_source_levels(client, token, base)
            except Exception as e:
                logger.warning(f"[Auto] edit sync unreadable {coin}: {e}")
                continue
            if not lv or lv.get("is_open") is False:
                continue  # closes handled by feed/sims paths
            tp, sl = lv.get("tp"), lv.get("stop_loss")
            if tp is None or sl is None:
                continue
            old_tp, old_sl = self._last_tpsl_levels(coin)
            if old_tp is None and old_sl is None:
                pass  # missing triggers: attach
            elif not (self._levels_moved(old_tp, tp)
                      or self._levels_moved(old_sl, sl)):
                continue
            try:
                pos = self.hl.position(coin)
                if pos is None:
                    continue
                side = "LONG" if float(pos.get("szi", 0) or 0) > 0 else "SHORT"
                before_n = len(self._tpsl_of(coin))
                self._attach_tpsl(coin, side, {"tp": tp, "sl": sl})
                if self._tpsl_of(coin):
                    n += 1
                    self._notify(f"TP/SL synced {coin} from source "
                                 f"(replaced {before_n} triggers).")
            except Exception as e:
                logger.warning(f"[Auto] edit sync failed {coin}: {e}")
        return n

    def _attach_tpsl(self, coin: str, side: str, ev: Dict[str, Any],
                     open_ts: float = 0) -> None:
        """Rest Pariston's TP/SL as native triggers. Levels come from the
        event when present, else resolved via the dashboard by open time.
        Best-effort: a failure alerts but never unwinds the fill."""
        tp, sl = ev.get("tp"), ev.get("sl")
        if tp is None or sl is None:
            tp, sl = self._resolve_local_tpsl(
                coin, side,
                float((self.hl.position(coin) or {}).get("entryPx", 0) or 0)
                if self.hl else 0,
                open_ts or __import__("time").time())
            if tp is None or sl is None:
                return
        try:
            from mirror.hl import HlBridge
            pos = self.hl.position(coin) if self.hl else None
            if pos is None:
                return
            size = abs(float(pos.get("szi", 0) or 0))
            if size <= 0:
                return
            entry = float(pos.get("entryPx", 0) or 0)
            tp, sl = HlBridge.sane_tpsl(side, entry, tp, sl)
            self.hl.cancel_tpsl(coin, self._tpsl_of(coin))
            self.hl.cancel_coin_triggers(coin)
            out = self.hl.place_tpsl(coin, side, size, tp, sl)
            self.store.audit("tpsl_placed", {
                "coin": coin, "tp": tp, "sl": sl,
                "cloids": [out["tp_cloid"], out["sl_cloid"]]})
            self._ev("INFO", "tpsl_placed",
                     {"coin": coin, "tp": tp, "sl": sl})
            self._notify(f"TP/SL set {coin}: tp={tp} sl={sl}")
        except Exception as e:
            self._notify(f"TPSL FAILED {coin} (position stands): {e}")
            self._ev("WARN", "tpsl_failed",
                     {"coin": coin, "error": str(e)[:160]})
            logger.warning(f"[Auto] tpsl failed: {e}")

    def _close_coin(self, coin: str, copies: Dict[str, Any],
                    summary: Dict[str, Any], why: str) -> None:
        """Market-close one mirrored coin (triggers canceled first). Shared
        by feed closes and the sims safety net. No-op when dry/not held."""
        if coin not in copies:
            return
        if not self.cfg.live or self.hl is None:
            summary["alerts"] += 1
            self._notify(f"Pariston closed {coin} — close your copy "
                         f"(dry-run: no action taken).")
            return
        if self.hl is not None:
            self.hl.cancel_tpsl(coin, self._tpsl_of(coin))
            self.hl.cancel_coin_triggers(coin)
        try:
            fill = self.hl.close(coin)
        except Exception as e:
            if "no open position" in str(e):
                # Goal state already true (trigger fill, manual close, or
                # liquidation): heal the ledger instead of erroring.
                self.store.audit("mimic_closed",
                                 {"coin": coin,
                                  "margin_usd": copies[coin]["margin_usd"],
                                  "note": "already-flat"})
                summary["closed"] += 1
                self._ev("INFO", "closed",
                         {"coin": coin, "why": "already-flat"})
                self._notify(f"CLOSED {coin} (already flat on HL).")
                return
            raise
        summary["closed"] += 1
        self.store.audit("mimic_closed",
                         {"coin": coin,
                          "margin_usd": copies[coin]["margin_usd"]})
        self._notify(f"CLOSED {coin} alongside Pariston ({why}).")
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

    def _follow_update(self, ev: Dict[str, Any], summary: Dict[str, Any]) -> None:
        coin = (ev.get("ticker") or "").replace("USDT", "").replace("USDC", "")
        if not coin:
            return
        if ev.get("action") == "close":
            self._close_coin(coin, self.mirrored_coins(), summary, "feed")
            return
        # Source TP/SL edit on a mirrored coin: replace our resting
        # triggers immediately (fast path). Redacted edits (tp/sl None for
        # non-owners) fall through to the per-coin history sync in
        # _sims_sync, which catches them within ~60s.
        copies = self.mirrored_coins()
        if coin in copies and ev.get("tp") is not None \
                and ev.get("sl") is not None:
            if not self.cfg.live or self.hl is None:
                summary["alerts"] += 1
                self._notify(f"Pariston moved TP/SL on {coin} "
                             f"(dry-run: no action taken).")
                return
            before_n = len(self._tpsl_of(coin))
            self._attach_tpsl(coin, str(copies[coin].get("side") or
                                        ev.get("side") or "LONG"), ev)
            summary["alerts"] += 1
            self._notify(f"TP/SL synced {coin} (replaced {before_n} triggers).")
            return
        summary["alerts"] += 1
        self._notify(f"Pariston updated {coin} ({ev.get('action')} "
                     f"tp={ev.get('tp')} sl={ev.get('sl')}) — logged, no action.")

    def _sims_sync(self, client, token: str) -> Dict[str, Any]:
        """Ground-truth sync against Pariston's board: open the missing,
        close what he no longer holds (side-flip counts as closed), then
        backfill TP/SL. A malformed/empty-unverifiable read aborts before
        any close — an unreadable board must never flatten the book."""
        from mirror.feed import fetch_open_positions
        summary: Dict[str, Any] = {"mimicked": 0, "closed": 0,
                                   "skipped": 0, "alerts": 0, "errors": 0}
        try:
            snap = client.list_paper_investments(token, PARISTON_PORTFOLIO_ID)
        except Exception as e:
            logger.warning(f"[Auto] sims sync unreadable: {e}")
            summary["errors"] = 1
            return summary
        if not isinstance(snap, dict) or snap.get("success") is False:
            logger.warning("[Auto] sims sync rejected (bad envelope)")
            summary["errors"] = 1
            return summary
        invs = snap.get("investments")
        if not isinstance(invs, list):
            logger.warning("[Auto] sims sync rejected (no investments list)")
            summary["errors"] = 1
            return summary
        board: Dict[str, str] = {}
        for inv in invs:
            if not isinstance(inv, dict) or not inv.get("baseId"):
                continue
            coin = str(inv.get("ticker") or "").upper()
            if coin:
                board[coin] = "LONG" if inv.get("directionLong") else "SHORT"
        self._pariston_opens = len(board)
        # Heal first: ledger-open but HL-flat (trigger fills, manual
        # closes) must not reach the close path as errors.
        try:
            healed = self.reconcile_ledger()
            if healed:
                logger.info(f"[Auto] sims sync healed {healed} flat leg(s)")
        except Exception as e:
            logger.warning(f"[Auto] sims reconcile failed: {e}")
        # Adopt wallet holdings that match his board but predate the ledger
        # (manual copies, previous bot generations): they enter full
        # lifecycle tracking instead of shadowing it forever.
        if self.cfg.live and self.hl is not None:
            copies = self.mirrored_coins()
            for coin, side in board.items():
                if coin in copies:
                    continue
                try:
                    pos = self.hl.position(coin)
                except Exception:
                    continue
                if pos is None:
                    continue
                try:
                    lev = (pos.get("leverage") or {}).get("value", 0)
                    margin = round(abs(float(pos.get("positionValue", 0) or 0)) /
                                   max(int(lev or 1), 1), 2)
                except (TypeError, ValueError, ZeroDivisionError):
                    margin = 0.0
                base = next((str(i.get("baseId") or "") for i in invs
                             if isinstance(i, dict) and
                             str(i.get("ticker") or "").upper() == coin), "")
                self.store.audit("mimicked", {
                    "coin": coin, "side": side, "margin_usd": margin,
                    "source": base, "note": "adopted-wallet-holding",
                    "avg_px": pos.get("entryPx")})
                self.store.mark_seen(f"sims-{base}" if base else f"adopt-{coin}")
                self._ev("INFO", "adopted",
                         {"coin": coin, "side": side, "margin_usd": margin})
                self._notify(f"ADOPTED {coin} {side} (${margin} margin) "
                             f"into lifecycle tracking.")
        # Closes first: mirrored coin gone from his board, or flipped side.
        copies = self.mirrored_coins()
        for coin in list(copies):
            if coin not in board:
                try:
                    self._close_coin(coin, copies, summary, "sims-missing")
                except Exception as e:
                    summary["errors"] += 1
                    self._ev("ERROR", "close_failed",
                             {"coin": coin, "error": str(e)[:160]})
                    logger.warning(f"[Auto] sync close failed {coin}: {e}")
            elif board[coin] != str(copies[coin].get("side") or "").upper():
                try:
                    self._close_coin(coin, copies, summary, "sims-flip")
                except Exception as e:
                    summary["errors"] += 1
                    self._ev("ERROR", "close_failed",
                             {"coin": coin, "error": str(e)[:160]})
                    logger.warning(f"[Auto] sync close failed {coin}: {e}")
        part = self.bootstrap_from_sims(client, token)
        for k in ("mimicked", "skipped", "errors"):
            summary[k] += part.get(k, 0)
        healed = self._backfill_tpsl()
        if healed:
            logger.info(f"[Auto] tpsl backfilled on {healed} coin(s)")
        try:
            synced = self._sync_edits(client, token)
            if synced:
                summary["alerts"] += synced
                logger.info(f"[Auto] tpsl edit-synced on {synced} coin(s)")
        except Exception as e:
            logger.warning(f"[Auto] edit sync skipped: {e}")
        logger.info(f"[Auto] sims sync: {summary}")
        return summary

    def run_forever(self, token: str = "") -> None:
        logger.info("[Auto] started "
                    f"(live={self.cfg.live}, poll={self.mirror_cfg.poll_s}s)")
        self._ev("INFO", "started", {"live": self.cfg.live})
        try:
            client = self._client()
            self.reconcile_ledger()
            self.bootstrap_from_sims(client, client.access_token or "")
        except Exception as e:
            self._ev("ERROR", "startup_failed", {"error": str(e)[:160]})
            logger.warning(f"[Auto] startup bootstrap skipped: {e}")
        n = 0
        heart_every = max(1, int(3600 / max(self.mirror_cfg.poll_s, 1)))
        while True:
            try:
                self.run_once(token)
                # Sims re-sync every 4th cycle (~60s): picks up already-open
                # coins skipped earlier on caps, closes anything Pariston
                # no longer holds (feed safety net), backfills TP/SL.
                n += 1
                if n % 4 == 0:
                    try:
                        client = self._client()
                        self._sims_sync(client, client.access_token or "")
                    except Exception as e:
                        self._ev("ERROR", "sync_failed",
                                 {"error": str(e)[:160]})
                        logger.warning(f"[Auto] periodic sync skipped: {e}")
                if n % heart_every == 0:
                    self._ev("INFO", "heartbeat",
                             {"open": self.open_count(),
                              "committed_usd": self.committed_usd(),
                              "account_usd": self._account_usd()})
            except Exception as e:
                self._ev("ERROR", "cycle_crashed", {"error": str(e)[:160]})
                logger.warning(f"[Auto] cycle crashed (contained): {e}")
            time.sleep(self.mirror_cfg.poll_s)
