"""Invo exit-sync: propagate APPLIED orchestrator decisions to paper positions.

State-based and idempotent: compares the exit ledger's active plans against
the last-synced levels and only emits actions on real differences. A repeat
run with no new applied decisions emits zero actions.

Scope rules (per operator direction):
- TP1 + SL only. TP2/TP3 are never sent — the investments API carries one
  priceTarget, and payloads are built from plan.tp1 exclusively.
- CLOSE fires only on applied supervisor-close events (genuine invalidation).
- REDUCE / FLAG_CONFLICT / INVALIDATE_PENDING_SETUP have no
  investment-level representation: logged, never posted.

Nothing here touches real-money paths (no dex/* calls, no order flow).
"""
import json as _json
import os as _os
from typing import Any, Dict, List

SYNC_STATE_NAME = "invo_exit_sync.json"


def load_sync_state(storage_dir: str = "data_store") -> Dict[str, Any]:
    """{synced_levels: {signal_id: {stop_loss, tp1}}, closed: [signal_ids]}."""
    path = _os.path.join(storage_dir, SYNC_STATE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
            if isinstance(data, dict):
                data.setdefault("synced_levels", {})
                data.setdefault("closed", [])
                return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"synced_levels": {}, "closed": []}


def save_sync_state(storage_dir: str, state: Dict[str, Any]) -> None:
    _os.makedirs(storage_dir, exist_ok=True)
    try:
        with open(_os.path.join(storage_dir, SYNC_STATE_NAME), "w", encoding="utf-8") as f:
            _json.dump(state, f, indent=2)
    except OSError:
        pass


def compute_sync_actions(
    plans: List[Dict[str, Any]],
    close_signal_ids: List[str],
    share_ledger: Dict[str, Any],
    sync_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pure function: applied exit-ledger state -> Invo actions.

    plans: active ExitPlan dicts (only version > 0 can differ from create).
    close_signal_ids: signals with an applied supervisor-close event.
    share_ledger: signal_id -> {baseIds: [...]} from the share path.
    Returns [{kind: modify|close, signal_id, base_ids, price_target?, stop_loss?}].
    """
    actions: List[Dict[str, Any]] = []
    synced_levels = sync_state.get("synced_levels", {}) or {}
    closed = set(sync_state.get("closed", []) or [])

    if not isinstance(share_ledger, dict):
        return actions
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        sid = plan.get("signal_id", "?")
        try:
            version = int(plan.get("version", 0) or 0)
        except (TypeError, ValueError):
            continue
        if version <= 0:
            continue  # genesis == create-time levels; nothing new to send
        rec = share_ledger.get(sid) or {}
        if not isinstance(rec, dict):
            continue  # legacy/malformed share record: no safe target
        base_ids = [b for b in (rec.get("baseIds") or []) if b]
        if not base_ids:
            continue  # no live investment to target (test/manual shares)
        # TP1-only by construction: plan.tp2/tp3 are never read here.
        cur = {"stop_loss": plan.get("stop_loss"), "tp1": plan.get("tp1")}
        if synced_levels.get(sid) != cur:
            actions.append({
                "kind": "modify",
                "signal_id": sid,
                "base_ids": base_ids,
                "price_target": plan.get("tp1"),
                "stop_loss": plan.get("stop_loss"),
            })

    if isinstance(close_signal_ids, (list, tuple)):
        for sid in close_signal_ids:
            if sid in closed:
                continue
            rec = share_ledger.get(sid) or {}
            if not isinstance(rec, dict):
                continue
            base_ids = [b for b in (rec.get("baseIds") or []) if b]
            if not base_ids:
                continue
            actions.append({"kind": "close", "signal_id": sid, "base_ids": base_ids})

    return actions
