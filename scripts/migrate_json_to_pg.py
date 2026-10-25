"""One-shot JSON -> Postgres migration for the Window 1 archive (Step 2.4).

Idempotent by construction:
  * natural-key tables upsert (`merge`) on their primary key;
  * surrogate-key tables (exit_decisions, exit_events) are deleted for
    window='W1' and re-inserted in source order, so `decisions_for()` keeps
    the original insertion order;
  * single-row tables (paper_state, scheduler_state, invo_exit_sync) merge
    on id=1.

Every row keeps the verbatim source record (where the schema has a payload
column) plus `payload_sha256`, `source_file` and `window='W1'`, so `--verify`
can re-read the archive and prove the database matches it column by column.

Usage:
  python scripts/migrate_json_to_pg.py --source prod_evidence/2026-10-24 --dry-run
  python scripts/migrate_json_to_pg.py --source prod_evidence/2026-10-24
  python scripts/migrate_json_to_pg.py --source prod_evidence/2026-10-24 --verify

`--verify` writes, then verifies. Any mismatch is reported and exits 1; the
archive is never modified.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

# `python scripts/...` puts scripts/ on sys.path, not the repo root.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db import models as db
from src.db.engine import database_url, get_session_factory
from src.papertrade.portfolio import _booked_columns
from src.tracking.outcome_tracker import _outcome_columns

WINDOW = "W1"
INGESTED_AT = datetime.now(timezone.utc)

# Tables with a natural key: (name, model, key column).
KEYED: list[tuple[str, Any, str]] = [
    ("signals", db.Signal, "signal_id"),
    ("exit_plans", db.ExitPlan, "signal_id"),
    ("invalidated_signals", db.InvalidatedSignal, "signal_id"),
    ("exit_last_eval", db.ExitLastEval, "signal_id"),
    ("exit_seen_keys", db.ExitSeenKey, "decision_key"),
    ("outcomes", db.Outcome, "signal_id"),
    ("paper_booked", db.PaperBooked, "signal_id"),
    ("sim_reservations", db.SimReservation, "signal_id"),
    ("invo_shares", db.InvoShare, "signal_id"),
]

# One-row tables keyed on id=1.
SINGLETON: list[tuple[str, Any]] = [
    ("paper_state", db.PaperState),
    ("scheduler_state", db.SchedulerState),
    ("invo_exit_sync", db.InvoExitSync),
]

# No natural key: delete window='W1' and re-insert in source order.
ORDERED: list[tuple[str, Any]] = [
    ("exit_decisions", db.ExitDecision),
    ("exit_events", db.ExitEvent),
]

TABLES = [name for name, _m, _k in KEYED] + [name for name, _m in SINGLETON] \
    + [name for name, _m in ORDERED]

# Provenance columns are excluded from verification: ingested_at is the run
# clock, not source data, and the rest are recomputed from the source record.
_SKIP_VERIFY = {"ingested_at"}


def _sha(record: Any) -> str:
    canon = json.dumps(record, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _prov(source_file: str, record: Any) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "ingested_at": INGESTED_AT,
        "payload_sha256": _sha(record),
        "window": WINDOW,
    }


def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_optional(path: str) -> Any:
    """None when the file is absent; a parse error is fatal, never silent."""
    if not os.path.exists(path):
        return None
    return _read(path)


def _require(record: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if record.get(k) is None]
    if missing:
        raise ValueError(f"{where}: missing required field(s) {missing}")


def collect(source_dir: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Load the archive into insert-ready rows, keyed by table name."""
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
    notes: list[str] = []

    def src(name: str) -> str:
        """Archive-qualified source id, so two archives in one DB stay distinct."""
        return f"{os.path.normpath(source_dir).replace(os.sep, '/')}/{name}"

    for path in sorted(glob.glob(os.path.join(source_dir, "signals_*.json"))):
        fname = src(os.path.basename(path))
        for i, rec in enumerate(_read(path)):
            _require(rec, ("signal_id", "timestamp", "symbol", "side", "entry",
                           "tp", "stop_loss", "leverage", "margin",
                           "position_notional", "risk_amount", "risk_reward",
                           "confidence"), f"{fname}[{i}]")
            tables["signals"].append({
                "signal_id": rec["signal_id"],
                "timestamp": rec["timestamp"],
                "symbol": rec["symbol"],
                "side": rec["side"],
                "strategy_id": rec["strategy_id"],
                "strategy_version": rec["strategy_version"],
                "configuration_hash": rec.get("configuration_hash"),
                "entry": rec["entry"],
                "tp": rec["tp"],
                "stop_loss": rec["stop_loss"],
                "leverage": rec["leverage"],
                "margin": rec["margin"],
                "position_notional": rec["position_notional"],
                "risk_amount": rec["risk_amount"],
                "risk_reward": rec["risk_reward"],
                "confidence": rec["confidence"],
                "btc_regime": rec.get("btc_regime"),
                "scoring_breakdown": rec.get("scoring_breakdown"),
                "conditions": rec.get("conditions"),
                "invalidation_conditions": rec.get("invalidation_conditions"),
                "management_rules": rec.get("management_rules"),
                "payload": rec,
                **_prov(fname, rec),
            })

    ledger = _read_optional(os.path.join(source_dir, "exit_decisions.json"))
    if ledger is not None:
        fname = src("exit_decisions.json")
        for i, rec in enumerate(ledger.get("decisions") or []):
            _require(rec, ("decision_key", "signal_id", "action"),
                     f"{fname}.decisions[{i}]")
            tables["exit_decisions"].append({
                "decision_key": rec["decision_key"],
                "signal_id": rec["signal_id"],
                "action": rec["action"],
                "data_ts_ms": rec.get("data_ts_ms"),
                "decided_at_ms": rec.get("decided_at_ms"),
                "code_version": rec.get("code_version"),
                "config_version": rec.get("config_version"),
                "rationale": rec.get("rationale"),
                "assessments": rec.get("assessments"),
                "risk_guard": rec.get("risk_guard"),
                "rejected": rec.get("rejected"),
                "events_applied": rec.get("events_applied"),
                "plan_after": rec.get("plan_after"),
                "payload": rec,
                **_prov(fname, rec),
            })
        for sid, plan in (ledger.get("plans") or {}).items():
            tables["exit_plans"].append({
                "signal_id": sid,
                "basis_decision_key": plan.get("basis_decision_key"),
                "decided_at_ms": plan.get("decided_at_ms"),
                "version": plan.get("version"),
                "plan": plan,
                **_prov(fname, plan),
            })
        for sid, events in (ledger.get("events") or {}).items():
            for ev in events or []:
                tables["exit_events"].append({
                    "signal_id": sid,
                    "kind": ev.get("kind"),
                    "decision_key": ev.get("decision_key"),
                    "data_ts_ms": ev.get("data_ts_ms"),
                    "decided_at_ms": ev.get("decided_at_ms"),
                    "payload": ev,
                    **_prov(fname, ev),
                })
        for sid in ledger.get("invalidated") or []:
            tables["invalidated_signals"].append({
                "signal_id": sid, **_prov(fname, sid)})
        for sid, le in (ledger.get("last_eval") or {}).items():
            tables["exit_last_eval"].append({
                "signal_id": sid,
                "data_ts": le.get("data_ts"),
                "at_ms": le.get("at"),
                **_prov(fname, le),
            })
        for key in ledger.get("seen_keys") or []:
            tables["exit_seen_keys"].append({
                "decision_key": key, "first_seen_at": None,
                **_prov(fname, key)})

    outcomes = _read_optional(os.path.join(source_dir, "outcomes.json"))
    if outcomes is not None:
        fname = src("outcomes.json")
        for sid, rec in outcomes.items():
            tables["outcomes"].append({
                "signal_id": sid,
                **_outcome_columns(rec),
                "payload": rec,
                **_prov(fname, rec),
            })

    portfolio = _read_optional(os.path.join(source_dir, "paper_portfolio.json"))
    if portfolio is not None:
        fname = src("paper_portfolio.json")
        _require(portfolio, ("starting_equity", "equity", "peak_equity"),
                 fname)
        tables["paper_state"].append({
            "id": 1,
            "starting_equity": portfolio["starting_equity"],
            "equity": portfolio["equity"],
            "peak_equity": portfolio["peak_equity"],
            "version": 0,
            **_prov(fname, portfolio),
        })
        for sid, rec in (portfolio.get("booked") or {}).items():
            tables["paper_booked"].append({
                "signal_id": sid,
                **_booked_columns(rec),
                "payload": rec,
                **_prov(fname, rec),
            })

    sim = _read_optional(os.path.join(source_dir, "sim_reservations.json"))
    if sim is not None:
        fname = src("sim_reservations.json")
        if sim.get("baseline") is not None:
            notes.append(
                "sim_reservations.baseline has no column - not migrated "
                "(schema gap, Step 2.4b)")
        for sid, rec in (sim.get("reservations") or {}).items():
            tables["sim_reservations"].append({
                "signal_id": sid,
                "symbol": rec.get("symbol"),
                "amount": rec.get("amount"),
                "status": rec.get("status"),
                "duplicate": rec.get("duplicate"),
                "evidence": rec.get("evidence"),
                "note": rec.get("note"),
                "reserved_at": rec.get("reserved_at"),
                "released_at": rec.get("released_at"),
                "payload": rec,
                **_prov(fname, rec),
            })

    exit_sync = _read_optional(os.path.join(source_dir, "invo_exit_sync.json"))
    if exit_sync is not None:
        tables["invo_exit_sync"].append({
            "id": 1, "state": exit_sync,
            **_prov(src("invo_exit_sync.json"), exit_sync)})

    shares = _read_optional(os.path.join(source_dir, "invo_shares.json"))
    if shares is not None:
        for sid, rec in shares.items():
            tables["invo_shares"].append({
                "signal_id": sid, "payload": rec,
                **_prov(src("invo_shares.json"), rec)})

    scheduler = _read_optional(os.path.join(source_dir, "scheduler_state.json"))
    if scheduler is not None:
        tables["scheduler_state"].append({
            "id": 1,
            "cooldown_hours": scheduler.get("cooldown_hours"),
            "published_signals": scheduler.get("published_signals") or {},
            **_prov(src("scheduler_state.json"), scheduler)})

    return tables, notes


