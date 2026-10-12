import os
import time
import json
import signal
import sys
import argparse
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.pipeline import SignalPipeline
from src.models.signal import SignalModel
from src.utils.telegram_formatter import TelegramFormatter
from src.utils.telegram_bot import TelegramBot
from src.data.persistence import SignalPersistence
from src.config import CONFIG, AppConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _ticker_to_symbol(ticker: str) -> str:
    """Map an Invo sims ticker ('ZEC') to a signal symbol ('ZECUSDT').

    Our universe is USDT-quoted; a bare coin gets the suffix, an already
    qualified ticker passes through. Empty in, empty out.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    return t if t.endswith(("USDT", "USDC")) else t + "USDT"


class BotScheduler:
    """
    Lightweight local scheduler per PRD §28 and Phase 6.
    Runs 15m candidate refreshes and daily deep scans.
    Features:
    - Zero paid cloud services required (pure Python standard library).
    - Duplicate signal prevention via state ledger.
    - Exponential backoff & retry for transient API failures.
    - Graceful shutdown handling.
    """

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        pipeline: Optional[SignalPipeline] = None,
        cooldown_hours: float = 6.0,
        state_file: str = "data_store/scheduler_state.json",
    ):
        self.config = config or CONFIG
        self.pipeline = pipeline or SignalPipeline(config=self.config)
        self.cooldown_hours = cooldown_hours
        self.state_file = state_file
        self.bot = TelegramBot()
        self.persistence = SignalPersistence()
        self.is_running = False

        state_dir = os.path.dirname(self.state_file)
        if state_dir and not os.path.exists(state_dir):
            os.makedirs(state_dir, exist_ok=True)

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read scheduler state: {e}")
        return {"published_signals": {}}

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save scheduler state: {e}")

    def is_duplicate(self, symbol: str, side: str, state: Dict[str, Any]) -> bool:
        """Checks if a signal for the same symbol & side was published within cooldown."""
        key = f"{symbol}_{side}"
        published_map = state.get("published_signals", {})
        if key in published_map:
            last_ts_str = published_map[key]
            last_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            elapsed_hours = (now_dt - last_dt).total_seconds() / 3600.0
            if elapsed_hours < self.cooldown_hours:
                logger.info(
                    f"[Scheduler] Suppressing duplicate for {symbol} {side} (elapsed: {elapsed_hours:.1f}h < {self.cooldown_hours}h)"
                )
                return True
        return False

    def run_scan_cycle(
        self,
        deep_scan: bool = False,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> List[SignalModel]:
        """
        Executes one complete scanning cycle with retry logic for transient errors.
        Prevents duplicate signal publication within cooldown window.
        """
        scan_type = "DEEP SCAN" if deep_scan else "15M REFRESH"
        logger.info(f"[Scheduler] Starting {scan_type}...")

        candidates = None
        for attempt in range(1, max_retries + 1):
            try:
                candidates = self.pipeline.run_pipeline()
                break
            except Exception as e:
                logger.warning(
                    f"[Scheduler] Scan attempt {attempt}/{max_retries} failed: {e}"
                )
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)
                else:
                    logger.error(f"[Scheduler] Max retries reached for {scan_type}. Aborting cycle.")
                    self._record_scan(scan_type, status="failed", candidates=0, published=0)
                    return []

        if not candidates:
            logger.info(f"[Scheduler] {scan_type} yielded 0 signals. Quality over quantity.")
            self._record_scan(scan_type, status="ok", candidates=0, published=0)
            # Maintenance still runs on quiet cycles: open positions need
            # outcome tracking, supervision, and Invo sync even when nothing
            # new publishes. (Nothing to share — share hook takes published.)
            self._track_outcomes()
            self._supervise_exits()
            self._sync_exits_to_invo()
            return []

        state = self._load_state()
        published_signals = []

        for cand_dict in candidates:
            try:
                signal_obj = SignalModel(**cand_dict)
                symbol = signal_obj.symbol
                side = signal_obj.side

                # Check duplicate suppression
                if self.is_duplicate(symbol, side, state):
                    continue

                # 1. Persist
                self.persistence.save_signal(signal_obj)

                # 2. Format
                rank = len(published_signals) + 1
                msg = TelegramFormatter.format_signal(signal_obj, rank=rank)

                # 3. Broadcast
                self.bot.send_message(msg)

                # 4. Update state ledger
                state["published_signals"][f"{symbol}_{side}"] = signal_obj.timestamp
                published_signals.append(signal_obj)

            except Exception as e:
                logger.error(f"[Scheduler] Failed to process candidate {cand_dict.get('symbol')}: {e}")

        self._save_state(state)
        logger.info(
            f"[Scheduler] {scan_type} complete. Published {len(published_signals)} new signal(s)."
        )
        self._record_scan(
            scan_type,
            status="ok",
            candidates=len(candidates or []),
            published=len(published_signals),
        )
        self._track_outcomes()
        self._share_paper_to_invo(published_signals)
        self._supervise_exits()
        self._sync_exits_to_invo()
        return published_signals

    def _share_paper_to_invo(self, published: list) -> None:  # noqa: C901 (gated bridge)
        """Auto-share published signals as Invo PAPER trades (no real money).

        Mapping verified live: entrySim = position size in sim dollars
        (INVO_SIM_SIZE, default 10.0); priceTarget/stopLoss = absolute prices.
        Gate: INVO_PAPER_ENABLED=true. Auth is autonomous: the client reuses
        its token, rotates silently, else OTP self-logins via Gmail IMAP.
        Needs INVO_EMAIL + INVO_GMAIL_APP_PASSWORD + INVO_PORTFOLIO_ID.
        """
        if os.getenv("INVO_PAPER_ENABLED", "false").lower() != "true":
            return
        try:
            from src.invo.client import InvoClient
            dry = os.getenv("INVO_PAPER_DRY_RUN", "true").lower() != "false"
            email = os.getenv("INVO_EMAIL", "")
            app_password = os.getenv("INVO_GMAIL_APP_PASSWORD", "")
            portfolio = os.getenv("INVO_PORTFOLIO_ID", "")
            if not dry and (not email or not app_password or not portfolio):
                logger.warning("[Scheduler] Invo paper-share skipped: INVO_EMAIL/INVO_GMAIL_APP_PASSWORD/INVO_PORTFOLIO_ID missing")
                return
            from src.invo.client import InvoConflictError, record_share
            try:
                sim_size = float(os.getenv("INVO_SIM_SIZE", "10.0"))
            except ValueError:
                sim_size = 10.0
            client = InvoClient(email=email)
            if not dry:
                client.ensure_auth(email=email, app_password=app_password,
                                   device_id=os.getenv("INVO_DEVICE_ID", ""))
                token = client.access_token or ""
                # Local conflict screen (zero requests): skip coins we already
                # shared while their outcome is still open.
                import json as _json
                try:
                    with open("data_store/outcomes.json", encoding="utf-8") as _f:
                        _outcomes = _json.load(_f)
                        if not isinstance(_outcomes, dict):
                            _outcomes = {}
                except (FileNotFoundError, ValueError, OSError):
                    _outcomes = {}
                # Ground truth (one cheap read): the coins actually open in
                # the portfolio right now. A non-final outcome must NOT block
                # a share when Invo shows the slot empty — externally closed
                # (or bot-closed) positions never finalize in the tracker,
                # and the old screen blocked their coin for up to 24h
                # (seen live: DOGE #8 skipped on a LONG closed 21h earlier).
                # Unreadable -> None -> legacy ledger-only screen.
                try:
                    _snap = client.list_paper_investments(token, portfolio)
                    _invs = (_snap.get("investments", [])
                             if isinstance(_snap, dict) else [])
                    invo_open = {_ticker_to_symbol(str(_i.get("ticker") or ""))
                                 for _i in _invs if isinstance(_i, dict)}
                    invo_open.discard("")
                except Exception as _e:
                    logger.warning("[Scheduler] Invo presence unreadable, "
                                   f"ledger screen only: {_e}")
                    invo_open = None
                just_shared: set = set()
            else:
                token = ""
                _outcomes = {}
                invo_open = None
                just_shared = set()
            for sig in published:
                try:
                    if not dry:
                        if sig.symbol in just_shared:
                            logger.info(f"[Scheduler] Invo share skipped (shared earlier this cycle): {sig.symbol}")
                            continue
                        if invo_open is not None:
                            if sig.symbol in invo_open:
                                logger.info(f"[Scheduler] Invo share skipped (open in portfolio): {sig.symbol}")
                                continue
                        elif client.already_shared_open(sig.symbol, portfolio, _outcomes):
                            logger.info(f"[Scheduler] Invo share skipped (open position already shared): {sig.symbol}")
                            continue
                    out = client.share_paper_trade(
                        {"symbol": sig.symbol, "side": sig.side,
                         "leverage": sig.leverage, "stop_loss": sig.stop_loss,
                         "entry": sig.entry},
                        token=token, portfolio_id=portfolio,
                        entry_sim=sim_size,
                        price_target=float(sig.tp),
                        dry_run=dry,
                    )
                    if not out.get("dry_run"):
                        record_share("data_store", getattr(sig, "signal_id", "?"), {
                            "baseIds": (out.get("response") or {}).get("baseIds", []),
                            "remainingSim": (out.get("response") or {}).get("remainingSim"),
                            "symbol": sig.symbol,
                            "side": sig.side,
                            "portfolioId": portfolio,
                        })
                        # Observational mirror only: record the sim commitment
                        # for reconciliation. Never gates sharing (server is
                        # the enforcer); failures here must not break shares.
                        try:
                            from src.invo.sim_ledger import SimLedger
                            SimLedger(storage_dir="data_store").reserve(
                                getattr(sig, "signal_id", "?"), sig.symbol,
                                sim_size)
                        except Exception as re:
                            logger.warning(f"[Scheduler] Sim reserve skipped: {re}")
                        just_shared.add(sig.symbol)
                except InvoConflictError as e:
                    # Setup already shared (same coin open) — not an error.
                    logger.info(f"[Scheduler] Invo share skipped (already open): {e}")
                    just_shared.add(sig.symbol)
                    record_share("data_store", getattr(sig, "signal_id", "?"), {
                        "baseIds": e.conflicting_base_ids,
                        "already_open": True,
                        "symbol": sig.symbol,
                        "side": sig.side,
                        "portfolioId": portfolio,
                    })
                    # The sim is committed remotely (by whoever opened it):
                    # mirror it so reconcile() doesn't cry wolf. Advisory.
                    try:
                        from src.invo.sim_ledger import SimLedger
                        SimLedger(storage_dir="data_store").reserve(
                            getattr(sig, "signal_id", "?"), sig.symbol,
                            sim_size, note="already-open-remote")
                    except Exception as re:
                        logger.warning(f"[Scheduler] Sim reserve skipped: {re}")
                except Exception as e:
                    # Auth death clears the token so the next cycle OTP
                    # self-logins fresh instead of 401-looping forever.
                    if "401" in str(e):
                        client.access_token = None
                    logger.warning(f"[Scheduler] Invo paper share skipped for {sig.symbol}: {e}")
        except Exception as e:
            logger.warning(f"[Scheduler] Invo paper share skipped: {e}")

    def _supervise_exits(self) -> None:
        """Run Exit Orchestrator supervision over open/pending setups.

        Paper-only and monitor-first: with EXITS_APPLY=false (default)
        decisions are recorded to the exit-decision ledger without changing
        any plan. Must never break scans.
        """
        if os.getenv("EXITS_ENABLED", "true").lower() != "true":
            return
        try:
            from src.exits.monitor import ExitMonitor
            # Reuse the pipeline's public-data fetcher: the exits package
            # never constructs network/trading clients itself (paper-only).
            monitor = ExitMonitor(fetcher=getattr(self.pipeline, "fetcher", None))
            summary = monitor.run_cycle()
            logger.info(f"[Scheduler] Exit supervision: {summary}")
        except Exception as e:
            logger.warning(f"[Scheduler] Exit supervision skipped: {e}")

    def _sync_exits_to_invo(self) -> None:
        """Propagate APPLIED ExitOrchestrator decisions to Invo paper positions.

        TP + SL only: the modify payload carries the active plan's tp as
        priceTarget. CLOSE fires on
        applied supervisor-close events. REDUCE / FLAG / INVALIDATE have no
        investment-level representation and are logged, never posted.
        Gated on INVO_PAPER_ENABLED + live (not dry-run) + EXITS_APPLY, so
        only risk-validated, actually-applied plans move paper state.
        State-based and idempotent via data_store/invo_exit_sync.json:
        a repeat run with no new applied decisions posts nothing.
        Must never break scans.
        """
        if os.getenv("INVO_PAPER_ENABLED", "false").lower() != "true":
            return
        try:
            from src.exits.ledger import ExitDecisionLedger
            from src.invo import exit_sync as _sync
            from src.invo.client import InvoClient, load_share_ledger
            dry = os.getenv("INVO_PAPER_DRY_RUN", "true").lower() != "false"
            apply = os.getenv("EXITS_APPLY", "false").lower() == "true"
            if dry or not apply:
                return
            email = os.getenv("INVO_EMAIL", "")
            app_password = os.getenv("INVO_GMAIL_APP_PASSWORD", "")
            portfolio = os.getenv("INVO_PORTFOLIO_ID", "")
            if not (email and app_password and portfolio):
                logger.warning("[Scheduler] Invo exit-sync skipped: INVO_EMAIL/INVO_GMAIL_APP_PASSWORD/INVO_PORTFOLIO_ID missing")
                return
            client = InvoClient(email=email)
            client.ensure_auth(email=email, app_password=app_password,
                               device_id=os.getenv("INVO_DEVICE_ID", ""))
            token = client.access_token or ""
            # Single-flight across overlapping deployments: fail OPEN (skip
            # this cycle with an info line), never deadlock the scan loop.
            from src.invo.volume_lock import acquire_lock, release_lock
            _owner = os.getenv("RAILWAY_DEPLOYMENT_ID", f"pid-{os.getpid()}")
            _lock = acquire_lock("data_store", "invo-exit-sync", _owner,
                                 stale_after_s=300)
            if _lock is None:
                logger.info("[Scheduler] Invo exit-sync skipped: contended lock "
                            "(sibling worker active?)")
                return
            try:
                self._run_exit_sync_locked(client, token, portfolio, _owner)
            finally:
                release_lock("data_store", "invo-exit-sync", _lock)
        except Exception as e:
            logger.warning(f"[Scheduler] Invo exit-sync skipped: {e}")

    def _run_exit_sync_locked(self, client, token: str, portfolio: str,
                              worker: str) -> None:
        """Sync body: runs only while holding the volume lock. See
        _sync_exits_to_invo for the contract (TP1+SL only, idempotent)."""
        import time as _time
        from src.exits.ledger import ExitDecisionLedger
        from src.invo import exit_sync as _sync
        from src.invo.client import load_share_ledger
        from src.invo.sim_ledger import SimLedger
        now_ms = lambda: int(_time.time() * 1000)
        ledger = ExitDecisionLedger()
        state = _sync.load_sync_state()
        tracker = _sync.SyncTracker(
            state, now_ms_fn=now_ms,
            save_fn=lambda s: _sync.save_sync_state("data_store", s))
        tracker.prune()  # bounded record growth; terminal-only, 30d TTL
        sim = SimLedger(storage_dir="data_store")
        shares = load_share_ledger()
        actions = _sync.compute_sync_actions(
            ledger.active_plans(), ledger.close_signal_ids(),
            shares, state)
        summary = _sync.summarize_sync(
            ledger.active_plans(), shares, state)
        logger.info(f"[Scheduler] Invo exit-sync reconcile: "
                    f"{summary['in_sync']} in-sync, {summary['unsynced']} pending, "
                    f"{summary['missing_base']} untargetable, "
                    f"{summary['closed_skipped']} closed-skipped.")
        if not actions:
            self._report_sim_reconcile(client, token, portfolio, sim)
            return
        done_modify, done_close = 0, 0
        _presence = {}  # one bounded remote read per cycle, shared by closes
        for a in actions:
            sid, kind = a["signal_id"], a["kind"]
            prev = state["synced_levels"].get(sid, {}) or {}
            gate = tracker.gate(sid, kind,
                                plan_version=a.get("plan_version"),
                                levels={"tp": a.get("price_target"),
                                        "stop_loss": a.get("stop_loss")}
                                if kind == "modify" else None)
            if not gate["proceed"]:
                self._audit(sid, kind, "suppressed", gate, worker, now_ms(),
                            prev=prev)
                continue
            tracker.note_attempt(sid, kind)
            try:
                if kind == "modify":
                    for bid in a["base_ids"]:
                        client.update_paper_trade(
                            bid, price_target=a["price_target"],
                            stop_loss=a["stop_loss"], token=token,
                            dry_run=False)
                    state["synced_levels"][sid] = {
                        "stop_loss": a["stop_loss"], "tp": a["price_target"]}
                    tracker.note_result(sid, kind, "confirmed", {
                        "plan_version": a.get("plan_version"),
                        "levels": gate["levels"]})
                    done_modify += 1
                    self._audit(sid, kind, "confirmed", gate, worker, now_ms(),
                                confirmer="authoritative-response", action=a,
                                prev=prev)
                else:
                    # Reconcile-before-retry: a timed-out earlier sell may
                    # already have closed the position. One bounded list read
                    # per cycle (cached); absent legs are never re-sold.
                    present = self._close_presence(
                        client, token, portfolio, a["base_ids"], _presence)
                    if present is not None:
                        _presence["checked"] = True
                        gone_legs = [b for b in a["base_ids"] if b not in present]
                        live_legs = [b for b in a["base_ids"] if b in present]
                        if gone_legs and not live_legs:
                            if sid not in state["closed"]:
                                state["closed"].append(sid)
                            sim.mark_gone(sid, "remote-absence-on-reconcile")
                            tracker.note_result(sid, kind, "confirmed", {
                                "converged": "already-gone"})
                            logger.info(f"[Scheduler] Invo exit-sync close for {sid}: "
                                        f"already gone server-side, stop retrying.")
                            self._audit(sid, kind, "reconciled", gate, worker, now_ms(),
                                        confirmer="remote-readback", action=a,
                                        prev=prev)
                            continue
                        a = {**a, "base_ids": live_legs}
                    for bid in a["base_ids"]:
                        client.close_paper_trade(bid, token=token, dry_run=False)
                    if sid not in state["closed"]:
                        state["closed"].append(sid)
                    sim.release(sid, credit=None, note="sync-close")
                    tracker.note_result(sid, kind, "confirmed", {})
                    done_close += 1
                    self._audit(sid, kind, "confirmed", gate, worker, now_ms(),
                                confirmer="authoritative-response", action=a,
                                prev=prev)
            except Exception as e:
                if "401" in str(e):
                    client.access_token = None
                cls = _sync.classify_sync_error(e)
                if cls["kind"] in ("already-gone", "already-applied"):
                    if sid not in state["closed"] and cls["kind"] == "already-gone":
                        state["closed"].append(sid)
                    if cls["kind"] == "already-gone":
                        sim.mark_gone(sid, str(e)[:200])
                    tracker.note_result(sid, kind, "confirmed", {
                        "converged": cls["kind"]})
                    logger.info(f"[Scheduler] Invo exit-sync {kind} for {sid}: "
                                f"{cls['kind']} — reconciled, stop retrying.")
                    self._audit(sid, kind, "reconciled", gate, worker, now_ms(),
                                confirmer="authoritative-response", action=a,
                                error=str(e)[:200], prev=prev)
                    continue
                if not cls["retryable"]:
                    tracker.note_result(sid, kind, "permanent",
                                        {"error": f"{cls['kind']}: {e}"})
                    logger.warning(f"[Scheduler] Invo exit-sync {kind} for {sid}: "
                                   f"permanent ({cls['kind']}), stop retrying: {e}")
                    self._audit(sid, kind, "failed", gate, worker, now_ms(),
                                action=a, error=f"{cls['kind']}: {e}", prev=prev)
                    continue
                tracker.note_result(sid, kind, "retryable",
                                    {"error": f"{cls['kind']}: {e}",
                                     "retry_after_s": cls.get("retry_after_s")})
                logger.warning(f"[Scheduler] Invo exit-sync {kind} for {sid}: "
                               f"retryable ({cls['kind']}), backing off: {e}")
                self._audit(sid, kind, "retry_scheduled", gate, worker, now_ms(),
                            action=a, error=f"{cls['kind']}: {e}", prev=prev)
        _sync.save_sync_state("data_store", state)
        if tracker.audit_gap:
            logger.warning("[Scheduler] Invo exit-sync: tracker persistence degraded.")
        logger.info(f"[Scheduler] Invo exit-sync: {done_modify} modified, {done_close} closed.")
        self._report_sim_reconcile(client, token, portfolio, sim)

    @staticmethod
    def _close_presence(client, token, portfolio, base_ids, cache):
        """Set of baseIds the server still shows as open, or None when the
        read fails/is ambiguous (caller then proceeds as before — a duplicate
        sell reconciles via the gone path, never silently assumed)."""
        if "present" not in cache:
            try:
                snap = client.list_paper_investments(token, portfolio)
                invs = snap.get("investments") or []
                cache["present"] = {i.get("baseId") for i in invs
                                    if isinstance(i, dict) and i.get("baseId")}
            except Exception as e:
                logger.warning(f"[Scheduler] Invo close-presence check skipped: {e}")
                cache["present"] = None
        return cache["present"]

    @staticmethod
    def _audit(sid, kind, event, gate, worker, now_ms, confirmer="unverified",
               action=None, error="", prev=None):
        """Best-effort audit emission. A failed append is surfaced and
        counted by the caller summary — never raised into accounting."""
        try:
            from src.invo.sync_audit import append_audit, build_event
            action = action or {}
            prev = prev or {}
            ev = build_event(
                correlation_id=f"{sid}:{kind}:"
                               f"{(gate.get('idempotency_key') or '')[:8]}",
                signal_id=sid, event=event,
                prev_tp=prev.get("tp", prev.get("tp1")), prev_sl=prev.get("stop_loss"),
                desired_tp=action.get("price_target"),
                desired_sl=action.get("stop_loss"),
                attempt=gate.get("attempt", 1), budget=5,
                worker=worker, req_ts_ms=now_ms,
                error_category=error[:200] if error else None,
                response_summary=(error or "")[:300] or None,
                confirmation=confirmer,
                plan_version=action.get("plan_version"),
                final_state=None)
            ok, _ = append_audit("data_store", ev)
            if not ok:
                logger.warning("[Scheduler] Invo exit-sync audit gap: append failed.")
        except Exception as e:
            logger.warning(f"[Scheduler] Invo exit-sync audit gap: {e}")

    def _report_sim_reconcile(self, client, token, portfolio, sim) -> None:
        """Observational reconcile vs the authoritative remote snapshot."""
        try:
            snap = client.list_paper_investments(token, portfolio)
            investments = snap.get("investments") or []
            from src.invo.client import load_share_ledger
            shares = load_share_ledger()
            by_base = {}
            for inv in investments:
                if isinstance(inv, dict) and inv.get("baseId"):
                    by_base[inv["baseId"]] = inv
            remote_open = {}
            for sid, rec in (shares.items() if isinstance(shares, dict) else []):
                if not isinstance(rec, dict):
                    continue
                for bid in rec.get("baseIds") or []:
                    inv = by_base.get(bid)
                    if inv is not None:
                        try:
                            remote_open[sid] = remote_open.get(sid, 0.0) + float(
                                inv.get("entrySim", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            pass
            rep = sim.reconcile(snap.get("portfolioRemainingSim"), remote_open)
            if rep["status"] == "divergent":
                logger.warning(f"[Scheduler] Sim reconcile DIVERGENT: {rep}")
            else:
                logger.info(f"[Scheduler] Sim reconcile {rep['status']}: "
                            f"reserved={rep.get('reserved_total')} "
                            f"drift={rep.get('bank_drift')}")
        except Exception as e:
            logger.warning(f"[Scheduler] Sim reconcile skipped (remote unknown): {e}")
        except Exception as e:
            logger.warning(f"[Scheduler] Invo exit-sync skipped: {e}")

    def _track_outcomes(self) -> None:
        """Update live outcome ledger + paper portfolio. Must never break scans."""
        try:
            from src.tracking.outcome_tracker import OutcomeTracker
            from src.papertrade.portfolio import PaperPortfolio
            tracker = OutcomeTracker()
            tracker.track_all()
            outcomes = tracker.load_ledger()
            PaperPortfolio().settle(outcomes)
        except Exception as e:
            logger.warning(f"[Scheduler] Outcome tracking skipped: {e}")
            return
        # Observational mirror only: free reservations for locally-terminal
        # ENTERED positions. Separate guard so mirror work can never break
        # tracking/settlement above.
        try:
            from src.invo.sim_ledger import SimLedger
            sim = SimLedger(storage_dir="data_store")
            freed = self._release_terminal_reservations(outcomes, sim)
            if freed:
                logger.info(f"[Scheduler] Sim mirror freed {freed} terminal reservation(s).")
        except Exception as e:
            logger.warning(f"[Scheduler] Sim terminal-release skipped: {e}")

    @staticmethod
    def _release_terminal_reservations(outcomes, sim) -> int:
        """Release mirror reservations for natural terminal outcomes.

        Eligible IFF all hold (conservative conjunction):
        - outcome record is final, AND
        - status in the tracker's TERMINAL_STATUSES
          (TP1_HIT / STOPPED_OUT / LIQUIDATED), AND
        - entry_triggered is truthy (the local trade actually entered).

        Everything else is preserved, especially EXPIRED (entry never
        triggered locally tells us nothing about the remote position, which
        was opened at share time) and non-final records. Release uses
        credit=None: the reservation is freed WITHOUT crediting, because a
        local terminal outcome is not authoritative evidence of remote
        settlement — the remote leg may still be open, in which case the
        next reconcile surfaces it as remote_only divergence (observable).
        Idempotent: repeat runs release nothing further.
        """
        from src.tracking.outcome_tracker import TERMINAL_STATUSES
        freed = 0
        for sid, oc in (outcomes.items() if isinstance(outcomes, dict) else []):
            try:
                if not isinstance(oc, dict) or not oc.get("final"):
                    continue
                if oc.get("status") not in TERMINAL_STATUSES:
                    continue
                if not oc.get("entry_triggered"):
                    continue
                out = sim.release(sid, credit=None,
                                  note="local-terminal-unverified")
                freed += 1 if out.get("released") else 0
            except Exception:
                continue  # one bad record must never block the rest
        return freed

    def _record_scan(self, scan_type: str, status: str, candidates: int, published: int) -> None:
        """Append a heartbeat entry so the dashboard proves every worker cycle ran."""
        path = os.path.join(os.path.dirname(self.state_file) or ".", "scan_history.json")
        history = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                history = []
        history.append(
            {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scan_type": scan_type,
                "status": status,
                "candidates": candidates,
                "published": published,
            }
        )
        try:
            # Keep ~10 days of 15m heartbeats (96/day) so a full analysis
            # window always survives; signals/outcomes/paper ledgers are uncapped.
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history[-1000:], f, indent=2)
        except OSError as e:
            logger.error(f"[Scheduler] Failed to record scan heartbeat: {e}")

    def start_loop(
        self,
        interval_seconds: int = 900,
        run_immediately: bool = True,
    ) -> None:
        """Starts continuous scheduling loop with signal handling for graceful shutdown."""
        self.is_running = True

        def _handle_exit(sig, frame):
            logger.info("[Scheduler] Received termination signal. Shutting down gracefully...")
            self.is_running = False

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        logger.info(
            f"[Scheduler] Continuous loop running. Interval: {interval_seconds}s ({interval_seconds / 60:.1f}m)."
        )

        if run_immediately:
            self.run_scan_cycle(deep_scan=False)

        while self.is_running:
            try:
                # Sleep in short slices to remain responsive to SIGINT
                for _ in range(interval_seconds):
                    if not self.is_running:
                        break
                    time.sleep(1)

                if self.is_running:
                    self.run_scan_cycle(deep_scan=False)

            except Exception as e:
                logger.error(f"[Scheduler] Unexpected error in scheduler loop: {e}")
                time.sleep(5)

        logger.info("[Scheduler] Scheduler loop exited cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Crypto Daily Futures Signal Engine Scheduler")
    parser.add_argument("--once", action="store_true", help="Run a single scan cycle and exit")
    parser.add_argument("--deep-scan", action="store_true", help="Run deep scan mode")
    parser.add_argument("--interval", type=int, default=900, help="Loop interval in seconds (default: 900 / 15m)")
    args = parser.parse_args()

    scheduler = BotScheduler()
    if args.once:
        scheduler.run_scan_cycle(deep_scan=args.deep_scan)
    else:
        scheduler.start_loop(interval_seconds=args.interval)

if __name__ == "__main__":
    main()
