"""Mirror orchestrator: watch Pariston, copy new setups, close the gone.

One cycle (every stage guarded; the loop never dies):
  1. Auth (own session) + read Pariston's open positions (read-only).
  2. New source baseId -> full detail -> brain sizes it -> paper-share
     into the mirror portfolio. Same-coin conflict is adopted, not an error.
  3. Source baseId vanished (= closed/liquidated on Pariston) -> market-
     close the mirror copy/copies. Already-gone counts as closed.
  4. Dry-run: reads + decides like live, posts nothing, marks seen only.

Feed/client factories are injectable so cycles run fully offline in tests.
"""
import time
from typing import Any, Callable, Dict, List, Optional

from src.utils.logger import get_logger

from mirror.brain import decide_size
from mirror.config import MirrorConfig
from mirror.feed import fetch_open_positions, fetch_setup_detail
from mirror.store import MirrorStore

logger = get_logger(__name__)


def _snapshot_bank(snap: Dict[str, Any]) -> Optional[float]:
    for key in ("portfolioRemainingSim", "remainingSim", "remaining_sim"):
        try:
            val = snap.get(key)
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _snapshot_open_count(snap: Dict[str, Any]) -> Optional[int]:
    inv = snap.get("investments")
    if isinstance(inv, list):
        return len(inv)
    return None


