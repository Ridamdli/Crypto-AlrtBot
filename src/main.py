from dotenv import load_dotenv

load_dotenv()

from src.data.persistence import SignalPersistence
from src.models.signal import SignalModel
from src.pipeline import SignalPipeline
from src.utils.logger import get_logger
from src.utils.telegram_bot import TelegramBot
from src.utils.telegram_formatter import TelegramFormatter

logger = get_logger(__name__)

def main():
    logger.info("Initializing Crypto-AlrtBot...")

    pipeline = SignalPipeline()
    candidates = pipeline.run_pipeline()

    if not candidates:
        logger.info("No viable signals generated. Quality over quantity – 0 signals today.")
        return {"delivered": 0, "failed": 0, "candidates": 0}

    bot = TelegramBot()
    persistence = SignalPersistence()

    delivered, failed = 0, 0
    for rank, cand_dict in enumerate(candidates, start=1):
        try:
            signal = SignalModel(**cand_dict)
            persistence.save_signal(signal)
            msg = TelegramFormatter.format_signal(signal, rank=rank)
            logger.info(f"Signal #{rank}: {signal.symbol} {signal.side} – confidence {signal.confidence}/100")
            if bot.send_message(msg):
                delivered += 1
            else:
                failed += 1
                logger.warning(f"Signal #{rank}: Telegram send failed – counted as undelivered, not published.")
        except Exception as e:
            failed += 1
            logger.error(f"Failed to publish signal for {cand_dict.get('symbol')}: {e}")

    logger.info(f"Daily run complete. {delivered} signal(s) delivered, {failed} failed, out of {len(candidates)}.")
    return {"delivered": delivered, "failed": failed, "candidates": len(candidates)}

if __name__ == "__main__":
    main()
