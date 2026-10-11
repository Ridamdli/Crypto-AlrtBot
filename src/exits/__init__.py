"""Exit Orchestrators — multi-agent trade supervision (paper-only).

Supervises every generated setup, reevaluates validity on market events, and
proposes exit-plan changes through deterministic workers + supervisor +
RiskGuard. Paper-side only: nothing here can place orders, move real money,
or touch broker credentials (no such imports exist in this package).
"""
from src.exits.models import (
    EXITS_CODE_VERSION,
    ExitAction,
    ExitPlan,
    PositionState,
    PositionView,
    SupervisorDecision,
    WorkerAssessment,
    classify_position,
)

__all__ = [
    "EXITS_CODE_VERSION",
    "ExitAction",
    "ExitPlan",
    "PositionState",
    "PositionView",
    "SupervisorDecision",
    "WorkerAssessment",
    "classify_position",
]
