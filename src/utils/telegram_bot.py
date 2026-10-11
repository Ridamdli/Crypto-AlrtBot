import json
import os
import time
from datetime import datetime, timezone

import requests
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Retry schedule (seconds) between POST attempts. Short on purpose: the
# scheduler loop must not stall for minutes on a wedged chat API.
RETRY_DELAYS = (1.0, 2.0, 4.0)
# HTTP statuses worth retrying. 400 means OUR payload is malformed — a
# retry would just re-send the same bad bytes, so fail fast instead.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class TelegramBot:
    def __init__(self, storage_dir: str = "data_store",
                 dead_letter_name: str = "telegram_dead_letter.json",
                 dead_letter_cap: int = 200):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        self.storage_dir = storage_dir
        self.dead_letter_path = os.path.join(storage_dir, dead_letter_name)
        self.dead_letter_cap = dead_letter_cap

    @staticmethod
    def enabled() -> bool:
        """Kill switch: TELEGRAM_ENABLED=false suppresses all sends."""
        return os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"

    def send_message(self, message: str) -> bool:
        """
        Sends a Markdown message to the configured Telegram chat.

        - Missing credentials or TELEGRAM_ENABLED=false: skip quietly
          (operator choice — not a failure, nothing dead-lettered).
        - Transient failures (network, 429, 5xx): retry with backoff.
        - 400 Bad Request: fail fast, dead-letter for inspection.
        - Exhausted retries: dead-letter for later inspection/replay.
        """
        if not self.enabled():
            logger.info("Telegram sending disabled (TELEGRAM_ENABLED=false). Skipping broadcast.")
            return False
        if not self.token or not self.chat_id:
            logger.warning("Telegram credentials not fully configured. Skipping broadcast.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        last_error: str = ""
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = requests.post(self.base_url, json=payload, timeout=10)
                if response.status_code == 400:
                    last_error = f"400 Bad Request: {response.text[:200]}"
                    break  # our payload is malformed — retrying is pointless
                response.raise_for_status()
                logger.info("Successfully sent signal to Telegram.")
                return True
            except requests.exceptions.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status not in RETRYABLE_STATUS and status < 500:
                    break  # non-retryable client error
            except Exception as e:  # defensive: never let a send crash the scan
                last_error = f"{type(e).__name__}: {e}"
                break
            if attempt < len(RETRY_DELAYS):
                logger.warning(
                    f"Telegram send attempt {attempt + 1} failed ({last_error}); "
                    f"retrying in {RETRY_DELAYS[attempt]:.0f}s.")
                time.sleep(RETRY_DELAYS[attempt])

        logger.error(f"Failed to send Telegram message after retries: {last_error}")
        self._dead_letter(message, last_error)
        return False

    def _dead_letter(self, message: str, error: str) -> None:
        """Persist an unsent message for later inspection/replay.

        Never stores credentials: no token, no chat id — just the timestamp,
        the failure, and the message body.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "error": error,
            "message": message,
        }
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            try:
                with open(self.dead_letter_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, list):
                        data = []
            except (FileNotFoundError, ValueError, OSError):
                data = []
            data.append(entry)
            with open(self.dead_letter_path, "w", encoding="utf-8") as f:
                json.dump(data[-self.dead_letter_cap:], f, indent=2)
            logger.info(f"Unsent Telegram message dead-lettered ({self.dead_letter_path}).")
        except OSError as e:
            logger.error(f"Failed to write Telegram dead letter: {e}")

    def dead_letter_count(self) -> int:
        try:
            with open(self.dead_letter_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return len(data) if isinstance(data, list) else 0
        except (FileNotFoundError, ValueError, OSError):
            return 0
