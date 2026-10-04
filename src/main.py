import os
from dotenv import load_dotenv

# Load env before imports
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
    
    # 1. Run Pipeline
    pipeline = SignalPipeline()
    candidates = pipeline.run_pipeline()
    
    if not candidates:
        logger.info("No viable signals generated today. Quality over quantity.")
        return

    # 2. Output Services
    bot = TelegramBot()
    formatter = TelegramFormatter()
    persistence = SignalPersistence()

    # 3. Process, Validate, and Deliver
    for cand_dict in candidates:
        try:
            # Validate against Strict Schema
            signal = SignalModel(**cand_dict)
            
            # Persist
            persistence.save_signal(signal)
            
            # Format
            msg = formatter.format_signal(signal)
            
            # Broadcast
            bot.send_message(msg)
            
        except Exception as e:
            logger.error(f"Failed to process/validate signal for {cand_dict.get('symbol')}: {e}")

    logger.info("Daily run complete.")

if __name__ == "__main__":
    main()
