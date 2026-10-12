"""Watcher tests: detect + notify, zero trading power. No network."""
from mirror.config import MirrorConfig
from mirror.store import MirrorStore
from mirror.watch import WatcherBot

SRC = "c7e2295e-e2fd-42db-9c2b-fb08ed2ec459"


def _pos(sid=SRC, ticker="ZEC"):
    return {"baseId": sid, "ticker": ticker, "side": "SHORT",
            "leverage": 10, "entry": 1293.8, "entrySim": 5}


def _setup():
    return {"symbol": "ZECUSDT", "side": "SHORT", "leverage": 10,
            "entry": 1293.8, "stop_loss": 1306.01, "tp1": 1267.50}


class FakeClient:
    access_token = "tok"

    def ensure_auth(self, **kwargs):
        return True


def _bot(tmp_path, positions, notes):
    cfg = MirrorConfig(email="e", app_password="p",
                       device_id="d", data_dir=str(tmp_path))
    return WatcherBot(cfg,
                      fetch_positions_fn=lambda *a: positions,
                      fetch_detail_fn=lambda *a, **k: _setup(),
                      client_factory=lambda: FakeClient(),
                      notify_fn=lambda text: notes.append(text) or True)


def test_new_setup_notifies_once_with_detail(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos()], notes)
    out = bot.run_once()
    assert out == {"new": 1, "closed": 0, "errors": 0}
    assert len(notes) == 1
    assert "ZEC" in notes[0] and "1267.5" in notes[0] and SRC in notes[0]
    out2 = bot.run_once()
    assert out2["new"] == 0 and len(notes) == 1
    assert SRC in MirrorStore(str(tmp_path)).seen_ids()


def test_vanished_source_reports_closed_once(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos()], notes)
    bot.run_once()
    assert len(notes) == 1
    bot2 = _bot(tmp_path, [], notes)
    out = bot2.run_once()
    assert out["closed"] == 1 and len(notes) == 2
    assert "closed" in notes[1]
    out2 = bot2.run_once()
    assert out2["closed"] == 0 and len(notes) == 2


def test_auth_failure_contained(tmp_path):
    cfg = MirrorConfig(data_dir=str(tmp_path))

    class _Bad:
        def ensure_auth(self, **k):
            raise RuntimeError("nope")

    bot = WatcherBot(cfg, client_factory=lambda: _Bad(),
                     notify_fn=lambda t: True)
    out = bot.run_once()
    assert out["errors"] == 1
