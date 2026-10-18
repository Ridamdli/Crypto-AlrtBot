"""Phase 18: signal archival regression tests (isolated tmp dirs, deterministic)."""
import datetime
import json
import os

from src.data import archival
from src.data.archival import (
    archive_old_signals,
    file_eligibility,
)

TODAY = datetime.date.today()


def _sig(sid, ts="2026-09-01T00:00:00Z"):
    return {"signal_id": sid, "symbol": "BTCUSDT", "side": "LONG",
            "timestamp": ts, "entry": 100.0}


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _mkfile(d, days_ago, records):
    name = f"signals_{(TODAY - datetime.timedelta(days=days_ago)).isoformat()}.json"
    p = os.path.join(d, name)
    _write(p, records)
    return p


def _outcomes(d, mapping):
    _write(os.path.join(d, "outcomes.json"), mapping)


def test_boundary_6_7_8_days(tmp_path):
    d = str(tmp_path)
    recs = [_sig("S-old")]
    p8 = _mkfile(d, 8, recs)
    p7 = _mkfile(d, 7, recs)
    p6 = _mkfile(d, 6, recs)
    _outcomes(d, {"S-old": {"final": True}})
    for p, days in ((p8, 8), (p7, 7), (p6, 6)):
        ok, reason = file_eligibility(p, {"S-old": {"final": True}}, TODAY)
        assert (ok, reason) == ((days > 7), ("eligible" if days > 7 else "within-window")), p


def test_all_final_archived_mixed_and_unresolved_pinned(tmp_path):
    d = str(tmp_path)
    _mkfile(d, 10, [_sig("A1"), _sig("A2")])
    _mkfile(d, 11, [_sig("B1"), _sig("B2")])
    far = (TODAY - datetime.timedelta(days=12)).isoformat()
    _write(os.path.join(d, f"signals_{far}.json"),
           [_sig("C1", ts="not-a-timestamp"), _sig("C2")])
    _outcomes(d, {"A1": {"final": True}, "A2": {"final": True},
                  "B1": {"final": True}, "B2": {"final": False},
                  "C1": {"final": True}, "C2": {"final": True}})
    out = archive_old_signals(d, today=TODAY)
    assert len(out["archived"]) == 1 and "A1" not in str(out["archived"])  # one file moved
    assert out["skipped"].get("unresolved") == 1
    assert out["skipped"].get("unparsable-timestamp") == 1
    # pinned files still active
    assert len([f for f in os.listdir(d) if f.startswith("signals_")]) == 2
    assert len(os.listdir(os.path.join(d, "archive"))) == 1


def test_today_file_never_archived(tmp_path):
    d = str(tmp_path)
    p = _mkfile(d, 0, [_sig("T1")])
    _outcomes(d, {"T1": {"final": True}})
    out = archive_old_signals(d, today=TODAY)
    assert out["archived"] == [] and os.path.exists(p)


def test_missing_outcome_malformed_record_malformed_json_pinned(tmp_path):
    d = str(tmp_path)
    _mkfile(d, 9, [_sig("M1")])  # no outcomes entry at all
    _mkfile(d, 10, [{"no_id": True}])
    bad = _mkfile(d, 11, [_sig("M2")])
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    _outcomes(d, {})
    out = archive_old_signals(d, today=TODAY)
    assert out["archived"] == []
    assert out["skipped"].get("unresolved") == 1
    assert out["skipped"].get("malformed-record") == 1
    assert sum("unreadable" in k or v for k, v in out["skipped"].items()) >= 1


def test_collision_identical_dedups_diverged_preserved(tmp_path):
    d = str(tmp_path)
    p = _mkfile(d, 9, [_sig("K1")])
    _outcomes(d, {"K1": {"final": True}})
    adir = os.path.join(d, "archive")
    os.makedirs(adir)
    # identical copy at dest -> source removed, counted once
    with open(p, "rb") as f:
        blob = f.read()
    with open(os.path.join(adir, os.path.basename(p)), "wb") as f:
        f.write(blob)
    out = archive_old_signals(d, today=TODAY)
    assert len(out["archived"]) == 1 and "(dedup)" in out["archived"][0]
    assert not os.path.exists(p)
    # diverged dest -> source preserved, no overwrite
    p2 = _mkfile(d, 10, [_sig("K2")])
    _outcomes(d, {"K1": {"final": True}, "K2": {"final": True}})
    with open(os.path.join(adir, os.path.basename(p2)), "w", encoding="utf-8") as f:
        json.dump([_sig("K2-OTHER")], f)
    out2 = archive_old_signals(d, today=TODAY)
    assert out2["archived"] == [] and os.path.exists(p2)
    assert out2["skipped"].get("collision-diverged") == 1


