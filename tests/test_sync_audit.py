"""Phase B — audit journal tests (TDD: src/invo/sync_audit.py missing).

Every sync attempt must leave a durable, correlated, redacted record —
or an explicitly surfaced gap. No network, no randomness.
"""
import json
import os

import pytest

from src.invo import sync_audit as audit


def _evt(**kw):
    base = {
        "correlation_id": "SIG-1:modify:corr-7f3a",
        "signal_id": "SIG-1",
        "symbol": "BTCUSDT",
        "event": "request_sent",
        "prev_tp1": 100.0, "prev_sl": 95.0,
        "desired_tp1": 101.0, "desired_sl": 96.0,
        "attempt": 1, "budget": 5,
        "worker": "test-worker",
        "req_ts_ms": 1000,
        "plan_version": 2,
    }
    base.update(kw)
    return base


def test_event_schema_complete_and_validated(tmp_path):
    ev = audit.build_event(**_evt())
    for field in audit.REQUIRED_FIELDS:
        assert field in ev, f"missing {field}"
    assert ev["event_id"] == "SIG-1:modify:corr-7f3a#1"  # deterministic, no uuid
    import pytest
    with pytest.raises(ValueError):
        audit.build_event(event="bogus-type")


def test_confirmation_values_validated():
    with pytest.raises(ValueError, match="unknown confirmation"):
        audit.build_event(**_evt(confirmation="authorative-response"))  # typo
    ev = audit.build_event(**_evt(confirmation="remote-readback"))
    assert ev["confirmation"] == "remote-readback"
    ev_none = audit.build_event(**_evt(confirmation=None))
    assert ev_none["confirmation"] is None


def test_event_types_closed_set():
    assert set(audit.EVENT_TYPES) >= {
        "plan_created", "update_requested", "request_sent", "confirmed",
        "retry_scheduled", "failed", "reconciled", "already_gone"}


def test_confirmation_values_closed_set():
    assert set(audit.CONFIRMATIONS) >= {
        "authoritative-response", "remote-readback", "unverified"}


def test_append_and_reload_roundtrip_with_correlation(tmp_path):
    assert audit.append_audit(str(tmp_path), _evt(attempt=1)) == (True, 1)
    assert audit.append_audit(str(tmp_path), _evt(attempt=2, event="retry_scheduled")) == (True, 2)
    events, gaps, info = audit.read_audit(str(tmp_path))
    assert gaps == []
    assert [e["seq"] for e in events] == [1, 2]
    assert {e["event"] for e in events} == {"request_sent", "retry_scheduled"}
    assert all(e["correlation_id"] == "SIG-1:modify:corr-7f3a" for e in events)
    assert info["retention_cap"] == audit.AUDIT_CAP_LINES


def test_gap_detection_after_line_loss(tmp_path):
    audit.append_audit(str(tmp_path), _evt(attempt=1))
    audit.append_audit(str(tmp_path), _evt(attempt=2))
    audit.append_audit(str(tmp_path), _evt(attempt=3))
    path = os.path.join(str(tmp_path), audit.AUDIT_NAME)
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    with open(path, "w", encoding="utf-8") as f:
        f.writelines([lines[0], lines[2]])  # seq 2 lost (crash/torn write)
    events, gaps, _ = audit.read_audit(str(tmp_path))
    assert [e["seq"] for e in events] == [1, 3]
    assert gaps == [(2, 2)]


def test_retention_cap_disclosed_and_counted(tmp_path):
    audit.append_audit(str(tmp_path), _evt(attempt=1), cap=2)
    audit.append_audit(str(tmp_path), _evt(attempt=2), cap=2)
    audit.append_audit(str(tmp_path), _evt(attempt=3), cap=2)
    events, gaps, info = audit.read_audit(str(tmp_path))
    assert [e["seq"] for e in events] == [2, 3]
    assert info["trimmed_lines"] == 1  # disclosed, not silent
    assert gaps == []  # accounted trims are not mystery gaps


