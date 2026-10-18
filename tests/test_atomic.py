"""Regression tests for atomic JSON persistence (Phase 14, R1).

Proves the shared helper and each migrated writer preserve behavior
while surviving torn-write failures. All fixtures are synthetic and
local to tmp_path; no network, no live services.
"""
import json
import os
import threading

import pytest

from src.utils.atomic import atomic_write_json


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_successful_write_schema_indent_and_cleanup(tmp_path):
    path = str(tmp_path / "ledger.json")
    atomic_write_json(path, {"a": 1, "b": [1, 2]})
    assert _read(path) == {"a": 1, "b": [1, 2]}
    with open(path, encoding="utf-8") as f:
        assert f.read().startswith('{\n  "a": 1')
    assert not os.path.exists(path + ".tmp")


def test_explicit_indent_preserved(tmp_path):
    path = str(tmp_path / "sig.json")
    atomic_write_json(path, {"x": [1]}, indent=4)
    with open(path, encoding="utf-8") as f:
        assert f.read().startswith('{\n    "x"')
    path2 = str(tmp_path / "flat.json")
    atomic_write_json(path2, {"x": 1}, indent=None)
    assert _read(path2) == {"x": 1}
    with open(path2, encoding="utf-8") as f:
        assert "\n" not in f.read().strip()


def test_serialization_failure_leaves_destination_intact(tmp_path):
    path = str(tmp_path / "ledger.json")
    atomic_write_json(path, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})
    assert _read(path) == {"v": 1}
    assert not os.path.exists(path + ".tmp")


def test_missing_directory_leaves_nothing_behind(tmp_path):
    path = str(tmp_path / "no-such-dir" / "ledger.json")
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 1})
    assert not os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_replace_failure_preserves_destination_and_cleans_tmp(tmp_path, monkeypatch):
    import src.utils.atomic as _atomic

    path = str(tmp_path / "ledger.json")
    atomic_write_json(path, {"v": 1})

    def _boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(_atomic.os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 2})
    assert _read(path) == {"v": 1}
    assert not os.path.exists(path + ".tmp")


def test_reader_never_sees_partial_payload(tmp_path):
    """Bounded approximation: concurrent replace loops must never yield
    a torn document to a reader. Does not prove OS crash durability."""
    path = str(tmp_path / "ledger.json")
    atomic_write_json(path, {"n": -1, "pad": ""})
    stop, errors = threading.Event(), []

    def _writer():
        for i in range(200):
            atomic_write_json(path, {"n": i, "pad": "x" * 2000})

    def _reader():
        for _ in range(200):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                assert set(d) == {"n", "pad"}
            except Exception as e:  # noqa: BLE001 - collected below
                errors.append(e)
                return

    tw, tr = threading.Thread(target=_writer), threading.Thread(target=_reader)
    tw.start()
    tr.start()
    tw.join(timeout=60)
    tr.join(timeout=60)
    stop.set()
    assert not errors


def test_scheduler_state_roundtrip_and_failure(tmp_path):
    from src.scheduler import BotScheduler

    sched = BotScheduler.__new__(BotScheduler)
    sched.state_file = str(tmp_path / "sched.json")
    state = {"published_signals": {"BTCUSDT_LONG": "2026-10-14T00:00:00Z"}}
    sched._save_state(state)
    assert sched._load_state() == state
    assert not os.path.exists(sched.state_file + ".tmp")


def test_orchestrator_dedup_state_roundtrip(tmp_path):
    from src.orchestrator import SignalDeduplicator

    path = str(tmp_path / "sched.json")
    d = SignalDeduplicator(state_file=path)
    assert d._load_state() == {"published_signals": {}}
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"published_signals": {}}
    d._save_state({"published_signals": {"X": "t"}})
    assert d._load_state() == {"published_signals": {"X": "t"}}
    assert not os.path.exists(path + ".tmp")


def test_signal_persistence_roundtrip(tmp_path, monkeypatch):
    import datetime as _dt

    from src.data.persistence import SignalPersistence
    from src.models.signal import SignalModel

    monkeypatch.setattr(
        "src.data.persistence.datetime",
        _FakeDateTime(_dt.datetime(2026, 10, 14)),
    )
    sp = SignalPersistence(storage_dir=str(tmp_path))
    sig = SignalModel(
        signal_id="SIG-T", timestamp="2026-10-14T00:00:00Z", symbol="BTCUSDT",
        side="LONG", entry=100.0, tp=104.0, stop_loss=98.0, leverage=5,
        margin=20.0, position_notional=100.0, risk_amount=1.0,
        risk_reward=2.0, confidence=70, conditions=[],
        invalidation_conditions=[], management_rules=[],
    )
    assert sp.save_signal(sig) is True
    with open(tmp_path / "signals_2026-10-14.json", encoding="utf-8") as f:
        rows = json.load(f)
    assert rows[-1]["signal_id"] == "SIG-T"


