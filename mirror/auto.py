"""Auto-mimic executor: the hands that tap confirm in the signed session.

Why this exists: Pariston entries/updates/closes are readable from any
login, but the confirm signature is device-bound (turnkey key lives in
the operator's signed-in browser). So execution drives THAT browser via
CDP — nothing here can sign on its own, which is also the safety story.

Loop (per new Pariston setup):
  watcher detects -> brain sizes -> driver opens the mimic dialog ->
  sets size -> verifies the dialog actually opened -> confirms (live
  only) -> captures POST /dex/position/create and audits the payload.

Safety (all hard, all env-tunable):
  MIMIC_LIVE=false default (dry-run: navigate + screenshot, never swipe).
  MAX_USD_PER_MIMIC / MAX_PCT_PER_MIMIC / MAX_CONCURRENT / MIN_REMAINING_USD.
  Post-confirm the recorded payload is audited; a non-success response
  raises and alerts instead of silently passing.
  Updates/closes of source setups are ALERTS in v1 (adapters stubbed);
  their dialogs get mapped in a supervised session before enabling.

Requires on this machine: Chrome running with
  --remote-debugging-port=9222  (same profile the operator trades in),
plus `pip install playwright` (attach only — no browser download needed).
"""
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

from mirror.brain import decide_size
from mirror.config import MirrorConfig
from mirror.feed import fetch_open_positions, fetch_setup_detail
from mirror.store import MirrorStore

logger = get_logger(__name__)

MIMIC_URL = ("https://app.invoapp.com/portfolio/{portfolio}/investment/"
             "{base_id}/mimic_position")


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
    cdp_url: str = "http://localhost:9222"
    live: bool = False
    account_usd: float = 18.38
    max_usd_per_mimic: float = 5.0
    max_pct_per_mimic: float = 25.0
    max_concurrent: int = 2
    min_remaining_usd: float = 3.0
    dialog_timeout_s: int = 30

    @classmethod
    def from_env(cls) -> "MimicConfig":
        return cls(
            cdp_url=os.getenv("MIMIC_CDP_URL", "http://localhost:9222"),
            live=os.getenv("MIMIC_LIVE", "false").lower() == "true",
            account_usd=_f("MIMIC_ACCOUNT_USD", 18.38),
            max_usd_per_mimic=_f("MIMIC_MAX_USD", 5.0),
            max_pct_per_mimic=_f("MIMIC_MAX_PCT", 25.0),
            max_concurrent=_i("MIMIC_MAX_CONCURRENT", 2),
            min_remaining_usd=_f("MIMIC_MIN_REMAINING_USD", 3.0),
            dialog_timeout_s=_i("MIMIC_DIALOG_TIMEOUT_S", 30),
        )


