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
        self._share_to_invo(published_signals)
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
            else:
                token = ""
            for sig in published:
                try:
                    out = client.share_paper_trade(
                        {"symbol": sig.symbol, "side": sig.side,
                         "leverage": sig.leverage, "stop_loss": sig.stop_loss},
                        token=token, portfolio_id=portfolio,
                        entry_sim=sim_size,
                        price_target=float(sig.tp1),
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
                except InvoConflictError as e:
                    # Setup already shared (same coin open) — not an error.
                    logger.info(f"[Scheduler] Invo share skipped (already open): {e}")
                    record_share("data_store", getattr(sig, "signal_id", "?"), {
                        "baseIds": e.conflicting_base_ids,
                        "already_open": True,
                        "symbol": sig.symbol,
                        "side": sig.side,
                        "portfolioId": portfolio,
                    })
                except Exception as e:
                    # Auth death clears the token so the next cycle OTP
                    # self-logins fresh instead of 401-looping forever.
                    if "401" in str(e):
                        client.access_token = None
                    logger.warning(f"[Scheduler] Invo paper share skipped for {sig.symbol}: {e}")
        except Exception as e:
            logger.warning(f"[Scheduler] Invo paper share skipped: {e}")

    def _share_to_invo(self, published: list) -> None:
        """Auto-share published signals to Invo. Disabled unless explicitly enabled.

        INVO_ENABLED=true + INVO_DRY_RUN=false + credentials required for any
        real post. Sharing opens REAL positions with REAL money. Never breaks scans.
        """
        if os.getenv("INVO_ENABLED", "false").lower() != "true":
            return
        try:
            from src.invo.client import InvoClient
            dry = os.getenv("INVO_DRY_RUN", "true").lower() != "false"
            client = InvoClient(
                email=os.getenv("INVO_EMAIL", ""),
            )
            for sig in published:
                try:
                    client.share_position(
                        {"symbol": sig.symbol, "side": sig.side,
                         "leverage": sig.leverage, "tp1": sig.tp1,
                         "stop_loss": sig.stop_loss},
                        dry_run=dry,
                        portfolio_id=os.getenv("INVO_PORTFOLIO_ID") or None,
                    )
                except Exception as e:
                    logger.warning(f"[Scheduler] Invo share skipped for {sig.symbol}: {e}")
        except Exception as e:
            logger.warning(f"[Scheduler] Invo share skipped: {e}")

    def _track_outcomes(self) -> None:
        """Update live outcome ledger + paper portfolio. Must never break scans."""
        try:
            from src.tracking.outcome_tracker import OutcomeTracker
            from src.papertrade.portfolio import PaperPortfolio
            tracker = OutcomeTracker()
            tracker.track_all()
            PaperPortfolio().settle(tracker.load_ledger())
        except Exception as e:
            logger.warning(f"[Scheduler] Outcome tracking skipped: {e}")

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
