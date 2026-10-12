"""Durable sync-attempt journal (append-only JSONL + retention disclosure).

Every TP1/SL sync attempt — request, retry, confirmation, failure,
reconciliation — lands here as one line, correlated across retries by
correlation_id (the supervisor decision_key). Retention is bounded
(AUDIT_CAP_LINES); trimming is counted in a sidecar file and disclosed on
read, so a trimmed line is never mistaken for a lost one. Unexplained
sequence gaps are reported explicitly.

Audit writes must never corrupt accounting: append_audit() returns
(ok, seq) and NEVER raises. Callers surface a False as a warning/metric
while the operation result stands.

Secrets policy: values under deny-listed keys (tokens, passwords, auth
headers) and inline bearer/password patterns are replaced with
"[redacted]" before persistence. Position/base IDs are operational
correlation data (already present in logs) and are preserved.

Duplicate identity: a duplicate is an entry with an IDENTICAL
(event_id, worker) pair — i.e. the exact same append performed twice by
the same actor. The same correlation_id from DIFFERENT workers, or across
different attempts, is audit history and is never collapsed: overlapping
scheduler instances are distinguished precisely by the worker field.
See find_duplicates().
"""
import json as _json
import os as _os
import re as _re
import time as _time
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

AUDIT_NAME = "invo_sync_audit.jsonl"
META_NAME = "invo_sync_audit.meta.json"
AUDIT_CAP_LINES = 10000

EVENT_TYPES = (
    "plan_created",
    "update_requested",
    "request_sent",
    "confirmed",
    "retry_scheduled",
    "failed",
    "reconciled",
    "already_gone",
)

CONFIRMATIONS = (
    "authoritative-response",
    "remote-readback",
    "unverified",
)

REQUIRED_FIELDS = (
    "event_id", "correlation_id", "signal_id", "symbol", "event",
    "prev_tp", "prev_sl", "desired_tp", "desired_sl",
    "attempt", "budget", "worker", "req_ts_ms", "resp_ts_ms",
    "http_status", "error_category", "response_summary", "confirmation",
    "plan_version", "final_state",
)

_DENY_KEYS = ("token", "password", "secret", "authorization", "bearer",
              "app_password", "access_token", "api_key", "apikey")
# Pair-first (Bearer <value>, key=value), then leftover deny words. Pair
# matching runs before word matching so secret VALUES are consumed too —
# a trailing greedy pattern would eat neighboring innocent tokens.
_PAIR_RE = _re.compile(
    r"(?i)\b(?:bearer\s+\S+|(?:password|passwd|pwd|token|secret|api[_-]?key|"
    r"app[_-]?password|access[_-]?token)\s*[:=]\s*\S+)")
_WORD_RE = _re.compile(
    r"(?i)\b(?:bearer|password|passwd|pwd|token|secret|api[_-]?key|"
    r"app[_-]?password|access[_-]?token)\b")


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[redacted]" if any(t in str(k).lower() for t in _DENY_KEYS)
                    else _sanitize(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, str):
        return _WORD_RE.sub("[redacted]", _PAIR_RE.sub("[redacted]", obj))
    return obj


def build_event(**kw: Any) -> Dict[str, Any]:
    """Validate + normalize one audit event. Deterministic event_id.

    Raises ValueError on unknown event type or missing correlation/signal.
    """
    if kw.get("event") not in EVENT_TYPES:
        raise ValueError(f"unknown audit event type: {kw.get('event')!r}")
    if not kw.get("correlation_id") or not kw.get("signal_id"):
        raise ValueError("audit event requires correlation_id and signal_id")
    if (kw.get("confirmation") is not None
            and kw["confirmation"] not in CONFIRMATIONS):
        raise ValueError(
            f"unknown confirmation {kw.get('confirmation')!r}; "
            f"expected one of {sorted(CONFIRMATIONS)} or None")
    attempt = int(kw.get("attempt", 1))
    ev: Dict[str, Any] = {"event_id": f"{kw['correlation_id']}#{attempt}"}
    for field in REQUIRED_FIELDS:
        if field in ("event_id",):
            continue
        ev[field] = kw.get(field)
    ev["attempt"] = attempt
    return _sanitize(ev)