def write(tables: dict[str, list[dict[str, Any]]]) -> None:
    """All-or-nothing: one transaction for the whole archive."""
    with get_session_factory()() as s, s.begin():
        for name, model, _key in KEYED:
            for row in tables[name]:
                s.merge(model(**row))
        for name, model in SINGLETON:
            for row in tables[name]:
                s.merge(model(**row))
        for name, model in ORDERED:
            s.execute(delete(model).where(model.window == WINDOW))
            for row in tables[name]:
                s.add(model(**row))


def _compare(problems: list[str], label: str, expected: dict, actual: Any) -> None:
    for col, val in expected.items():
        if col in _SKIP_VERIFY:
            continue
        got = getattr(actual, col)
        if got != val:
            problems.append(f"{label}.{col}: expected {val!r}, got {got!r}")


def _verify_keyed(s: Session, problems: list[str], name: str, model: Any,
                  key: str, rows: list[dict[str, Any]],
                  files: set[str]) -> None:
    actual = {getattr(r, key): r for r in s.execute(
        select(model).where(model.window == WINDOW,
                            model.source_file.in_(files))).scalars()}
    expected_keys = {r[key] for r in rows}
    for missing in sorted(expected_keys - set(actual), key=str):
        problems.append(f"{name}: missing row {key}={missing}")
    for extra in sorted(set(actual) - expected_keys, key=str):
        problems.append(f"{name}: unexpected row {key}={extra}")
    for row in rows:
        db_row = actual.get(row[key])
        if db_row is not None:
            _compare(problems, f"{name}[{row[key]}]", row, db_row)


