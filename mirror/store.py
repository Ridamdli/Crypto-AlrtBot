"""Mirror ledgers: seen source ids + mirrored copies + audit. Own dir only.

File layout inside data_dir (default mirror_data/):
  mirrored.json  {"seen": [pariston_baseIds...],
                  "positions": {source_baseId: {...}}}
  mirror_audit.jsonl  append-only event log, capped
Writes are atomic (tmp + os.replace). Never touches the main bot's
data_store/ — the two services share nothing on disk.
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
                    data.setdefault("positions", {})
                    return data
        except (FileNotFoundError, ValueError, OSError):
            pass
        return {"seen": [], "positions": {}}

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

    def record_mirror(self, source_base_id: str, rec: Dict[str, Any]) -> None:
        data = self.load()
        if source_base_id not in data["seen"]:
            data["seen"].append(source_base_id)
        data["positions"][source_base_id] = {
            "symbol": rec.get("symbol"),
            "side": rec.get("side"),
            "mirrorBaseIds": rec.get("mirrorBaseIds", []),
            "size": rec.get("size"),
            "already_open": bool(rec.get("already_open", False)),
            "mirrored_at": int(time.time()),
            "closed": False,
        }
        self._save(data)

    def open_positions(self) -> Dict[str, Dict[str, Any]]:
        return {sid: p for sid, p in self.load()["positions"].items()
                if isinstance(p, dict) and not p.get("closed")}

    def mark_closed(self, source_base_id: str) -> None:
        data = self.load()
        pos = data["positions"].get(source_base_id)
        if isinstance(pos, dict):
            pos["closed"] = True
            pos["closed_at"] = int(time.time())
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
