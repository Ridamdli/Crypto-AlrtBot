"""Regression tests for uncapped publishing and scan heartbeat."""
import json
import os

from src.engines.scoring_engine import ScoringEngine
from src.scheduler import BotScheduler


def _cand(conf):
    c = {"symbol": "BTCUSDT", "side": "LONG", "confidence": conf}
    return c


def test_rank_candidates_uncapped_returns_all_viable():
    eng = ScoringEngine.__new__(ScoringEngine)
    eng.min_confidence = 60
    # bypass scoring: pre-set confidence and stub score_candidate
    eng.score_candidate = lambda cand, config=None: cand["confidence"]
    cands = [_cand(90), _cand(80), _cand(70), _cand(60), _cand(59)]
    out = eng.rank_candidates(cands, top_n=None)
    assert [c["confidence"] for c in out] == [90, 80, 70, 60]


def test_rank_candidates_top_n_still_caps_when_asked():
    eng = ScoringEngine.__new__(ScoringEngine)
    eng.min_confidence = 60
    eng.score_candidate = lambda cand, config=None: cand["confidence"]
    cands = [_cand(90), _cand(80), _cand(70)]
    out = eng.rank_candidates(cands, top_n=2)
    assert [c["confidence"] for c in out] == [90, 80]


def _full_cand(timestamp="2026-10-11T12:00:00Z"):
    return {
        "signal_id": "SIG-WIRE-1",
        "timestamp": timestamp,
        "symbol": "SOLUSDT",
        "side": "LONG",
        "strategy_id": "donchian_20",
        "entry": 110.20,
        "tp1": 112.40,
        "tp2": 113.50,
        "tp3": 114.60,
        "stop_loss": 108.55,
        "leverage": 5,
        "margin": 20.0,
        "position_notional": 100.0,
        "risk_amount": 1.65,
        "risk_reward_tp1": 1.33,
        "risk_reward_tp2": 2.0,
        "risk_reward_tp3": 2.67,
        "confidence": 75,
        "conditions": ["test"],
        "invalidation_conditions": ["test"],
        "management_rules": ["test"],
    }


def test_scan_cycle_routes_published_signals_to_paper_share(tmp_path):
    """run_scan_cycle must hand published signals to _share_paper_to_invo.

    Regression: the cycle once called the dead _share_to_invo hook (gated on
    unset INVO_ENABLED), so Telegram fired while Invo never attempted a post.
    """
    from types import SimpleNamespace

    state_file = str(tmp_path / "sched_state.json")

    class OnePipeline:
        def run_pipeline(self):
            return [_full_cand()]

    sched = BotScheduler(pipeline=OnePipeline(), state_file=state_file)
    sched.bot = SimpleNamespace(send_message=lambda msg: True)
    sched.persistence = SimpleNamespace(save_signal=lambda sig: None)
    sched._track_outcomes = lambda: None
    sched._supervise_exits = lambda: None
    sched._sync_exits_to_invo = lambda: None
    sched._archive_old_signals = lambda: None
    seen = {}
    sched._share_paper_to_invo = lambda published: seen.setdefault("n", len(published))
    out = sched.run_scan_cycle()
    assert len(out) == 1
    assert seen.get("n") == 1
    assert not hasattr(sched, "_share_to_invo")


def test_empty_scan_cycle_writes_heartbeat(tmp_path):
    state_file = str(tmp_path / "sched_state.json")

    class EmptyPipeline:
        def run_pipeline(self):
            return []

    sched = BotScheduler(pipeline=EmptyPipeline(), state_file=state_file)
    # Maintenance hooks stubbed: this test pins heartbeat output only and
    # must not read or write the real repository data_store/.
    sched._track_outcomes = lambda: None
    sched._supervise_exits = lambda: None
    sched._sync_exits_to_invo = lambda: None
    sched._archive_old_signals = lambda: None
    out = sched.run_scan_cycle()
    assert out == []
    hb_path = os.path.join(str(tmp_path), "scan_history.json")
    assert os.path.exists(hb_path)
    with open(hb_path, encoding="utf-8") as f:
        history = json.load(f)
    assert history[-1]["status"] == "ok"
    assert history[-1]["candidates"] == 0
    assert history[-1]["published"] == 0


