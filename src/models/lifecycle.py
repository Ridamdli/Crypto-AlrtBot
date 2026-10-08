from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import json

from src.utils.logger import get_logger

logger = get_logger(__name__)

class SignalState(str, Enum):
    """Signal lifecycle states per PRD §26."""
    GENERATED = "GENERATED"
    WAITING_FOR_ENTRY = "WAITING_FOR_ENTRY"
    ACTIVE = "ACTIVE"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

class SignalLifecycleTracker:
    """
    Deterministic Signal Lifecycle State Machine per PRD §26.
    Tracks the theoretical progression of a published signal without order execution.
    """

    # Explicit, legal state transition rules
    VALID_TRANSITIONS: Dict[SignalState, List[SignalState]] = {
        SignalState.GENERATED: [
            SignalState.WAITING_FOR_ENTRY,
            SignalState.INVALIDATED,
        ],
        SignalState.WAITING_FOR_ENTRY: [
            SignalState.ACTIVE,
            SignalState.EXPIRED,
            SignalState.INVALIDATED,
        ],
        SignalState.ACTIVE: [
            SignalState.TP1_HIT,
            SignalState.STOPPED_OUT,
            SignalState.INVALIDATED,
        ],
        SignalState.TP1_HIT: [
            SignalState.TP2_HIT,
            SignalState.STOPPED_OUT,  # Breakeven stop hit on remaining position
        ],
        SignalState.TP2_HIT: [
            SignalState.TP3_HIT,
            SignalState.STOPPED_OUT,  # Trailing stop hit on remaining position
        ],
        SignalState.TP3_HIT: [],      # Terminal
        SignalState.STOPPED_OUT: [],  # Terminal
        SignalState.EXPIRED: [],      # Terminal
        SignalState.INVALIDATED: [],  # Terminal
    }

    def __init__(
        self,
        signal_id: str,
        initial_state: SignalState = SignalState.GENERATED,
        created_at: Optional[str] = None,
    ):
        self.signal_id = signal_id
        self.current_state = initial_state
        self.history: List[Dict[str, Any]] = [
            {
                "from_state": None,
                "to_state": initial_state.value,
                "timestamp": created_at or datetime.now(timezone.utc).isoformat() + "Z",
                "reason": "Signal initialized",
                "price": None,
            }
        ]

    @property
    def is_terminal(self) -> bool:
        return len(self.VALID_TRANSITIONS.get(self.current_state, [])) == 0

    def transition_to(
        self,
        target_state: SignalState,
        reason: str,
        timestamp: Optional[str] = None,
        price: Optional[float] = None,
    ) -> bool:
        """
        Transitions the signal state machine to target_state.
        Validates transition legality deterministically.
        Idempotent: repeating the current state returns True without error.
        Raises ValueError on invalid state transition.
        """
        if target_state == self.current_state:
            # Idempotent no-op
            return True

        allowed = self.VALID_TRANSITIONS.get(self.current_state, [])
        if target_state not in allowed:
            err = (
                f"Illegal lifecycle transition for {self.signal_id}: "
                f"{self.current_state.value} -> {target_state.value}. "
                f"Allowed transitions from {self.current_state.value}: {[s.value for s in allowed]}"
            )
            logger.error(err)
            raise ValueError(err)

        ts = timestamp or datetime.now(timezone.utc).isoformat() + "Z"
        record = {
            "from_state": self.current_state.value,
            "to_state": target_state.value,
            "timestamp": ts,
            "reason": reason,
            "price": price,
        }
        self.history.append(record)
        old_state = self.current_state
        self.current_state = target_state
        logger.info(
            f"[Lifecycle] {self.signal_id}: {old_state.value} -> {target_state.value} ({reason})"
        )
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "current_state": self.current_state.value,
            "is_terminal": self.is_terminal,
            "history": self.history,
        }

    def save(self, filepath: str) -> None:
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "SignalLifecycleTracker":
        with open(filepath, "r") as f:
            data = json.load(f)
        tracker = cls(
            signal_id=data["signal_id"],
            initial_state=SignalState(data["current_state"]),
        )
        tracker.history = data.get("history", [])
        return tracker