def test_audit_write_failure_surfaced_not_raised(tmp_path):
    blocker = os.path.join(str(tmp_path), "blocker")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("x")
    # A regular file blocks directory creation -> makedirs raises OSError.
    ok, seq = audit.append_audit(os.path.join(blocker, "sub"), _evt())
    assert ok is False and seq is None  # surfaced, never raised


def test_redaction_strips_secrets(tmp_path):
    ev = audit.build_event(**_evt(
        response_summary="Bearer abc123 token=xyz password=hunter2 baseId=b1 ok"))
    ok, _ = audit.append_audit(str(tmp_path), ev)
    assert ok is True
    events, _, _ = audit.read_audit(str(tmp_path))
    blob = json.dumps(events[0]).lower()
    assert "abc123" not in blob and "hunter2" not in blob
    assert "bearer" not in blob and "password" not in blob
    assert "b1" in json.dumps(events[0])  # baseId correlation preserved


def test_malformed_lines_skipped_with_gap(tmp_path):
    audit.append_audit(str(tmp_path), _evt(attempt=1))
    path = os.path.join(str(tmp_path), audit.AUDIT_NAME)
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json}\n")
    audit.append_audit(str(tmp_path), _evt(attempt=2))
    events, gaps, _ = audit.read_audit(str(tmp_path))
    assert [e["seq"] for e in events] == [1, 2]
    assert gaps == []  # corrupt line is not a seq (seq assigned only on success)


def test_duplicate_identity_same_key_vs_workers(tmp_path):
    base = _evt(attempt=1)
    audit.append_audit(str(tmp_path), audit.build_event(**base))
    audit.append_audit(str(tmp_path), audit.build_event(**base))  # double append
    other = dict(base, worker="worker-2")
    audit.append_audit(str(tmp_path), audit.build_event(**other))
    events, _, _ = audit.read_audit(str(tmp_path))
    assert len(events) == 3  # nothing collapsed on read
    dupes = audit.find_duplicates(events)
    # Only the same-actor double append is a duplicate; the other worker's
    # same-correlation record is independent history under its own key.
    assert list(dupes) == ["SIG-1:modify:corr-7f3a#1 @ test-worker"]
    assert len(dupes["SIG-1:modify:corr-7f3a#1 @ test-worker"]) == 2
    workers = {e["worker"] for e in events}
    assert workers == {"test-worker", "worker-2"}  # cross-worker history kept


def test_limited_read_invents_no_gaps(tmp_path):
    for i in (1, 2, 3, 4, 5):
        audit.append_audit(str(tmp_path), _evt(attempt=i))
    events, gaps, info = audit.read_audit(str(tmp_path), limit=2)
    assert [e["seq"] for e in events] == [4, 5]  # page only
    assert gaps == []  # no gap invented by the page window
    assert info["total_retained"] == 5


def test_genuine_gap_survives_paging(tmp_path):
    for i in (1, 2, 4, 5):
        audit.append_audit(str(tmp_path), _evt(attempt=i))
    # Forge the hole the appender would never create: drop seq 3 by hand.
    import os as _os
    path = _os.path.join(str(tmp_path), audit.AUDIT_NAME)
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f.readlines()
                 if '"seq": 3,' not in ln and '"seq":3' not in ln.replace(" ", "")]
    assert len(lines) == 3
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    events, gaps, _ = audit.read_audit(str(tmp_path), limit=2)
    assert [e["seq"] for e in events] == [2, 4]  # page window
    assert gaps == [(3, 3)]  # genuine hole still reported despite paging


def test_final_state_transitions_recorded(tmp_path):
    for i, (evt, state) in enumerate([
            ("update_requested", "pending"),
            ("request_sent", "in_flight"),
            ("retry_scheduled", "retryable"),
            ("confirmed", "confirmed")], start=1):
        audit.append_audit(str(tmp_path), _evt(attempt=i, event=evt,
                                               final_state=state))
    events, _, _ = audit.read_audit(str(tmp_path))
    assert [e["final_state"] for e in events] == [
        "pending", "in_flight", "retryable", "confirmed"]