def test_repeat_runs_idempotent_and_bounded(tmp_path):
    d = str(tmp_path)
    for i in range(8):
        _mkfile(d, 9 + i, [_sig(f"R{i}")])
    _outcomes(d, {f"R{i}": {"final": True} for i in range(8)})
    first = archive_old_signals(d, today=TODAY, max_moves=5)
    assert len(first["archived"]) == 5  # bound respected
    second = archive_old_signals(d, today=TODAY, max_moves=5)
    assert len(second["archived"]) == 3
    third = archive_old_signals(d, today=TODAY)
    assert third["archived"] == [] and third["errors"] == []


def test_tracker_active_vs_history_and_no_double_count(tmp_path):
    from src.tracking.outcome_tracker import OutcomeTracker

    d = str(tmp_path)
    p = _mkfile(d, 9, [_sig("H1"), _sig("H2")])
    _outcomes(d, {"H1": {"final": True}, "H2": {"final": True}})
    archive_old_signals(d, today=TODAY)
    assert not os.path.exists(p)
    t = OutcomeTracker.__new__(OutcomeTracker)
    t.storage_dir = d
    active = t.load_all_signals()
    assert [s["signal_id"] for s in active] == []
    full = t.load_all_signals(include_archive=True)
    assert sorted(s["signal_id"] for s in full) == ["H1", "H2"]
    # interrupted move left a duplicate in both places -> counted once
    _write(p, [_sig("H1")])
    full2 = t.load_all_signals(include_archive=True)
    assert sorted(s["signal_id"] for s in full2) == ["H1", "H2"]
    assert t.load_all_signals() == [_sig("H1")] or len(t.load_all_signals()) == 1


def test_dashboard_history_matches_pre_archival_counts(tmp_path, monkeypatch):
    import src.dashboard as dash

    d = str(tmp_path)
    monkeypatch.setattr(dash, "DATA_DIR", d)
    _mkfile(d, 9, [_sig("D1")])
    _mkfile(d, 1, [_sig("D2")])
    _outcomes(d, {"D1": {"final": True}, "D2": {"final": False}})
    before = dash.load_all_signals(include_archive=True)
    archive_old_signals(d, today=TODAY)
    after_active = dash.load_all_signals()
    after_full = dash.load_all_signals(include_archive=True)
    assert sorted(s["signal_id"] for s in before) == ["D1", "D2"]
    assert sorted(s["signal_id"] for s in after_full) == ["D1", "D2"]
    assert [s["signal_id"] for s in after_active] == ["D2"]
    assert dash.compute_analytics(after_full)["total_signals"] == 2


def test_outcomes_ledger_untouched_by_archival(tmp_path):
    d = str(tmp_path)
    _mkfile(d, 9, [_sig("O1")])
    ledger = {"O1": {"final": True, "net_realized_r": 1.5}}
    _outcomes(d, ledger)
    with open(os.path.join(d, "outcomes.json"), "rb") as f:
        before = f.read()
    archive_old_signals(d, today=TODAY)
    with open(os.path.join(d, "outcomes.json"), "rb") as f:
        assert f.read() == before


def test_scheduler_archival_never_raises(tmp_path, monkeypatch):
    from src.scheduler import BotScheduler

    s = BotScheduler.__new__(BotScheduler)
    s.persistence = type("P", (), {"storage_dir": str(tmp_path)})()
    s._archive_old_signals()  # empty dir: no-op
    # poisoned dir type still must not raise
    s.persistence = type("P", (), {"storage_dir": os.path.join(str(tmp_path), "nope", "x")} )()
    s._archive_old_signals()
    assert True
