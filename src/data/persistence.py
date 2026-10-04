import json
import os
from datetime import datetime
from src.models.signal import SignalModel
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SignalPersistence:
    def __init__(self, storage_dir: str = "data_store"):
        self.storage_dir = storage_dir
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)

    def save_signal(self, signal: SignalModel) -> bool:
        """
        Saves a validated signal to local JSON for auditing/backtesting.
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(self.storage_dir, f"signals_{date_str}.json")

        # Load existing
        data = []
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Could not read existing signals: {e}")

        # Append new
        data.append(signal.dict())

        # Save
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=4)
            logger.info(f"Signal {signal.signal_id} persisted locally.")
            return True
        except Exception as e:
            logger.error(f"Failed to save signal: {e}")
            return False
