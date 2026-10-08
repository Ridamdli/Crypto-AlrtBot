import csv
import os
from typing import List, Dict, Any

class JesseAdapter:
    """
    Adapter to export market data and signal events into formats compatible
    with Jesse (https://github.com/jesse-ai/jesse) for independent validation per PRD §33.
    Jesse is purely an external validator, NOT a runtime dependency.
    """

    @staticmethod
    def export_candles_to_csv(
        klines: List[Dict[str, Any]],
        symbol: str,
        output_dir: str = "data_store/jesse_export",
    ) -> str:
        """
        Exports standardized klines to Jesse CSV format:
        [timestamp (ms), open, close, high, low, volume]
        """
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, f"{symbol}_candles.csv")

        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            # Jesse format: timestamp, open, close, high, low, volume
            for k in klines:
                writer.writerow([
                    k["timestamp"],
                    k["open"],
                    k["close"],
                    k["high"],
                    k["low"],
                    k["volume"],
                ])
        return file_path

    @staticmethod
    def export_signals_to_jesse_events(
        signals: List[Dict[str, Any]],
        output_dir: str = "data_store/jesse_export",
    ) -> str:
        """Exports signals as external event markers for Jesse strategy replay."""
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, "signals_events.csv")

        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "signal_id", "timestamp", "symbol", "side",
                "entry", "sl", "tp1", "tp2", "tp3", "leverage", "confidence"
            ])
            for s in signals:
                writer.writerow([
                    s.get("signal_id"),
                    s.get("timestamp"),
                    s.get("symbol"),
                    s.get("side"),
                    s.get("entry"),
                    s.get("stop_loss"),
                    s.get("tp1"),
                    s.get("tp2"),
                    s.get("tp3"),
                    s.get("leverage"),
                    s.get("confidence"),
                ])
        return file_path