def _verify_ordered(s: Session, problems: list[str], name: str, model: Any,
                    rows: list[dict[str, Any]], files: set[str]) -> None:
    actual = list(s.execute(
        select(model).where(model.window == WINDOW,
                            model.source_file.in_(files))
        .order_by(model.id)).scalars())
    if len(actual) != len(rows):
        problems.append(f"{name}: expected {len(rows)} rows, found {len(actual)}")
    for i, (expected, db_row) in enumerate(zip(rows, actual)):
        _compare(problems, f"{name}[{i}]", expected, db_row)


def verify(tables: dict[str, list[dict[str, Any]]]) -> int:
    """Verify only the rows this source directory claims (by source_file), so
    two archives can coexist in one database."""
    problems: list[str] = []
    with get_session_factory()() as s:
        for name, model, key in KEYED:
            rows = tables[name]
            files = {r["source_file"] for r in rows}
            if files:
                _verify_keyed(s, problems, name, model, key, rows, files)
        for name, model in SINGLETON:
            rows = tables[name]
            files = {r["source_file"] for r in rows}
            if files:
                _verify_keyed(s, problems, name, model, "id", rows, files)
        for name, model in ORDERED:
            rows = tables[name]
            files = {r["source_file"] for r in rows}
            if files:
                _verify_ordered(s, problems, name, model, rows, files)
    total = sum(len(v) for v in tables.values())
    if problems:
        print(f"VERIFY FAILED: {len(problems)} mismatch(es) across {total} rows")
        for problem in problems[:20]:
            print(f"  {problem}")
        if len(problems) > 20:
            print(f"  ... {len(problems) - 20} more")
        return 1
    print(f"VERIFY OK: 0 mismatches across {len(tables)} tables / {total} rows")
    return 0


def _print_counts(tables: dict[str, list[dict[str, Any]]],
                  notes: list[str]) -> None:
    width = max(len(name) for name in tables)
    for name in TABLES:
        print(f"  {name:<{width}}  {len(tables[name]):>6}")
    for note in notes:
        print(f"  note: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="prod_evidence/2026-10-24",
                        help="directory holding the JSON archive")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report counts; never touches a database")
    parser.add_argument("--verify", action="store_true",
                        help="write, then prove the database matches the archive")
    args = parser.parse_args(argv)

    load_dotenv()
    if not args.dry_run and database_url() is None:
        print("DATABASE_URL is not set (env or .env); use --dry-run or "
              "configure .env", file=sys.stderr)
        return 2

    tables, notes = collect(args.source)
    print(f"source: {args.source}  window: {WINDOW}")
    if args.dry_run:
        _print_counts(tables, notes)
        print("dry run: no database writes")
        return 0

    write(tables)
    _print_counts(tables, notes)
    print("migration committed")
    if args.verify:
        return verify(tables)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