def test_empty_cycle_still_runs_maintenance_hooks(tmp_path):
    """Quiet cycles must still track outcomes, supervise exits, and sync.

    Regression (found live 2026-10-11): run_scan_cycle returned early on
    zero candidates, so supervision/Invo-sync went silent on every quiet
    cycle while open paper positions still needed management.
    """
    state_file = str(tmp_path / "sched_state.json")

    class EmptyPipeline:
        def run_pipeline(self):
            return []

    sched = BotScheduler(pipeline=EmptyPipeline(), state_file=state_file)
    calls = []
    sched._track_outcomes = lambda: calls.append("track")
    sched._share_paper_to_invo = lambda published: calls.append(("share", list(published)))
    sched._supervise_exits = lambda: calls.append("supervise")
    sched._sync_exits_to_invo = lambda: calls.append("sync")
    sched._archive_old_signals = lambda: calls.append("archive")
    assert sched.run_scan_cycle() == []
    assert "track" in calls
    assert "supervise" in calls
    assert "sync" in calls
    assert "archive" in calls  # quiet branch reaches archival, stubbed off real data
    assert ("share", []) not in calls  # nothing published: nothing to share


def _wired_sched(tmp_path, send_fn):
    """Scheduler with one fixed candidate, stubbed sink + persistence."""
    from types import SimpleNamespace

    state_file = str(tmp_path / "sched_state.json")

    class OnePipeline:
        def run_pipeline(self):
            return [_full_cand()]

    sched = BotScheduler(pipeline=OnePipeline(), state_file=state_file)
    sched.bot = SimpleNamespace(send_message=send_fn)
    sched.persistence = SimpleNamespace(save_signal=lambda sig: None)
    sched._track_outcomes = lambda: None
    sched._supervise_exits = lambda: None
    sched._sync_exits_to_invo = lambda: None
    sched._share_paper_to_invo = lambda published: None
    sched._archive_old_signals = lambda: None
    return sched


def _ledger_keys(tmp_path):
    with open(tmp_path / "sched_state.json", encoding="utf-8") as f:
        return json.load(f).get("published_signals", {})


def test_failed_send_not_ledgered(tmp_path):
    """F3: a failed broadcast must not burn the 6h dedup window."""
    sched = _wired_sched(tmp_path, send_fn=lambda msg: False)
    assert sched.run_scan_cycle() == []
    assert _ledger_keys(tmp_path) == {}


def test_disabled_send_not_ledgered(tmp_path, monkeypatch):
    """F3: TELEGRAM_ENABLED=false surfaces as send_message False."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    from src.utils.telegram_bot import TelegramBot
    assert TelegramBot.enabled() is False
    sched = _wired_sched(tmp_path, send_fn=lambda msg: False)
    assert sched.run_scan_cycle() == []
    assert _ledger_keys(tmp_path) == {}


def test_successful_send_ledgered(tmp_path):
    sched = _wired_sched(tmp_path, send_fn=lambda msg: True)
    out = sched.run_scan_cycle()
    assert len(out) == 1
    assert _ledger_keys(tmp_path) == {"SOLUSDT_LONG": "2026-10-11T12:00:00Z"}


def test_failed_send_retries_next_cycle(tmp_path):
    """F3: fail once (no ledger), succeed next cycle (published)."""
    from types import SimpleNamespace

    calls = {"n": 0}

    def _flaky(msg):
        calls["n"] += 1
        return calls["n"] > 1

    sched = _wired_sched(tmp_path, send_fn=_flaky)
    assert sched.run_scan_cycle() == []  # failed: nothing published
    assert _ledger_keys(tmp_path) == {}
    out = sched.run_scan_cycle()  # send works now: published
    assert len(out) == 1
    assert "SOLUSDT_LONG" in _ledger_keys(tmp_path)


def test_successful_send_suppressed_within_cooldown(tmp_path):
    """Unchanged policy: a delivered signal stays deduplicated for 6h."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    class NowPipeline:
        def run_pipeline(self):
            return [_full_cand(timestamp=now)]

    from types import SimpleNamespace

    sched = BotScheduler(pipeline=NowPipeline(),
                         state_file=str(tmp_path / "sched_state.json"))
    sched.bot = SimpleNamespace(send_message=lambda msg: True)
    sched.persistence = SimpleNamespace(save_signal=lambda sig: None)
    sched._track_outcomes = lambda: None
    sched._supervise_exits = lambda: None
    sched._sync_exits_to_invo = lambda: None
    sched._share_paper_to_invo = lambda published: None
    sched._archive_old_signals = lambda: None
    assert len(sched.run_scan_cycle()) == 1
    assert sched.run_scan_cycle() == []  # same symbol_side, within cooldown


