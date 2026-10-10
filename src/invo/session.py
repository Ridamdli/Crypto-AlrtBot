"""
Invo session store: tokens live as long as possible, OTP is last resort.

- Persists access/refresh tokens + timestamps to data_store/invo_session.json
  (volume-backed on Railway, survives restarts; chmod 600 best-effort).
- OTP logins are rate-gated: min OTP_COOLDOWN_S between starts so a bad
  stretch can never look like an attack (default 30 min).
- Auth chain everywhere: memory token -> disk session -> silent refresh ->
  OTP self-login (cooldown-checked). Never passwords (platform is OTP-only).
"""
import json
import os
import time
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

SESSION_NAME = "invo_session.json"
ACTIVITY_NAME = "invo_activity.jsonl"
OTP_COOLDOWN_S = 1800
ACTIVITY_CAP = 2000


class SessionStore:
    def __init__(self, storage_dir: str = "data_store", name: str = SESSION_NAME):
        self.path = os.path.join(storage_dir, name)
        os.makedirs(storage_dir, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def save(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        data = self.load()
        data.update(patch)
        data["updated_at"] = int(time.time())
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as e:
            logger.error(f"[Invo] Failed to save session: {e}")
        return data

    def otp_allowed(self, cooldown_s: int = OTP_COOLDOWN_S) -> bool:
        """True only if no OTP start happened inside the cooldown window."""
        last = self.load().get("last_otp_at", 0) or 0
        try:
            return (time.time() - float(last)) >= cooldown_s
        except (TypeError, ValueError):
            return True

    def mark_otp(self) -> None:
        self.save({"last_otp_at": int(time.time())})


def log_activity(storage_dir: str, event: str, detail: Optional[Dict[str, Any]] = None,
                 cap: int = ACTIVITY_CAP) -> None:
    """Append-only activity history for the Invo bridge (debugging/audit)."""
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, ACTIVITY_NAME)
    entry = {"ts": int(time.time()), "event": event, "detail": detail or {}}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.error(f"[Invo] Failed to append activity: {e}")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > cap:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-cap:])
    except OSError:
        pass


def read_activity(storage_dir: str, limit: int = 100):
    try:
        with open(os.path.join(storage_dir, ACTIVITY_NAME), encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
    except OSError:
        return []