class MirrorBot:
    def __init__(
        self,
        cfg: MirrorConfig,
        fetch_positions_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
        fetch_detail_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        client_factory: Optional[Callable[[], Any]] = None,
    ):
        self.cfg = cfg
        self.store = MirrorStore(cfg.data_dir)
        self._fetch_positions = fetch_positions_fn or fetch_open_positions
        self._fetch_detail = fetch_detail_fn or fetch_setup_detail
        self._client_factory = client_factory or self._default_client

    def _default_client(self):
        from src.invo.client import InvoClient
        return InvoClient(email=self.cfg.email, storage_dir=self.cfg.data_dir)

    # ── one cycle ──
    def run_once(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "mirrored": 0, "skipped": 0, "closed": 0, "errors": 0,
        }
        try:
            client = self._client_factory()
            client.ensure_auth(email=self.cfg.email,
                               app_password=self.cfg.app_password,
                               device_id=self.cfg.device_id)
            token = client.access_token or ""
        except Exception as e:
            logger.warning(f"[Mirror] auth failed, cycle skipped: {e}")
            self.store.audit("auth_failed", {"error": str(e)[:200]})
            return {**summary, "errors": 1}

        try:
            pariston = self._fetch_positions(
                client, token, self.cfg.pariston_portfolio_id)
        except Exception as e:
            logger.warning(f"[Mirror] Pariston unreadable, cycle skipped: {e}")
            return {**summary, "errors": 1, "feed_error": str(e)[:160]}
        live_ids = {p["baseId"] for p in pariston if p.get("baseId")}

        bank: Optional[float] = None
        remote_open: Optional[int] = None
        if not self.cfg.dry_run:
            try:
                snap = client.list_paper_investments(token, self.cfg.portfolio_id)
                snap = snap if isinstance(snap, dict) else {}
                bank = _snapshot_bank(snap)
                remote_open = _snapshot_open_count(snap)
            except Exception as e:
                logger.warning(f"[Mirror] mirror-bank unreadable, shares "
                               f"skipped this cycle: {e}")
                summary["errors"] += 1

        for pos in pariston:
            sid = str(pos.get("baseId") or "")
            if not sid or sid in self.store.seen_ids():
                continue
            try:
                self._mirror_one(client, token, pos, bank, remote_open, summary)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"[Mirror] copy failed for "
                               f"{pos.get('ticker')}: {e}")

        if not self.cfg.dry_run:
            try:
                self._close_vanished(client, token, live_ids, summary)
            except Exception as e:
                summary["errors"] += 1
                logger.warning(f"[Mirror] close pass failed: {e}")

        logger.info(f"[Mirror] cycle: {summary}")
        return summary

    # ── copy one setup ──
    def _mirror_one(self, client, token: str, pos: Dict[str, Any],
                    bank: Optional[float], remote_open: Optional[int],
                    summary: Dict[str, Any]) -> None:
        sid = str(pos["baseId"])
        ticker = str(pos.get("ticker") or "?")
        if self.cfg.dry_run:
            try:
                setup = self._fetch_detail(client, token, sid)
            except Exception:
                setup = {}
            summary["mirrored"] += 1
            self.store.mark_seen(sid)
            self.store.audit("copy_dry_run", {"source": sid, "ticker": ticker,
                                              "size": self.cfg.base_sim,
                                              "setup": setup})
            logger.info(f"[Mirror DRY-RUN] would copy {ticker} "
                        f"size={self.cfg.base_sim}")
            return

        try:
            setup = self._fetch_detail(client, token, sid)
        except Exception as e:
            summary["skipped"] += 1
            self.store.mark_seen(sid)
            self.store.audit("detail_failed", {"source": sid, "error": str(e)[:160]})
            logger.info(f"[Mirror] detail unreadable for {ticker}, skipped")
            return
        if not setup.get("symbol"):
            summary["skipped"] += 1
            self.store.mark_seen(sid)
            self.store.audit("detail_empty", {"source": sid, "ticker": ticker})
            logger.info(f"[Mirror] empty detail for {ticker}, skipped")
            return

        open_count = (remote_open if remote_open is not None
                      else len(self.store.open_positions()))
        size, reason = decide_size(bank, open_count,
                                   base_size=self.cfg.base_sim,
                                   max_open=self.cfg.max_open,
                                   max_risk_pct=self.cfg.max_risk_pct)
        if size is None:
            summary["skipped"] += 1
            self.store.mark_seen(sid)
            self.store.audit("copy_skipped", {"source": sid, "ticker": ticker,
                                              "reason": reason})
            logger.info(f"[Mirror] skipped {ticker} ({sid}): {reason}")
            return

        from src.invo.client import InvoConflictError
        try:
            out = client.share_paper_trade(
                {"symbol": setup["symbol"], "side": setup["side"],
                 "leverage": setup.get("leverage", 10),
                 "stop_loss": setup.get("stop_loss"),
                 "entry": setup.get("entry")},
                token=token, portfolio_id=self.cfg.portfolio_id,
                entry_sim=size, price_target=setup.get("tp1"), dry_run=False)
            resp = out.get("response") or {}
            self.store.record_mirror(sid, {"symbol": setup["symbol"],
                                           "side": setup["side"],
                                           "mirrorBaseIds": resp.get("baseIds", []),
                                           "size": size})
            summary["mirrored"] += 1
            self.store.audit("copied", {"source": sid, "ticker": ticker,
                                        "size": size, "reason": reason,
                                        "remainingSim": resp.get("remainingSim")})
            logger.info(f"[Mirror] copied {ticker} size={size} ({reason})")
        except InvoConflictError as e:
            self.store.record_mirror(sid, {"symbol": setup.get("symbol", ticker),
                                           "side": setup.get("side", "?"),
                                           "mirrorBaseIds": e.conflicting_base_ids,
                                           "size": size, "already_open": True})
            summary["skipped"] += 1
            self.store.audit("copy_conflict", {"source": sid, "ticker": ticker,
                                               "baseIds": e.conflicting_base_ids})
            logger.info(f"[Mirror] already open, adopted {ticker}: {e}")
        except Exception as e:
            if "401" in str(e):
                client.access_token = None
            raise

    # ── close what Pariston closed ──
    def _close_vanished(self, client, token: str, live_ids: set,
                        summary: Dict[str, Any]) -> None:
        from src.invo.client import err_is_gone
        targets = [sid for sid in self.store.open_positions()
                   if sid not in live_ids]
        if not targets:
            return
        for sid in targets:
            pos = self.store.open_positions()[sid]
            ok = True
            for bid in pos.get("mirrorBaseIds", []):
                try:
                    client.close_paper_trade(str(bid), token=token, dry_run=False)
                    self.store.audit("closed", {"source": sid, "baseId": bid})
                except Exception as e:
                    if err_is_gone(e):
                        self.store.audit("close_already_gone",
                                         {"source": sid, "baseId": bid})
                    else:
                        ok = False
                        summary["errors"] += 1
                        logger.warning(f"[Mirror] close failed {bid}: {e}")
            if ok:
                self.store.mark_closed(sid)
                summary["closed"] += 1
                logger.info(f"[Mirror] closed {pos.get('symbol')} "
                            f"(Pariston closed {sid})")

    # ── loop ──
    def run_forever(self) -> None:
        logger.info("[Mirror] started "
                    f"(dry_run={self.cfg.dry_run}, poll={self.cfg.poll_s}s, "
                    f"base={self.cfg.base_sim}, max_open={self.cfg.max_open}, "
                    f"watch={self.cfg.pariston_portfolio_id})")
        while True:
            try:
                self.run_once()
            except Exception as e:  # last-resort guard: the loop never dies
                logger.warning(f"[Mirror] cycle crashed (contained): {e}")
            time.sleep(self.cfg.poll_s)
