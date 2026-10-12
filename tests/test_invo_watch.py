"""Tests for the bounded production observer (all fixture-local, fast).

Nothing here touches production: main() runs against tmp data dirs with
1-2s windows. Live-read paths are unit-tested via _confirm_absent_closes
with synthetic snapshots only.
"""
import io
import json
import os
import sys

from scripts.invo_watch import (
    _as_ms,
    _confirm_absent_closes,
    _corr_kind,
    _pct,
    main as watch_main,
)


def test_corr_kind_valid_and_malformed():
    assert _corr_kind("SIG-1:modify:abc12345") == "modify"
    assert _corr_kind("SIG-1:close:zz") == "close"
    assert _corr_kind("no-delimiter") == ""
    assert _corr_kind("") == ""
    assert _corr_kind(None) == ""
    assert _corr_kind("SIG-1:") == ""
    assert _corr_kind(12345) == ""


def test_as_ms_valid_missing_invalid():
    assert _as_ms(1000) == 1000
    assert _as_ms("2000") == 2000
    assert _as_ms(None) is None
    assert _as_ms("soon") is None
    assert _as_ms(0) is None  # zero would masquerade as 1970
    assert _as_ms(-5) is None


def test_pct_needs_samples():
    assert _pct([], 0.5) is None
    assert _pct([3.0], 0.5) is None
    assert _pct([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0


def _write(dirpath, name, obj):
    with open(os.path.join(dirpath, name), "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)


def _journal_line(**kw):
    base = {"seq": 1, "correlation_id": "S1:modify:aa11", "signal_id": "S1",
            "event": "request_sent", "req_ts_ms": 1000,
            "desired_tp1": 101.0, "desired_sl": 96.0, "attempt": 1}
    base.update(kw)
    return json.dumps(base) + "\n"


def test_bounded_run_counts_malformed_not_crashes(tmp_path, monkeypatch, capsys):
    d = str(tmp_path)
    _write(d, "scan_history.json", [{"timestamp": "2026-10-11T00:00:00Z"}])
    _write(d, "invo_sync_audit.jsonl",
           _journal_line() + "{not json}\n" +
           _journal_line(seq=2, correlation_id="BROKEN-NO-DELIM", attempt=1))
    monkeypatch.setattr(sys, "argv",
                        ["invo_watch", "--duration-s", "1", "--interval-s", "1",
                         "--data-dir", d])
    assert watch_main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["eligible_observed"] == 1
    assert report["malformed_records"] == 1
    assert report["heartbeats_observed"] >= 1
    assert report["requests_used"] == 0  # ledger-only: zero API requests


def test_latency_none_when_timestamps_missing(tmp_path, monkeypatch, capsys):
    d = str(tmp_path)
    _write(d, "scan_history.json", [])
    _write(d, "invo_sync_audit.jsonl",
           _journal_line(req_ts_ms=None) +
           _journal_line(seq=2, event="confirmed", req_ts_ms=None,
                         confirmation="authoritative-response"))
    monkeypatch.setattr(sys, "argv",
                        ["invo_watch", "--duration-s", "1", "--interval-s", "1",
                         "--data-dir", d])
    assert watch_main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["confirmed"] == 1
    assert report["latency_s"] == {"median": None, "p95": None, "n": 0,
                                   "semantics": report["latency_s"]["semantics"]}
    assert "never zero-for-unknown" in report["latency_s"]["semantics"]


def test_out_write_failure_returns_nonzero_but_prints_report(
        tmp_path, monkeypatch, capsys):
    d = str(tmp_path)
    _write(d, "scan_history.json", [])
    _write(d, "invo_sync_audit.jsonl", "")
    bad_out = os.path.join(d, "no-such-dir", "deep", "report.json")
    monkeypatch.setattr(sys, "argv",
                        ["invo_watch", "--duration-s", "1", "--interval-s", "1",
                         "--data-dir", d, "--out", bad_out])
    assert watch_main() == 3
    out = capsys.readouterr()
    assert "eligible_observed" in out.out  # report still printed
    assert "ERROR" in out.err


def test_confirm_absent_closes_marks_readback():
    pending = {"S1:close:zz": {"first_seen_ms": 1000,
                               "desired": {"tp1": 1.0, "sl": 2.0},
                               "plan_version": 1}}
    resolved, latencies = [], []
    snap = {"investments": [{"baseId": "other"}]}
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _write(d, "invo_shares.json",
               {"S1": {"baseIds": ["b9"]}})
        _confirm_absent_closes(pending, resolved, latencies, snap, d, 5000)
    assert pending == {}
    assert resolved[0]["confirmation"] == "remote-readback"
    assert resolved[0]["latency_s"] == 4.0


def test_confirm_absent_closes_keeps_present_positions():
    pending = {"S1:close:zz": {"first_seen_ms": 1000,
                               "desired": {"tp1": 1.0, "sl": 2.0},
                               "plan_version": 1}}
    resolved, latencies = [], []
    snap = {"investments": [{"baseId": "b9"}]}
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _write(d, "invo_shares.json",
               {"S1": {"baseIds": ["b9"]}})
        _confirm_absent_closes(pending, resolved, latencies, snap, d, 5000)
    assert "S1:close:zz" in pending  # still present -> untouched
    assert resolved == []
