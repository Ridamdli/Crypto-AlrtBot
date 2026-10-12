"""Phase B — sim-reservation mirror tests (TDD: module does not exist yet).

The local ledger is a RECONCILIATION/MONITORING MIRROR of the server-side
Invo sim bank. It must never block sharing, never fabricate rejections, and
must explicitly represent unknown/stale remote state.
"""
import threading

import pytest

from src.invo.sim_ledger import SimLedger


def _ledger(tmp_path, **kw):
    return SimLedger(storage_dir=str(tmp_path), **kw)


def test_reserve_and_release_roundtrip(tmp_path):
    led = _ledger(tmp_path)
    rec = led.reserve("SIG-1", "BTCUSDT", 10.0)
    assert rec["status"] == "reserved"
    assert led.snapshot()["reserved_total"] == 10.0
    out = led.release("SIG-1", credit=12.5, fee=0.1)
    assert out["released"] is True
    snap = led.snapshot()
    assert snap["reserved_total"] == 0.0
    assert snap["released_total"] == 12.5
    assert snap["fees_total"] == 0.1


def test_concurrent_reserves_never_lose_updates(tmp_path):
    led = _ledger(tmp_path)
    errors = []

    def worker(i):
        try:
            for j in range(10):
                led.reserve(f"SIG-{i}-{j}", "ETHUSDT", 5.0)
        except Exception as e:  # noqa: BLE001 - collected, asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    snap = led.snapshot()
    assert snap["reserved_count"] == 80
    assert snap["reserved_total"] == 80 * 5.0