def decide_live_size(account_usd: float, committed_usd: float,
                     cfg: MimicConfig) -> tuple:
    """(margin_usd | None, reason). Real-money brain: flat cap + guards.

    Never risks more than max_pct of the account or max_usd flat, never
    drops remaining below the floor, never exceeds max concurrent
    (checked by caller via open count).
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


def size_pct(margin_usd: float, account_usd: float) -> float:
    """Dialog slider percent for a margin target."""
    if account_usd <= 0:
        return 0.0
    return round(min(100.0, margin_usd / account_usd * 100.0), 1)


class ChromeDriver:
    """CDP-attached driver for the operator's signed-in Chrome.

    page_factory is injectable (tests pass a fake page; live uses
    playwright's sync API attached to MIMIC_CDP_URL). Coordinates are
    viewport fractions, calibrated in a supervised session.
    """

    # Calibrated against the 1280-wide mimic dialog (supervised pass may
    # adjust via env MIMIC_SIZE_X/Y, MIMIC_SWIPE_*).
    SIZE_XY = (0.83, 0.633)
    SWIPE_FROM = (0.68, 0.895)
    SWIPE_TO = (0.965, 0.895)

    def __init__(self, cdp_url: str, page_factory=None):
        self.cdp_url = cdp_url
        self._page_factory = page_factory or self._live_page
        self._pw = None

    def _live_page(self, url: str):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.goto(url)
        return page

    def close(self):
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass

    def mimic_once(self, portfolio_id: str, base_id: str, pct: float,
                   live: bool, timeout_s: int = 30) -> Dict[str, Any]:
        """Open the mimic dialog, set size, confirm iff live.

        Returns {"confirmed": bool, "shots": {...}, "record": {...}} where
        record is the captured /dex/position/create payload+result.
        Raises on dialog failure or non-success confirm.
        """
        url = MIMIC_URL.format(portfolio=portfolio_id, base_id=base_id)
        page = self._page_factory(url)
        try:
            page.wait_for_timeout(4000)
            before = page.screenshot()
            vw, vh = page.viewport_size["width"], page.viewport_size["height"]
            sx, sy = self.SIZE_XY
            page.mouse.click(int(vw * sx), int(vh * sy))
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.type(str(pct))
            page.wait_for_timeout(800)
            after = page.screenshot()
            if after == before:
                raise RuntimeError("size field did not change (dialog missing?)")
            if not live:
                return {"confirmed": False, "shots": {"before": len(before),
                                                       "after": len(after)},
                        "record": {}}
            fx, fy = self.SWIPE_FROM
            tx, ty = self.SWIPE_TO
            page.mouse.move(int(vw * fx), int(vh * fy))
            page.mouse.down()
            page.mouse.move(int(vw * tx), int(vh * ty), steps=25)
            page.mouse.up()
            try:
                resp = page.expect_response("**/dex/position/create",
                                            timeout=timeout_s * 1000)
                result = resp.value.json()
            except Exception as e:
                raise RuntimeError(f"no position/create response: {e}")
            if not result.get("success"):
                raise RuntimeError(f"confirm rejected: {str(result)[:200]}")
            return {"confirmed": True,
                    "shots": {"before": len(before), "after": len(after)},
                    "record": result.get("data", {})}
        finally:
            try:
                page.close()
            except Exception:
                pass


class AutoBot:
    """Watcher + brain + executor. Loop never dies; money moves only when
    live=True AND every guard passes AND the dialog confirms."""

    def __init__(self, mirror_cfg: MirrorConfig, mimic_cfg: MimicConfig,
                 driver: Optional[ChromeDriver] = None,
                 fetch_positions_fn=None, fetch_detail_fn=None,
                 client_factory=None, notify_fn=None):
        self.mirror_cfg = mirror_cfg
        self.cfg = mimic_cfg
        self.store = MirrorStore(mirror_cfg.data_dir)
        self.driver = driver or ChromeDriver(mimic_cfg.cdp_url)
        self._fetch_positions = fetch_positions_fn or fetch_open_positions
        self._fetch_detail = fetch_detail_fn or fetch_setup_detail
        self._client_factory = client_factory or self._default_client
        self._notify_fn = notify_fn or (lambda t: logger.info(f"[Auto] {t}"))

    def _default_client(self):
        from src.invo.client import InvoClient
        return InvoClient(email=self.mirror_cfg.email,
                          storage_dir=self.mirror_cfg.data_dir)

    def _notify(self, text: str) -> None:
        self.store.audit("notify", {"text": text[:300]})
        try:
            self._notify_fn(text)
        except Exception as e:
            logger.warning(f"[Auto] notify sink failed: {e}")

    def committed_usd(self) -> float:
        """Sum of open mimicked margins from the audit ledger."""
        total = 0.0
        for e in self.store.recent_audit(5000):
            if e.get("event") == "mimicked":
                try:
                    total += float((e.get("detail") or {}).get("margin_usd", 0))
                except (TypeError, ValueError):
                    continue
            if e.get("event") == "mimic_closed":
                try:
                    total -= float((e.get("detail") or {}).get("margin_usd", 0))
                except (TypeError, ValueError):
                    continue
        return max(0.0, round(total, 2))

    def open_count(self) -> int:
        return sum(1 for e in self.store.recent_audit(5000)
                   if e.get("event") == "mimicked") - sum(
                   1 for e in self.store.recent_audit(5000)
                   if e.get("event") == "mimic_closed")

    def run_once(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"mimicked": 0, "skipped": 0,
                                   "alerts": 0, "errors": 0}
        try:
            client = self._client_factory()
            client.ensure_auth(email=self.mirror_cfg.email,
                               app_password=self.mirror_cfg.app_password,
                               device_id=self.mirror_cfg.device_id)
            token = client.access_token or ""
            positions = self._fetch_positions(
                client, token, self.mirror_cfg.pariston_portfolio_id)
        except Exception as e:
            logger.warning(f"[Auto] cycle skipped: {e}")
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
            try:
                self._mimic_one(client, token, pos, summary)
            except Exception as e:
                summary["errors"] += 1
                self._notify(f"MIMIC FAILED {pos.get('ticker')}: {e}")
                logger.warning(f"[Auto] mimic failed: {e}")
        # Closes / updates of source setups: v1 alerts (adapters stubbed).
        for sid in self.store.seen_ids() - live_ids - \
                self.store.reported_closed_ids():
            self.store.mark_reported_closed(sid)
            summary["alerts"] += 1
            self._notify(f"Pariston closed {sid} — close your copy in the app "
                         f"(auto-close lands in v2).")
        logger.info(f"[Auto] cycle: {summary}")
        return summary

    def _mimic_one(self, client, token: str, pos: Dict[str, Any],
                   summary: Dict[str, Any]) -> None:
        sid = str(pos["baseId"])
        ticker = str(pos.get("ticker") or "?")
        try:
            setup = self._fetch_detail(client, token, sid)
        except Exception:
            setup = {}
        if self.open_count() >= self.cfg.max_concurrent:
            summary["skipped"] += 1
            self._notify(f"SKIP {ticker}: max-concurrent-{self.cfg.max_concurrent}")
            return
        margin, reason = decide_live_size(self.cfg.account_usd,
                                          self.committed_usd(), self.cfg)
        if margin is None:
            summary["skipped"] += 1
            self._notify(f"SKIP {ticker}: {reason}")
            return
        pct = size_pct(margin, self.cfg.account_usd)
        self._notify(f"Pariston {ticker} {pos.get('side')} {pos.get('leverage')}x "
                     f"-> mimic ${margin} ({pct}%) [{reason}] "
                     f"{'LIVE' if self.cfg.live else 'DRY-RUN'}")
        try:
            out = self.driver.mimic_once(
                self.mirror_cfg.pariston_portfolio_id, sid, pct,
                self.cfg.live, self.cfg.dialog_timeout_s)
        except Exception as e:
            summary["errors"] += 1
            raise RuntimeError(f"dialog failed: {e}")
        if out.get("confirmed"):
            summary["mimicked"] += 1
            self.store.audit("mimicked", {
                "source": sid, "ticker": ticker,
                "side": pos.get("side"), "leverage": pos.get("leverage"),
                "margin_usd": margin, "pct": pct,
                "tradeId": (out.get("record") or {}).get("tradeId")})
            self._notify(f"MIMICKED {ticker} ${margin} "
                         f"trade={(out.get('record') or {}).get('tradeId')}")
        else:
            summary["skipped"] += 1
            self.store.audit("mimic_dry_run", {"source": sid, "ticker": ticker,
                                               "margin_usd": margin, "pct": pct})

    def run_forever(self) -> None:
        logger.info("[Auto] started "
                    f"(live={self.cfg.live}, poll={self.mirror_cfg.poll_s}s)")
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.warning(f"[Auto] cycle crashed (contained): {e}")
            time.sleep(self.mirror_cfg.poll_s)
