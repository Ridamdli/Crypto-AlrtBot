"""Portable single-flight lock for shared storage.

Why this exists: rolling Railway deployments can run two scheduler loops
against the same volume. Every ledger here is read-modify-write, so
overlapping writers can tear files or double-POST. This lock makes the
overlap window small and observable. Callers MUST fail open (skip the guarded
section with a warning), never deadlock the scan loop.

Two mechanisms, chosen by `is_pg_enabled()`:

JSON backend — atomic create (O_CREAT|O_EXCL) of `<name>.lock` carrying
{owner, token, acquired_at}. A holder that dies leaves the file behind; any
acquirer treats a lock older than stale_after_s (by file mtime) as abandoned
and takes over. Corrupt-but-fresh files are never deleted (a live holder may
own them) — acquisition simply fails.

Postgres backend — `pg_try_advisory_lock(key)`, where key is a stable 63-bit
hash of the lock name. The lock is session-scoped, so this module keeps the
acquiring connection open until release_lock() unlocks it. A crashed process
cannot leak the lock: PostgreSQL releases advisory locks when the connection
dies, which is why `stale` is always False there and stale_after_s is unused.
On a non-PostgreSQL DATABASE_URL (the SQLite test backend) the advisory calls
are unavailable, so acquisition fails open.
"""
import hashlib as _hashlib
import json as _json
import os as _os
import time as _time
import uuid as _uuid

from sqlalchemy import text as _text
from sqlalchemy.exc import SQLAlchemyError

from src.db import get_engine, is_pg_enabled
from src.utils.logger import get_logger

logger = get_logger(__name__)

# token -> (open connection holding the advisory lock, key, owner)
_HELD: dict[str, tuple] = {}


def _advisory_key(name: str) -> int:
    """Stable across processes (unlike hash(), which is salted per run)."""
    digest = _hashlib.sha1(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _acquire_pg(name: str, owner: str) -> str | None:
    key = _advisory_key(name)
    try:
        conn = get_engine().connect()
    except (SQLAlchemyError, RuntimeError) as e:
        logger.warning(f"[Lock] postgres unavailable: {e}")
        return None
    try:
        got = bool(conn.execute(_text("SELECT pg_try_advisory_lock(:k)"),
                                {"k": key}).scalar())
    except SQLAlchemyError as e:
        logger.warning(f"[Lock] pg_try_advisory_lock failed: {e}")
        conn.close()
        return None
    if not got:
        conn.close()
        return None
    token = _uuid.uuid4().hex
    _HELD[token] = (conn, key, owner)
    return token


def _release_pg(token: str) -> bool:
    entry = _HELD.pop(token, None)
    if entry is None:
        return False
    conn, key, _owner = entry
    try:
        return bool(conn.execute(_text("SELECT pg_advisory_unlock(:k)"),
                                 {"k": key}).scalar())
    except SQLAlchemyError as e:
        logger.warning(f"[Lock] pg_advisory_unlock failed: {e}")
        # Discard the pooled DBAPI connection so PostgreSQL drops the lock
        # instead of parking it in the pool.
        conn.invalidate()
        return False
    finally:
        conn.close()


def _status_pg(name: str) -> dict:
    key = _advisory_key(name)
    try:
        with get_engine().connect() as conn:
            held = conn.execute(
                _text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                      "AND classid = :hi AND objid = :lo AND granted"),
                {"hi": key >> 32, "lo": key & 0xFFFFFFFF},
            ).scalar()
    except (SQLAlchemyError, RuntimeError) as e:
        logger.warning(f"[Lock] pg_locks read failed: {e}")
        return {"held": False, "owner": None, "stale": False}
    # PostgreSQL drops advisory locks with the connection, so a held lock is
    # never stale and its owner is not identifiable from pg_locks.
    return {"held": bool(held), "owner": None, "stale": False}


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
    if is_pg_enabled():
        return _acquire_pg(name, owner)
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
    if is_pg_enabled():
        return _release_pg(token)
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
    if is_pg_enabled():
        return _status_pg(name)
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
