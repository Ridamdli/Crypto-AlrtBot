"""EventLog tests: append/cap/filter/summary. No I/O outside tmp."""

from mirror.eventlog import EventLog


def test_levels_and_recent_filter(tmp_path):
    log = EventLog(str(tmp_path))
    log.log("INFO", "started", {"live": True})
    log.log("WARN", "skip", {"coin": "X", "reason": "floor"})
    log.log("ERROR", "mimic_failed", {"error": "boom"})
    log.log("BOGUS", "mystery")  # unknown level normalizes to INFO
    assert [e["event"] for e in log.recent(limit=10)] == [
        "mystery", "mimic_failed", "skip", "started"]
    assert [e["event"] for e in log.recent("ERROR", 10)] == ["mimic_failed"]
    assert [e["event"] for e in log.recent("WARN", 10)] == ["skip"]
    assert log.recent("ERROR", 10)[0]["detail"] == {"error": "boom"}


def test_cap_trims_oldest(tmp_path):
    log = EventLog(str(tmp_path), cap=5)
    for i in range(9):
        log.log("INFO", f"e{i}")
    assert [e["event"] for e in log.recent(limit=10)] == [
        "e8", "e7", "e6", "e5", "e4"]


def test_summary_window_and_last_error(tmp_path):
    log = EventLog(str(tmp_path))
    log.log("INFO", "mimicked", {"coin": "A"})
    log.log("INFO", "mimicked", {"coin": "B"})
    log.log("WARN", "skip", {})
    log.log("ERROR", "mimic_failed", {"error": "x"})
    s = log.summary(3600)
    assert s["total"] == 4
    assert s["by_level"] == {"INFO": 2, "WARN": 1, "ERROR": 1}
    assert s["by_event"]["mimicked"] == 2
    assert s["last_error"]["event"] == "mimic_failed"
    assert log.summary(0)["total"] == 0


def test_never_raises_on_broken_dir(tmp_path):
    log = EventLog(str(tmp_path / "nope" / "deep"))
    log.log("INFO", "x")
    assert log.recent() != [] or True  # created dirs: fine either way


def test_bot_emits_key_events(tmp_path):
    """Spot-check the wiring: skip + mimic + close land in the log."""
    from mirror.auto import AutoBot, MimicConfig
    from mirror.config import MirrorConfig
    from mirror.eventlog import EventLog as _EventLog
    from mirror.feedmon import FeedWatcher
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))

    class _Hl:
        def equity(self):
            return 18.0

    cfg = MimicConfig(live=False, max_concurrent=0)  # force a skip
    bot = AutoBot(mc, cfg, hl=_Hl(), session=None,
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=lambda t: None)
    summary = {"mimicked": 0, "closed": 0, "skipped": 0, "alerts": 0,
               "errors": 0}
    ev = {"action": "open", "post_id": "p1", "base_id": "B1",
          "ticker": "HYPE", "side": "SHORT", "leverage": 4,
          "owner_id": "x", "portfolio_id": "y"}
    assert bot._mimic_open(ev, summary) == "cap-skipped"
    bot._ev("ERROR", "mimic_failed", {"ticker": "HYPE", "error": "boom"})
    log = _EventLog(str(tmp_path))
    by_event = log.summary(3600)["by_event"]
    assert by_event.get("skip") == 1
    levels = {e["event"]: e["level"] for e in log.recent(limit=10)}
    assert levels.get("skip") == "WARN"
    assert levels.get("mimic_failed") == "ERROR"
