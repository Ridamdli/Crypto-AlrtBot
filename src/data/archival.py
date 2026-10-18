"""Signal-file archival: move fully-settled daily files out of the active glob.

A `signals_YYYY-MM-DD.json` file may be archived only when EVERY record in
it is demonstrably final per the outcomes ledger. Archival is a plain
os.replace() move into `data_store/archive/` (same filesystem, atomic:
crash leaves the file in exactly one place), so retries are naturally
idempotent and no manifest is needed.

What this deliberately does NOT do:
- never touches today's file or anything within the 7-day window;
- never archives a file containing a non-final, missing-outcome,
  malformed, or ambiguous record (those pin their file in place);
- never deletes records (archive/ retains full content);
- never changes settlement, dedup, or scoring semantics.
"""
import datetime as _datetime
import glob as _glob
import json as _json
import logging as _logging
import os as _os
from typing import Any

logger = _logging.getLogger(__name__)

ARCHIVE_DIR_NAME = "archive"
RETENTION_DAYS = 7
MAX_MOVES_PER_RUN = 5


def _parse_file_date(name: str) -> _datetime.date | None:
    """YYYY-MM-DD from a signals_YYYY-MM-DD.json basename, else None."""
    base = _os.path.basename(name)
    if not base.startswith("signals_") or not base.endswith(".json"):
        return None
    try:
        return _datetime.date(
            int(base[8:12]), int(base[13:15]), int(base[16:18]))
    except (ValueError, IndexError):
        return None


def _parse_ts_ms(ts: Any) -> int | None:
    """Signal timestamp -> epoch ms, mirroring the tracker's tolerance."""
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return int(ts) if ts > 100_000_000_000 else int(ts * 1000)
    if isinstance(ts, str):
        try:
            dt = _datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_datetime.timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _load_outcomes(storage_dir: str) -> dict[str, Any]:
    path = _os.path.join(storage_dir, "outcomes.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _load_records(path: str) -> tuple[list[Any] | None, str]:
    """Returns (records-or-None, reason). None means unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (FileNotFoundError, ValueError, OSError) as e:
        return None, f"unreadable ({e.__class__.__name__})"
    if not isinstance(data, list):
        return None, "not-a-list"
    return data, ""


def file_eligibility(path: str, outcomes: dict[str, Any],
                     today: _datetime.date) -> tuple[bool, str]:
    """Decide whether one signal file may be archived.

    Returns (eligible, reason). `reason` is "eligible" or a short
    machine-stable blocker code for rate-limited diagnostics.
    """
    file_date = _parse_file_date(path)
    if file_date is None:
        return False, "bad-filename"
    if file_date >= today - _datetime.timedelta(days=RETENTION_DAYS):
        return False, "within-window"
    records, why = _load_records(path)
    if records is None:
        return False, why
    for rec in records:
        if not isinstance(rec, dict) or not rec.get("signal_id"):
            return False, "malformed-record"
        if _parse_ts_ms(rec.get("timestamp")) is None:
            return False, "unparsable-timestamp"
        if not isinstance(outcomes.get(rec["signal_id"]), dict) or \
                not outcomes[rec["signal_id"]].get("final"):
            return False, "unresolved"
    return True, "eligible"


def archive_old_signals(
    storage_dir: str,
    outcomes: dict[str, Any] | None = None,
    today: _datetime.date | None = None,
    max_moves: int = MAX_MOVES_PER_RUN,
    warned: set[str] | None = None,
) -> dict[str, Any]:
    """Move eligible signal files to `<storage_dir>/archive/`.

    Pure file hygiene: never raises (per-file isolation), moves at most
    `max_moves` files, stops at the first file newer than the cutoff
    (glob is date-sorted). Returns
    {"archived": [names], "skipped": {reason: count}, "errors": [..]}.
    `warned` (caller-owned set) rate-limits repeat diagnostics per path.
    """
    if today is None:
        today = _datetime.datetime.now().date()
    if outcomes is None:
        outcomes = _load_outcomes(storage_dir)
    if warned is None:
        warned = set()
    summary: dict[str, Any] = {"archived": [], "skipped": {}, "errors": []}
    archive_dir = _os.path.join(storage_dir, ARCHIVE_DIR_NAME)

    def _note(path: str, reason: str) -> None:
        summary["skipped"][reason] = summary["skipped"].get(reason, 0) + 1
        key = f"{_os.path.basename(path)}:{reason}"
        if key not in warned:
            warned.add(key)
            logger.warning(f"[Archival] keeping {key} active ({reason}).")

    moved = 0
    for path in sorted(_glob.glob(_os.path.join(storage_dir, "signals_*.json"))):
        if moved >= max_moves:
            break
        try:
            eligible, reason = file_eligibility(path, outcomes, today)
        except Exception as e:  # never let one file break the pass
            summary["errors"].append(f"{_os.path.basename(path)}: {e}")
            continue
        if not eligible:
            # Sorted oldest-first: a file newer than the cutoff ends the
            # scan, but malformed/blocked older files must not mask newer
            # eligible ones behind them... they can't: older files sort
            # first, and only the within-window reason terminates.
            if reason == "within-window":
                break
            _note(path, reason)
            continue
        try:
            _os.makedirs(archive_dir, exist_ok=True)
            dest = _os.path.join(archive_dir, _os.path.basename(path))
            if _os.path.exists(dest):
                with open(path, "rb") as f_src, open(dest, "rb") as f_dst:
                    identical = f_src.read() == f_dst.read()
                if identical:
                    # A previous move completed (or the file was already
                    # archived): removing the leftover source converges.
                    _os.unlink(path)
                    summary["archived"].append(_os.path.basename(path)
                                              + " (dedup)")
                    moved += 1
                else:
                    _note(path, "collision-diverged")
            else:
                _os.replace(path, dest)
                summary["archived"].append(_os.path.basename(path))
                moved += 1
        except OSError as e:
            summary["errors"].append(f"{_os.path.basename(path)}: {e}")
    return summary