def test_duplicate_reserve_is_idempotent_not_double_counted(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    rec = led.reserve("SIG-1", "BTCUSDT", 10.0)
    assert rec["duplicate"] is True
    assert led.snapshot()["reserved_total"] == 10.0


def test_reserve_is_advisory_never_blocks(tmp_path):
    """Even 10x over a hypothetical cap, reserve() records and warns —
    it must never raise or refuse (server remains the enforcer)."""
    led = _ledger(tmp_path)
    for i in range(20):
        rec = led.reserve(f"SIG-{i}", "BTCUSDT", 10.0)
        assert rec["status"] == "reserved"
    assert led.snapshot()["reserved_total"] == 200.0
    advice = led.advisory_available(remote_remaining=100.0, next_amount=10.0)
    assert advice["would_overdraft"] is True
    assert advice["stale"] is False


def test_boundary_exact_limit_fill(tmp_path):
    led = _ledger(tmp_path)
    for i in range(10):
        led.reserve(f"SIG-{i}", "BTCUSDT", 10.0)
    advice = led.advisory_available(remote_remaining=100.0, next_amount=0.01)
    assert advice["implied_available"] == 0.0
    assert advice["would_overdraft"] is True  # 0.01 over the line still counts


def test_release_unknown_id_is_explicit_not_silent(tmp_path):
    led = _ledger(tmp_path)
    out = led.release("NOPE", credit=5.0)
    assert out["released"] is False
    assert out["reason"] == "unknown-reservation"
    assert led.snapshot()["released_total"] == 0.0


def test_double_release_credits_once(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    first = led.release("SIG-1", credit=11.0, fee=0.1)
    second = led.release("SIG-1", credit=11.0, fee=0.1)
    assert first["released"] is True
    assert second["released"] is False
    assert second["reason"] == "already-released"
    assert led.snapshot()["released_total"] == 11.0
    assert led.snapshot()["fees_total"] == 0.1


def test_failed_open_releases_reservation(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    out = led.release("SIG-1", credit=0.0, fee=0.0, note="open-rejected")
    assert out["released"] is True
    assert led.snapshot()["reserved_total"] == 0.0


def test_gone_close_marks_terminal_without_credit(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    out = led.mark_gone("SIG-1", evidence="Investment not found")
    assert out["status"] == "gone"
    assert led.snapshot()["reserved_total"] == 0.0
    # Re-release after gone is a no-op, not a double credit.
    again = led.release("SIG-1", credit=10.0)
    assert again["released"] is False


def test_restart_recovery_reloads_reservations(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    led.reserve("SIG-2", "ETHUSDT", 5.0)
    led.release("SIG-2", credit=4.0)
    fresh = SimLedger(storage_dir=str(tmp_path))
    snap = fresh.snapshot()
    assert snap["reserved_total"] == 10.0
    assert snap["reserved_count"] == 1
    assert snap["released_total"] == 4.0


def test_concurrent_closes_credit_exactly_once(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    results = []

    def closer():
        results.append(led.release("SIG-1", credit=11.0))

    threads = [threading.Thread(target=closer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r["released"]) == 1
    assert led.snapshot()["released_total"] == 11.0


def test_precision_round_trip_fractional_amounts(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 55.35014379554033)
    led.release("SIG-1", credit=60.481896061401365, fee=0.006085051359516616)
    snap = led.snapshot()
    assert snap["reserved_total"] == 0.0
    assert snap["released_total"] == pytest.approx(60.481896061401365)
    assert snap["fees_total"] == pytest.approx(0.006085051359516616)


def test_reconcile_matches_remote_snapshot(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    led.reserve("SIG-2", "ETHUSDT", 10.0)
    rep = led.reconcile(remote_remaining=80.0,
                        remote_open={"SIG-1": 10.0, "SIG-2": 10.0})
    assert rep["status"] == "reconciled"
    assert rep["diff_reserved"] == 0.0
    assert rep["bank_drift"] == 0.0  # first snapshot sets the baseline
    second = led.reconcile(remote_remaining=85.0,
                           remote_open={"SIG-1": 10.0, "SIG-2": 10.0})
    assert second["status"] == "reconciled"
    assert second["bank_drift"] == 5.0  # drift disclosed, never enforced


def test_reconcile_flags_divergence(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    # Remote closed SIG-1 outside our view (e.g. TP hit, app-side action).
    rep = led.reconcile(remote_remaining=100.0, remote_open={})
    assert rep["status"] == "divergent"
    assert rep["diff_reserved"] == 10.0
    assert "SIG-1" in rep["local_only"]


def test_unknown_remote_state_is_explicit(tmp_path):
    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    rep = led.reconcile(remote_remaining=None, remote_open=None)
    assert rep["status"] == "unknown"
    assert "stale" in rep["note"].lower() or "unknown" in rep["note"].lower()
    # Unknown remote must never zero local books.
    assert led.snapshot()["reserved_total"] == 10.0


def test_prune_drops_only_old_resolved_records(tmp_path):
    from src.invo.sim_ledger import SimLedger, SIM_RESOLVED_TTL_MS
    led = _ledger(tmp_path)
    now_ms = 200_000_000_000
    # Ledger stamps are seconds-epoch (time.time()); TTL is milliseconds.
    old_s = (now_ms - SIM_RESOLVED_TTL_MS - 1) / 1000.0
    led.reserve("OLD-REL", "BTCUSDT", 10.0)
    led.release("OLD-REL", credit=11.0)
    led.reserve("OLD-GONE", "ETHUSDT", 5.0)
    led.mark_gone("OLD-GONE", "closed")
    led.reserve("FRESH-REL", "BTCUSDT", 10.0)
    led.release("FRESH-REL", credit=9.0)
    led.reserve("STILL-OPEN", "SOLUSDT", 7.0)
    # Backdate only the old terminal records; fresh + open keep real stamps.
    recs = led._state["reservations"]
    for sid in ("OLD-REL", "OLD-GONE"):
        recs[sid]["released_at"] = old_s
    dropped = SimLedger._prune_resolved(recs, now_ms=now_ms)
    assert dropped == 2
    assert "OLD-REL" not in recs and "OLD-GONE" not in recs
    assert "FRESH-REL" in recs and "STILL-OPEN" in recs
    # Persist (as reconcile does) and reload: a re-release of a pruned id
    # is unknown-safe, never double credit.
    led.reconcile(remote_remaining=100.0, remote_open={"STILL-OPEN": 7.0})
    led2 = SimLedger(storage_dir=str(tmp_path))
    out = led2.release("OLD-REL", credit=11.0)
    assert out == {"released": False, "reason": "unknown-reservation"}


def test_prune_keeps_unstamped_and_reserved_records(tmp_path):
    from src.invo.sim_ledger import SimLedger
    led = _ledger(tmp_path)
    led.reserve("NO-STAMP", "BTCUSDT", 10.0)
    led.release("NO-STAMP", credit=10.0)
    del led._state["reservations"]["NO-STAMP"]["released_at"]  # legacy shape
    led.reserve("ANCIENT-OPEN", "ETHUSDT", 5.0)
    dropped = SimLedger._prune_resolved(
        led._state["reservations"], now_ms=999_999_999_999_999)
    assert dropped == 0  # missing stamp kept; reserved kept at any age


def test_reconcile_prunes_stale_history_but_keeps_books(tmp_path):
    from src.invo.sim_ledger import SIM_RESOLVED_TTL_MS, SimLedger
    led = _ledger(tmp_path)
    led.reserve("OPEN", "BTCUSDT", 10.0)
    led.reserve("OLD", "ETHUSDT", 5.0)
    led.release("OLD", credit=4.0)
    led._state["reservations"]["OLD"]["released_at"] = (
        (200_000_000_000 - SIM_RESOLVED_TTL_MS - 1) / 1000.0)  # seconds-epoch
    rep = led.reconcile(remote_remaining=90.0,
                        remote_open={"OPEN": 10.0})
    assert rep["status"] == "reconciled"
    assert "OLD" not in led._state["reservations"]
    assert led.snapshot()["reserved_total"] == 10.0


def test_persistence_failure_surfaced_not_hidden(tmp_path, monkeypatch):
    import os as _os

    led = _ledger(tmp_path)
    led.reserve("SIG-1", "BTCUSDT", 10.0)
    # Simulate a failed atomic publish (disk full / permission loss).
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(_os, "replace", _boom)
    led.reserve("SIG-2", "ETHUSDT", 5.0)
    assert led.healthy is False
    # In-memory books intact despite the failed write.
    assert led.snapshot()["reserved_total"] == 15.0