def _paths(storage_dir: str):
    return (_os.path.join(storage_dir, AUDIT_NAME),
            _os.path.join(storage_dir, META_NAME))


def _read_meta(storage_dir: str) -> Dict[str, Any]:
    _, meta_path = _paths(storage_dir)
    try:
        with open(meta_path, encoding="utf-8") as f:
            data = _json.load(f)
            if isinstance(data, dict):
                return {"trimmed_lines": int(data.get("trimmed_lines", 0))}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"trimmed_lines": 0}


def _write_meta(storage_dir: str, meta: Dict[str, Any]) -> None:
    _, meta_path = _paths(storage_dir)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump(meta, f)
    except OSError as e:
        logger.warning(f"[Audit] meta write failed ({e}) — counts may under-report trims.")


def append_audit(storage_dir: str, event: Dict[str, Any],
                 cap: int = AUDIT_CAP_LINES) -> Tuple[bool, Optional[int]]:
    """Append one validated event. Returns (ok, seq). Never raises."""
    try:
        _os.makedirs(storage_dir, exist_ok=True)
        path, _ = _paths(storage_dir)
        seq = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        seq = max(seq, int(_json.loads(line).get("seq", 0)))
                    except (ValueError, AttributeError):
                        continue
        except FileNotFoundError:
            pass
        seq += 1
        record = {"seq": seq, **event}
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
        # Bounded retention with disclosed trimming.
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return True, seq
        if len(lines) > cap:
            keep = lines[-cap:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
            meta = _read_meta(storage_dir)
            meta["trimmed_lines"] = meta.get("trimmed_lines", 0) + (len(lines) - cap)
            _write_meta(storage_dir, meta)
        return True, seq
    except OSError as e:
        logger.warning(f"[Audit] append failed ({e}) — event NOT recorded.")
        return False, None


def find_duplicates(events) -> Dict[str, List[Dict[str, Any]]]:
    """Group truly-duplicate entries by (event_id, worker).

    Only groups with >1 member are returned; the key is rendered as
    "event_id @ worker" for readability.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        if isinstance(ev, dict) and ev.get("event_id"):
            key = f"{ev['event_id']} @ {ev.get('worker', '?')}"
            groups.setdefault(key, []).append(ev)
    return {k: v for k, v in groups.items() if len(v) > 1}


def read_audit(storage_dir: str, limit: int = 500
               ) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]], Dict[str, Any]]:
    """Returns (events newest-last, seq gaps, info). Malformed lines skipped.

    Gaps = sequence ranges present in neither the journal nor the disclosed
    trim count — i.e. genuinely unexplained loss. Accounted trims (from the
    meta sidecar) are reported via info, not as gaps.
    """
    path, _ = _paths(storage_dir)
    events: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = _json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
                    events.append(rec)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"[Audit] read failed ({e}).")
    events.sort(key=lambda r: r["seq"])
    meta = _read_meta(storage_dir)
    trimmed = meta.get("trimmed_lines", 0)
    # Lowest seq we can account for: 1..trimmed were deliberately dropped.
    # Gaps are computed over the COMPLETE retained sequence BEFORE the
    # presentation limit is applied: a limited page must never invent a gap
    # merely because earlier records were omitted from the returned page.
    gaps: List[Tuple[int, int]] = []
    if events:
        lo = max(1, trimmed + 1)
        prev = lo - 1
        for rec in events:
            if rec["seq"] > prev + 1:
                gaps.append((prev + 1, rec["seq"] - 1))
            prev = rec["seq"]
    page = events[-limit:]
    info = {"retention_cap": AUDIT_CAP_LINES, "trimmed_lines": trimmed,
            "events": len(page), "total_retained": len(events)}
    return page, gaps, info
