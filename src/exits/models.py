"""Exit Orchestrator data model.

Position states are DERIVED from existing artifacts (persisted signal +
outcome-ledger record + exit-ledger overlay) so no schema migration is needed
anywhere else in the repository:

- PENDING:     signal exists, entry not triggered, outcome not final
               (covers "no record yet" and "recorded but waiting").
- OPEN:        entry triggered, TP not hit, outcome not final.
- PARTIAL:     legacy (ladder era); single-TP plans never emit it.
- CLOSED:      outcome record final=True.
- INVALIDATED: setup never entered (expired, or exit-ledger invalidated flag).

Only stdlib + typing are imported here. This module (and the whole
src/exits package) must never import broker, network-trading, or order
execution code — enforced by a unit test.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

EXITS_CODE_VERSION = "0.1.0"


class PositionState(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    CLOSED = "closed"
    INVALIDATED = "invalidated"


class ExitAction(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    ADJUST_TAKE_PROFITS = "ADJUST_TAKE_PROFITS"
    REDUCE_PAPER_POSITION = "REDUCE_PAPER_POSITION"
    CLOSE_PAPER_POSITION = "CLOSE_PAPER_POSITION"
    INVALIDATE_PENDING_SETUP = "INVALIDATE_PENDING_SETUP"
    FLAG_CONFLICT = "FLAG_CONFLICT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# Actions that change the active exit plan (and therefore need RiskGuard).
PLAN_CHANGING_ACTIONS = {
    ExitAction.TIGHTEN_STOP,
    ExitAction.ADJUST_TAKE_PROFITS,
}


@dataclass(frozen=True)
class ExitPlan:
    """An exit plan version for one signal. Version 0 is the original plan."""
    signal_id: str
    version: int
    stop_loss: float
    tp: float
    decided_at_ms: int
    basis_decision_key: str = "genesis"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "version": self.version,
            "stop_loss": self.stop_loss,
            "tp": self.tp,
            "decided_at_ms": self.decided_at_ms,
            "basis_decision_key": self.basis_decision_key,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExitPlan":
        return ExitPlan(
            signal_id=d["signal_id"],
            version=int(d.get("version", 0)),
            stop_loss=float(d["stop_loss"]),
            tp=float(d.get("tp", d.get("tp1"))),
            decided_at_ms=int(d.get("decided_at_ms", 0)),
            basis_decision_key=str(d.get("basis_decision_key", "genesis")),
        )

    @staticmethod
    def genesis(signal: dict[str, Any]) -> "ExitPlan":
        return ExitPlan(
            signal_id=signal["signal_id"],
            version=0,
            stop_loss=float(signal["stop_loss"]),
            tp=float(signal.get("tp", signal.get("tp1"))),
            decided_at_ms=0,
            basis_decision_key="genesis",
        )


@dataclass(frozen=True)
class PositionView:
    """Everything the workers/supervisor need, derived from existing ledgers."""
    signal: dict[str, Any]
    outcome: dict[str, Any] | None
    state: PositionState
    plan: ExitPlan
    invalidated_by_exits: bool = False

    @property
    def signal_id(self) -> str:
        return self.signal["signal_id"]

    @property
    def symbol(self) -> str:
        return self.signal["symbol"]

    @property
    def side(self) -> str:
        return self.signal["side"]

    @property
    def entry(self) -> float:
        return float(self.signal["entry"])

    @property
    def is_long(self) -> bool:
        return self.side.upper() == "LONG"


def classify_position(
    signal: dict[str, Any],
    outcome: dict[str, Any] | None,
    invalidated_by_exits: bool = False,
) -> PositionState:
    """Derive the lifecycle state. Pure function of existing records."""
    if outcome is None:
        return PositionState.INVALIDATED if invalidated_by_exits else PositionState.PENDING
    if outcome.get("final"):
        if not outcome.get("entry_triggered"):
            return PositionState.INVALIDATED
        return PositionState.CLOSED
    if invalidated_by_exits:
        return PositionState.INVALIDATED
    if outcome.get("entry_triggered"):
        return PositionState.OPEN
    return PositionState.PENDING


@dataclass
class WorkerAssessment:
    """One worker's structured verdict. Workers propose; they never apply.

    evidence_quality semantics (0.0–1.0, documented per worker):
      1.0 = fresh complete data, all required measurements available.
      <1.0 = discounted for staleness (0.5x), thin history (0.7x),
             or missing optional inputs such as OI (0.8x). Multiplicative.
      0.0 = abstention (verdict carries no weight; see `uncertainty`).
    """
    worker_id: str
    worker_version: str
    assessed_at_ms: int
    data_ts_ms: int
    verdict: str
    evidence: dict[str, Any] = field(default_factory=dict)
    indicators: list[str] = field(default_factory=list)
    proposed_action: str | None = None
    proposal: dict[str, Any] = field(default_factory=dict)
    evidence_quality: float = 1.0
    uncertainty: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "worker_version": self.worker_version,
            "assessed_at_ms": self.assessed_at_ms,
            "data_ts_ms": self.data_ts_ms,
            "verdict": self.verdict,
            "evidence": self.evidence,
            "indicators": self.indicators,
            "proposed_action": self.proposed_action,
            "proposal": self.proposal,
            "evidence_quality": self.evidence_quality,
            "uncertainty": self.uncertainty,
        }


@dataclass
class SupervisorDecision:
    """Final proposed action plus the reasoning that justifies it."""
    signal_id: str
    action: str
    rationale: str
    rejected: list[dict[str, str]] = field(default_factory=list)
    assessments: list[dict[str, Any]] = field(default_factory=list)
    risk_guard: dict[str, Any] = field(default_factory=dict)
    decision_key: str = ""
    plan_after: dict[str, Any] | None = None
    events_applied: list[dict[str, Any]] = field(default_factory=list)
    config_version: str = ""
    code_version: str = EXITS_CODE_VERSION
    decided_at_ms: int = 0
    data_ts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "action": self.action,
            "rationale": self.rationale,
            "rejected": self.rejected,
            "assessments": self.assessments,
            "risk_guard": self.risk_guard,
            "decision_key": self.decision_key,
            "plan_after": self.plan_after,
            "events_applied": self.events_applied,
            "config_version": self.config_version,
            "code_version": self.code_version,
            "decided_at_ms": self.decided_at_ms,
            "data_ts_ms": self.data_ts_ms,
        }
