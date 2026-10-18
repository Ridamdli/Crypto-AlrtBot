"""Atomic filesystem writes shared by all ledger persistence paths.

A crash or kill mid-write must never leave a truncated destination file:
readers of these ledgers degrade to empty defaults on corrupt input, so a
torn write would silently reset deduplication, equity, or exit plans.
"""
import json
import os
from typing import Any


def atomic_write_json(path: str, data: Any, indent: int | None = 2) -> None:
    """Serialize `data` to JSON and atomically replace `path` with it.

    Serialize-first: a serialization failure raises before anything on
    disk is touched. The payload is written to `path + ".tmp"` in the
    same directory (same filesystem, so the rename is atomic), flushed
    and closed, then moved over the destination with os.replace().
    On any failure the temp file is removed best-effort and the existing
    destination is left untouched; the original exception propagates so
    callers keep their existing error contract.

    Does NOT create parent directories and does NOT guard against
    concurrent read-modify-write races: two writers can still interleave
    (last replace wins). Callers needing that must serialize externally.

    Encoding and indentation match the existing ledger convention
    (UTF-8, indent=2).
    """
    text = json.dumps(data, indent=indent)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
