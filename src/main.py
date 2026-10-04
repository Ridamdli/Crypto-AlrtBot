import os
from dotenv import load_dotenv

load_dotenv()

from src.pipeline import SignalPipeline
from src.models.signal import SignalModel
from src.utils.telegram_formatter import TelegramFormatter
from src.utils.telegram_bot import TelegramBot
from src.data.persistence import SignalPersistence
from src.utils.logger import get_logger

logger = get_logger(__name__)

def main():
    logger.info("Initializing Crypto-AlrtBot...")

    pipeline = SignalPipeline()
    candidates = pipeline.run_pipeline()

    if not candidates:
        logger.info("No viable signals generated. Quality over quantity – 0 signals today.")
        return

    bot = TelegramBot()
    persistence = SignalPersistence()

    for rank, cand_dict in enumerate(candidates, start=1):
        try:
            signal = SignalModel(**cand_dict)
            persistence.save_signal(signal)
            msg = TelegramFormatter.format_signal(signal, rank=rank)
            logger.info(f"Signal #{rank}: {signal.symbol} {signal.side} – confidence {signal.confidence}/100")
            bot.send_message(msg)
        except Exception as e:
            logger.error(f"Failed to publish signal for {cand_dict.get('symbol')}: {e}")

    logger.info(f"Daily run complete. {len(candidates)} signal(s) published.")

if __name__ == "__main__":
    main()
