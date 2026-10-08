import os
import json
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone

from src.pipeline import SignalPipeline
from src.config import CONFIG, AppConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)

class ReplayHarness:
    """
    Deterministic Replay Harness per PRD §31, §38 and Milestone 6.1.
    Injects historical timestamps into the exact production SignalPipeline.
    Guarantees no future data leakage and verifies that:
        Replay(timestamp=X) == Replay(timestamp=X)
    """

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        pipeline: Optional[SignalPipeline] = None,
        storage_dir: str = "data_store/replay",
    ):
        self.config = config or CONFIG
        self.pipeline = pipeline or SignalPipeline(config=self.config)
        self.storage_dir = storage_dir
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir, exist_ok=True)

    def replay_timestamp(
        self,
        as_of_time: Union[int, str, datetime],
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Runs the production signal pipeline as of a specific historical timestamp.
        Only data available on or before as_of_time is fetched and used.
        """
        logger.info(f"[ReplayHarness] Running replay for timestamp: {as_of_time}")
        signals = self.pipeline.run_pipeline(as_of_time=as_of_time, symbols=symbols)
        logger.info(f"[ReplayHarness] Generated {len(signals)} signals for {as_of_time}")
        return signals

    def replay_range(
        self,
        timestamps: List[Union[int, str, datetime]],
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Replays a chronological series of historical timestamps.
        Returns a dict mapping timestamp string to generated signals.
        """
        all_results = {}
        for ts in timestamps:
            signals = self.replay_timestamp(as_of_time=ts, symbols=symbols)
            ts_key = str(ts)
            all_results[ts_key] = signals
        return all_results

    def save_replay_results(
        self,
        results: Dict[str, Any],
        filename: Optional[str] = None,
    ) -> str:
        """Serializes replay outputs to local JSON file for auditing/evaluation."""
        if not filename:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"replay_{now_str}.json"
        
        file_path = os.path.join(self.storage_dir, filename)
        with open(file_path, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"[ReplayHarness] Saved replay results to {file_path}")
        return file_path

    def load_replay_results(self, filepath: str) -> Dict[str, Any]:
        """Loads serialized replay results from disk."""
        with open(filepath, "r") as f:
            return json.load(f)

    @staticmethod
    def assert_deterministic(
        run1: List[Dict[str, Any]],
        run2: List[Dict[str, Any]],
        fields_to_check: Optional[List[str]] = None,
    ) -> bool:
        """
        Verifies that two runs produce the exact identical outputs.
        Checks:
        - Candidate count and order
        - Symbol and side
        - Confidence score
        - Entry, SL, TP1, TP2, TP3
        - Leverage, margin, position notional, risk amount
        - R:R ratios
        - Conditions, invalidation conditions, management rules
        """
        if len(run1) != len(run2):
            raise AssertionError(
                f"Candidate counts differ: {len(run1)} != {len(run2)}"
            )

        fields = fields_to_check or [
            "signal_id",
            "symbol",
            "side",
            "confidence",
            "entry",
            "tp1",
            "tp2",
            "tp3",
            "stop_loss",
            "leverage",
            "margin",
            "position_notional",
            "risk_amount",
            "risk_reward_tp1",
            "risk_reward_tp2",
            "risk_reward_tp3",
            "conditions",
            "invalidation_conditions",
            "management_rules",
            "configuration_hash",
        ]

        for idx, (s1, s2) in enumerate(zip(run1, run2)):
            for f in fields:
                val1 = s1.get(f)
                val2 = s2.get(f)
                if isinstance(val1, float) and isinstance(val2, float):
                    if abs(val1 - val2) > 1e-6:
                        raise AssertionError(
                            f"Mismatch at index {idx}, field '{f}': {val1} != {val2}"
                        )
                elif val1 != val2:
                    raise AssertionError(
                        f"Mismatch at index {idx}, field '{f}': {val1} != {val2}"
                    )

        return True
