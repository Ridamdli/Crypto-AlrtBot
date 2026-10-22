"""P3 shadow evaluation for INVALIDATE_PENDING_SETUP.

Records what P3 *would* have decided. It never changes the decision: the
supervisor computes this row and then returns its usual result.

P3 requires a completed structure break AND counter confirmation. The only
counter verdict that counts as confirmation today is GENUINE_REVERSAL; the
observed NO_OPPOSITION / WEAK_OPPOSITION verdicts do not.

`closes_beyond` is a snapshot flag (1/0), not a consecutive-close count — the
ledger stores one close per decision. Persistence gates need the bar replay.
"""
import json
import os
import time

from src.utils.logger import get_logger

logger = get_logger(__name__)

# data_store is the main service's mounted volume, so the sample survives a
# redeploy. mirror_data would be container-local there.
DEFAULT_PATH = os.path.join("data_store", "p3_shadow.jsonl")


def evaluate(view, struct, counter) -> dict:
    ev = dict(getattr(struct, "evidence", None) or {})
    stop = ev.get("active_stop")
    close = ev.get("last_close")
    margin = None
    try:
        if stop and close:
            margin = round(abs(float(close) - float(stop)) / float(stop) * 100, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        margin = None
    structure_pass = bool(ev.get("stop_breached"))
    counter_verdict = getattr(counter, "verdict", None)
    counter_pass = counter_verdict == "GENUINE_REVERSAL"
    return {
        "ts": int(time.time()),
        "signal_id": view.signal_id,
        "coin": view.symbol,
        "side": view.side,
        "invalidation_level": stop,
        "last_close": close,
        "breach_margin_pct": margin,
        "closes_beyond": 1 if structure_pass else 0,
        "counter_verdict": counter_verdict,
        "structure_pass": structure_pass,
        "counter_pass": counter_pass,
        "verdict": "pass" if (structure_pass and counter_pass) else "reject",
        "would_be_action": "INVALIDATE_PENDING_SETUP",
    }


def write(record: dict, path: str = DEFAULT_PATH) -> bool:
    """Append one row. Shadowing must never affect exit handling."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except OSError as e:
        logger.warning(f"[P3 shadow] write failed: {e}")
        return False
