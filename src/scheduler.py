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
        return published_signals

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
