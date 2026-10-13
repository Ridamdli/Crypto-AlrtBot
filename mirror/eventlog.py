"""Persistent machine event history for the mimic bot.

Railway runtime logs are ephemeral and noisy; this JSONL ledger lives on
the (volume-backed) data dir, survives restarts, and answers one question
fast: "is the bot healthy?" — errors, warnings and key infos with UTC
timestamps, capped so it can never fill the disk.

File: <data_dir>/mirror_events.jsonl, cap 5000 lines (oldest trimmed).
Levels: ERROR (needs eyes), WARN (degraded but running), INFO (proof of
life: mimics, closes, reconciles, startup, hourly heartbeat).
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

EVENTS_NAME = "mirror_events.jsonl"
EVENTS_CAP = 5000
LEVELS = ("ERROR", "WARN", "INFO")


class EventLog:
    def __init__(self, data_dir: str = "mirror_data",
                 name: str = EVENTS_NAME, cap: int = EVENTS_CAP):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, name)
        self.cap = cap
        os.makedirs(data_dir, exist_ok=True)

    def log(self, level: str, event: str,
            detail: Optional[Dict[str, Any]] = None) -> None:
        """Append one event. Never raises."""
        if level not in LEVELS:
            level = "INFO"
        entry = {"ts": int(time.time()), "level": level, "event": event,
                 "detail": detail or {}}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > self.cap:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-self.cap:])
        except OSError:
            pass

    def recent(self, level: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Newest-first (capped) events, optionally filtered by level."""
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()[-max(limit * 4, 200):]
        except OSError:
            return []
        out = []
        for ln in reversed(lines):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if level and e.get("level") != level:
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def summary(self, since_s: int = 86400) -> Dict[str, Any]:
        """Counts by level + event over the trailing window + last error."""
        cutoff = time.time() - since_s
        by_level: Dict[str, int] = {}
        by_event: Dict[str, int] = {}
        last_error = None
        total = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                for ln in f:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if int(e.get("ts", 0) or 0) < cutoff:
                        continue
                    total += 1
                    lv = e.get("level", "?")
                    by_level[lv] = by_level.get(lv, 0) + 1
                    ev = e.get("event", "?")
                    by_event[ev] = by_event.get(ev, 0) + 1
                    if lv == "ERROR":
                        last_error = e
        except OSError:
            pass
        return {"window_s": since_s, "total": total, "by_level": by_level,
                "by_event": by_event, "last_error": last_error}
