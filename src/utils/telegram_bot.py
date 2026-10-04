import os
import requests
from src.utils.logger import get_logger

logger = get_logger(__name__)

class TelegramBot:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_message(self, message: str) -> bool:
        """
        Sends a Markdown message to the configured Telegram chat.
        """
        if not self.token or not self.chat_id:
            logger.warning("Telegram credentials not fully configured. Skipping broadcast.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            response = requests.post(self.base_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Successfully sent signal to Telegram.")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