def test_normal_cycle_invokes_archival(tmp_path):
    """Publishing branch must reach archival without touching real data."""
    from types import SimpleNamespace

    class OnePipeline:
        def run_pipeline(self):
            return [_full_cand()]

    sched = BotScheduler(pipeline=OnePipeline(),
                         state_file=str(tmp_path / "sched_state.json"))
    sched.bot = SimpleNamespace(send_message=lambda msg: True)
    sched.persistence = SimpleNamespace(save_signal=lambda sig: None)
    sched._track_outcomes = lambda: None
    sched._supervise_exits = lambda: None
    sched._sync_exits_to_invo = lambda: None
    sched._share_paper_to_invo = lambda published: None
    calls = []
    sched._archive_old_signals = lambda: calls.append("archive")
    assert len(sched.run_scan_cycle()) == 1
    assert calls == ["archive"]


def _run_main(monkeypatch, cands, send_fn=None, use_real_bot=False):
    """Run src.main.main with stubbed pipeline/sinks. Returns (result, sent)."""
    import src.main as _main
    from types import SimpleNamespace

    sent = []

    class FakePipeline:
        def run_pipeline(self):
            return list(cands)

    if use_real_bot:
        from src.utils.telegram_bot import TelegramBot
        bot = TelegramBot()
    else:
        def _send(msg):
            sent.append(msg)
            return send_fn(msg) if send_fn is not None else True
        bot = SimpleNamespace(send_message=_send)
    monkeypatch.setattr(_main, "SignalPipeline", lambda: FakePipeline())
    monkeypatch.setattr(_main, "TelegramBot", lambda: bot)
    monkeypatch.setattr(_main, "SignalPersistence",
                        lambda: SimpleNamespace(save_signal=lambda sig: None))
    return _main.main(), sent


def test_main_counts_delivered_on_success(monkeypatch):
    result, sent = _run_main(monkeypatch, [_full_cand()])
    assert result == {"delivered": 1, "failed": 0, "candidates": 1}
    assert len(sent) == 1


def test_main_failed_send_not_counted_delivered(monkeypatch):
    result, sent = _run_main(monkeypatch, [_full_cand()],
                             send_fn=lambda msg: False)
    assert result == {"delivered": 0, "failed": 1, "candidates": 1}
    assert len(sent) == 1  # attempted once; no retry in one-shot path


def test_main_mixed_delivery(monkeypatch):
    other = _full_cand()
    other.update({"signal_id": "SIG-WIRE-2", "symbol": "ETHUSDT"})
    result, _ = _run_main(monkeypatch, [_full_cand(), other],
                          send_fn=lambda msg: "ETHUSDT" not in msg)
    assert result == {"delivered": 1, "failed": 1, "candidates": 2}


def test_main_disabled_send_reports_failure(monkeypatch):
    """TELEGRAM_ENABLED=false with the real bot: no network (no creds in
    this env), returns False, counted as failed not delivered."""
    import os
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result, _ = _run_main(monkeypatch, [_full_cand()], use_real_bot=True)
    assert result == {"delivered": 0, "failed": 1, "candidates": 1}


def test_main_empty_candidates(monkeypatch):
    result, sent = _run_main(monkeypatch, [])
    assert result == {"delivered": 0, "failed": 0, "candidates": 0}
    assert sent == []


def test_main_formatter_error_counts_failed(monkeypatch):
    import src.main as _main
    from types import SimpleNamespace

    class FakePipeline:
        def run_pipeline(self):
            return [_full_cand()]

    def _broken_format(*a, **k):
        raise RuntimeError("bad format")

    monkeypatch.setattr(_main, "SignalPipeline", lambda: FakePipeline())
    monkeypatch.setattr(_main, "TelegramBot",
                        lambda: SimpleNamespace(send_message=lambda msg: True))
    monkeypatch.setattr(_main, "SignalPersistence",
                        lambda: SimpleNamespace(save_signal=lambda sig: None))
    monkeypatch.setattr(_main.TelegramFormatter, "format_signal",
                        staticmethod(_broken_format))
    assert _main.main() == {"delivered": 0, "failed": 1, "candidates": 1}
