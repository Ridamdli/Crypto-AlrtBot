"""Portable volume lockfile for single-flight sections on shared storage.

Why this exists: rolling Railway deployments can run two scheduler loops
against the same volume. Every ledger here is read-modify-write JSON, so
overlapping writers can tear files or double-POST. This lock makes the
overlap window small and observable — it does NOT guarantee distributed
mutual exclusion (network filesystems may not honor O_EXCL, clocks may
skew). Callers MUST fail open (skip the guarded section with a warning),
never deadlock the scan loop. The limitation is documented, not solved.

Mechanism: atomic create (O_CREAT|O_EXCL) of `<name>.lock` carrying
{owner, token, acquired_at}. A holder that dies leaves the file behind;
any acquirer treats a lock older than stale_after_s (by file mtime) as
abandoned and takes over. Corrupt-but-fresh files are never deleted
(a live holder may own them) — acquisition simply fails.
"""
import json as _json
import os as _os
import time as _time
import uuid as _uuid

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _path(storage_dir: str, name: str) -> str:
    return _os.path.join(storage_dir, f"{name}.lock")


def _read_payload(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return None


def _age_s(path: str) -> float:
    try:
        return _time.time() - _os.path.getmtime(path)
    except OSError:
        return float("inf")


def acquire_lock(storage_dir: str, name: str, owner: str,
                 stale_after_s: int = 120) -> str | None:
    """Acquire the named lock. Returns an opaque token, or None if held."""
    _os.makedirs(storage_dir, exist_ok=True)
    path = _path(storage_dir, name)
    token = _uuid.uuid4().hex
    payload = _json.dumps({"owner": owner, "token": token,
                           "acquired_at": _time.time()})
    flags = _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY
    try:
        fd = _os.open(path, flags, 0o644)
    except FileExistsError:
        pass
    except OSError as e:
        logger.warning(f"[Lock] cannot create {path}: {e}")
        return None
    else:
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except OSError:
            try:
                _os.unlink(path)
            except OSError:
                pass
            return None
        return token
    # File already exists: take over only if provably stale (by mtime),
    # whether parseable or not. A fresh lock — even an unreadable one —
    # may belong to a live holder: deny (fail safe, no deletion).
    if _age_s(path) < stale_after_s:
        return None
    try:
        _os.unlink(path)
    except OSError:
        return None
    return acquire_lock(storage_dir, name, owner, stale_after_s)


def release_lock(storage_dir: str, name: str, token: str) -> bool:
    """Release only if the token matches the current holder. Never raises."""
    path = _path(storage_dir, name)
    current = _read_payload(path)
    if current is None or current.get("token") != token:
        return False
    try:
        _os.unlink(path)
        return True
    except OSError:
        return False


def lock_status(storage_dir: str, name: str,
                stale_after_s: int = 120) -> dict:
    """Snapshot for logging/monitoring. Never raises."""
    path = _path(storage_dir, name)
    payload = _read_payload(path)
    if payload is None:
        # Distinguish missing/corrupt: corrupt files still block acquisition.
        try:
            _os.path.getmtime(path)
            return {"held": True, "owner": None, "stale": False,
                    "note": "unparseable lockfile"}
        except OSError:
            return {"held": False, "owner": None, "stale": False}
    age = _age_s(path)
    return {"held": True, "owner": payload.get("owner"),
            "stale": age >= stale_after_s, "age_s": round(age, 1)}
