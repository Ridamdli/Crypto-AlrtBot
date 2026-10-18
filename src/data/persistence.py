import json
import os
from datetime import datetime, timezone

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
        # Day boundary is UTC so the file date matches the UTC signal
        # timestamps regardless of the host timezone.
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = os.path.join(self.storage_dir, f"signals_{date_str}.json")

        # Load existing
        data = []
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
            except Exception as e:
                # Quarantine the unreadable file instead of overwriting it:
                # a fresh write here would destroy every prior signal of the
                # day. The .corrupt suffix keeps it outside the readers'
                # signals_*.json glob.
                quarantine = file_path + ".corrupt"
                logger.error(
                    f"Could not read existing signals ({e}); preserving as "
                    f"{quarantine} and starting a fresh file."
                )
                try:
                    os.replace(file_path, quarantine)
                except OSError as move_err:
                    logger.error(
                        f"Cannot quarantine corrupt signals file: {move_err}; "
                        "refusing to overwrite."
                    )
                    return False
                data = []

        # Append new
        data.append(signal.dict())

        # Save
        try:
            from src.utils.atomic import atomic_write_json
            atomic_write_json(file_path, data, indent=4)
            logger.info(f"Signal {signal.signal_id} persisted locally.")
            return True
        except Exception as e:
            logger.error(f"Failed to save signal: {e}")
            return False