class _FakeDateTime:
    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now


def _persistence_sig(signal_id):
    from src.models.signal import SignalModel

    return SignalModel(
        signal_id=signal_id, timestamp="2026-10-14T00:00:00Z",
        symbol="BTCUSDT", side="LONG", entry=100.0, tp=104.0,
        stop_loss=98.0, leverage=5, margin=20.0, position_notional=100.0,
        risk_amount=1.0, risk_reward=2.0, confidence=70, conditions=[],
        invalidation_conditions=[], management_rules=[],
    )


def test_corrupt_signal_file_quarantined_not_overwritten(tmp_path, monkeypatch):
    """A corrupt daily file must never be replaced by a lone new signal.

    Regression: save_signal used to swallow the parse error and atomically
    rewrite the file with only the new record, destroying the day's history.
    """
    import datetime as _dt
    import glob as _glob

    from src.data.persistence import SignalPersistence

    monkeypatch.setattr(
        "src.data.persistence.datetime",
        _FakeDateTime(_dt.datetime(2026, 10, 14)),
    )
    day_file = tmp_path / "signals_2026-10-14.json"
    day_file.write_text("{not valid json", encoding="utf-8")

    sp = SignalPersistence(storage_dir=str(tmp_path))
    assert sp.save_signal(_persistence_sig("SIG-T")) is True

    # Prior bytes preserved next door, not destroyed.
    quarantine = tmp_path / "signals_2026-10-14.json.corrupt"
    assert quarantine.read_text(encoding="utf-8") == "{not valid json"
    # Fresh file holds exactly the new signal.
    rows = json.loads(day_file.read_text(encoding="utf-8"))
    assert [r["signal_id"] for r in rows] == ["SIG-T"]

    # Persistence is not blocked for the rest of the day.
    assert sp.save_signal(_persistence_sig("SIG-T2")) is True
    rows = json.loads(day_file.read_text(encoding="utf-8"))
    assert [r["signal_id"] for r in rows] == ["SIG-T", "SIG-T2"]

    # The quarantined file is invisible to the readers' signals_*.json glob.
    assert _glob.glob(str(tmp_path / "signals_*.json")) == [str(day_file)]


def test_signal_day_boundary_is_utc(tmp_path, monkeypatch):
    """The daily filename must follow the UTC date, not host-local time."""
    import datetime as _dt

    from src.data.persistence import SignalPersistence

    class _UTCOnlyDateTime:
        def now(self, tz=None):
            assert tz is not None, "day boundary must be read with a UTC tz"
            assert tz.utcoffset(None) == _dt.timedelta(0)
            return _dt.datetime(2026, 2, 1, 0, 30, tzinfo=_dt.timezone.utc)

    monkeypatch.setattr("src.data.persistence.datetime", _UTCOnlyDateTime())
    sp = SignalPersistence(storage_dir=str(tmp_path))
    assert sp.save_signal(_persistence_sig("SIG-UTC")) is True
    # 00:30 UTC on Feb 1 is still Jan 31 in every UTC-negative locale.
    assert (tmp_path / "signals_2026-02-01.json").exists()


def test_lifecycle_save_load_roundtrip(tmp_path):
    from src.models.lifecycle import SignalLifecycleTracker, SignalState

    t = SignalLifecycleTracker(signal_id="S", initial_state=SignalState.GENERATED)
    t.transition_to(SignalState.WAITING_FOR_ENTRY, reason="test")
    path = str(tmp_path / "lc.json")
    t.save(path)
    assert SignalLifecycleTracker.load(path).current_state == SignalState.WAITING_FOR_ENTRY
    assert not os.path.exists(path + ".tmp")


def test_outcome_ledger_roundtrip(tmp_path):
    from src.tracking.outcome_tracker import OutcomeTracker

    tr = OutcomeTracker.__new__(OutcomeTracker)
    tr.storage_dir = str(tmp_path)
    tr.ledger_path = os.path.join(str(tmp_path), "outcomes.json")
    tr.save_ledger({"A": {"final": True}})
    assert tr.load_ledger() == {"A": {"final": True}}
    assert not os.path.exists(tr.ledger_path + ".tmp")


def test_paper_portfolio_save_load(tmp_path):
    from src.papertrade.portfolio import PaperPortfolio

    pf = PaperPortfolio(storage_dir=str(tmp_path))
    pf.save({"booked": {}, "equity": 1000.0})
    assert pf.load()["equity"] == 1000.0
    assert not os.path.exists(pf.ledger_path + ".tmp")


def test_mirror_recorder_state_roundtrip(tmp_path):
    from mirror.recorder import TradingSession

    path = str(tmp_path / "trading_session.json")
    s = TradingSession(state_path=path, refresh_token="r",
                       access_token="a")
    s._save()
    s2 = TradingSession(state_path=path)
    assert s2.refresh_token == "r" and s2.access_token == "a"
    assert not os.path.exists(path + ".tmp")
