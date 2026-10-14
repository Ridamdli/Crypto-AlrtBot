"""Mirror ledgers: seen source ids + close reports + audit. Own dir only.

File layout inside data_dir (default mirror_data/):
  mirrored.json  {"seen": [pariston_baseIds...],
                  "reported_closed": [pariston_baseIds...]}
  mirror_audit.jsonl  append-only event log, capped
Writes are atomic (tmp + os.replace). The watcher holds no positions and
never touches the main bot's data_store/ — nothing shared on disk.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

LEDGER_NAME = "mirrored.json"
AUDIT_NAME = "mirror_audit.jsonl"
AUDIT_CAP = 5000


class MirrorStore:
    def __init__(self, data_dir: str = "mirror_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.ledger_path = os.path.join(data_dir, LEDGER_NAME)
        self.audit_path = os.path.join(data_dir, AUDIT_NAME)

    # ── ledger ──
    def load(self) -> Dict[str, Any]:
        try:
            with open(self.ledger_path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("seen", [])
                    data.setdefault("reported_closed", [])
                    return data
        except (FileNotFoundError, ValueError, OSError):
            pass
        return {"seen": [], "reported_closed": []}

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.ledger_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.ledger_path)

    def seen_ids(self) -> set:
        return set(self.load().get("seen", []))

    def mark_seen(self, source_base_id: str) -> None:
        data = self.load()
        if source_base_id not in data["seen"]:
            data["seen"].append(source_base_id)
            self._save(data)

    def unmark_seen(self, source_base_id: str) -> None:
        """Allow one fresh attempt for a previously skipped source id.
        Capital/concurrency skips are transient (floor lowered, slots
        freed) and must never bury a coin forever."""
        data = self.load()
        if source_base_id in data["seen"]:
            data["seen"].remove(source_base_id)
            self._save(data)

    def reported_closed_ids(self) -> set:
        return set(self.load().get("reported_closed", []))

    def mark_reported_closed(self, source_base_id: str) -> None:
        data = self.load()
        reported = set(data.get("reported_closed", []))
        reported.add(source_base_id)
        data["reported_closed"] = sorted(reported)
        self._save(data)

    # ── audit ──
    def audit(self, event: str, detail: Optional[Dict[str, Any]] = None) -> None:
        entry = {"ts": int(time.time()), "event": event, "detail": detail or {}}
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            return
        try:
            with open(self.audit_path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > AUDIT_CAP:
                with open(self.audit_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-AUDIT_CAP:])
        except OSError:
            pass

    def recent_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            with open(self.audit_path, encoding="utf-8") as f:
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
