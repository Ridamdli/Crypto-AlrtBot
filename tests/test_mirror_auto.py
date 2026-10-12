"""Auto-mimic tests: brain math + driver flow + cycle. No browser."""
import pytest

from mirror.auto import (AutoBot, ChromeDriver, MimicConfig, decide_live_size,
                         size_pct)
from mirror.config import MirrorConfig


def _cfg(**kw):
    base = dict(account_usd=18.38, max_usd_per_mimic=5.0,
                max_pct_per_mimic=25.0, max_concurrent=2,
                min_remaining_usd=3.0)
    base.update(kw)
    return MimicConfig(**base)


def test_brain_full_mimic():
    margin, reason = decide_live_size(18.38, 0.0, _cfg())
    assert margin == round(18.38 * 0.25, 2) and reason == "full-mimic"


def test_brain_flat_cap_binds():
    margin, _ = decide_live_size(100.0, 0.0, _cfg())
    assert margin == 5.0


def test_brain_floor_halts():
    margin, reason = decide_live_size(18.38, 16.0, _cfg())
    assert margin is None and "floor" in reason


def test_brain_blind_never():
    margin, reason = decide_live_size(0.0, 0.0, _cfg())
    assert margin is None and "bank" in reason


def test_size_pct_math():
    assert size_pct(4.6, 18.38) == 25.0
    assert size_pct(5.0, 100.0) == 5.0
    assert size_pct(1.0, 0.0) == 0.0


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    @property
    def value(self):
        return self

    def json(self):
        return self._payload


class FakePage:
    def __init__(self, confirm_ok=True, shots_change=True):
        self.viewport_size = {"width": 1280, "height": 720}
        self.clicks = []
        self.keys = []
        self.confirm_ok = confirm_ok
        self.shots_change = shots_change
        self.n_shots = 0
        self.closed = False

    def wait_for_timeout(self, ms):
        return None

    def screenshot(self):
        self.n_shots += 1
        if not self.shots_change:
            return b"shot-same"
        return b"shot-%d-chg" % self.n_shots

    @property
    def mouse(self):
        outer = self

        class _M:
            def click(self, x, y):
                outer.clicks.append(("click", x, y))

            def move(self, x, y, steps=1):
                outer.clicks.append(("move", x, y))

            def down(self):
                outer.clicks.append(("down",))

            def up(self):
                outer.clicks.append(("up",))
        return _M()

    @property
    def keyboard(self):
        outer = self

        class _K:
            def press(self, k):
                outer.keys.append(("press", k))

            def type(self, s):
                outer.keys.append(("type", s))
        return _K()

    def expect_response(self, pattern, timeout=None):
        if self.confirm_ok:
            return FakeResp({"success": True,
                             "data": {"tradeId": "t-1", "positionRecordId": "p-1"}})
        return FakeResp({"success": False, "error": "nope"})

    def close(self):
        self.closed = True


def test_driver_dry_run_never_confirms():
    page = FakePage()
    d = ChromeDriver("http://x", page_factory=lambda url: page)
    out = d.mimic_once("pf", "base-1", 25.0, live=False)
    assert out["confirmed"] is False
    assert any(k[0] == "type" and k[1] == "25.0" for k in page.keys)
    assert not any(c[0] == "down" for c in page.clicks)
    assert page.closed is True


def test_driver_live_confirms_and_audits_record():
    page = FakePage()
    d = ChromeDriver("http://x", page_factory=lambda url: page)
    out = d.mimic_once("pf", "base-1", 25.0, live=True)
    assert out["confirmed"] is True
    assert out["record"]["tradeId"] == "t-1"
    assert ("down",) in page.clicks


def test_driver_rejected_confirm_raises():
    page = FakePage(confirm_ok=False)
    d = ChromeDriver("http://x", page_factory=lambda url: page)
    with pytest.raises(RuntimeError, match="confirm rejected"):
        d.mimic_once("pf", "base-1", 25.0, live=True)


def test_driver_dead_dialog_raises():
    page = FakePage(shots_change=False)
    d = ChromeDriver("http://x", page_factory=lambda url: page)
    with pytest.raises(RuntimeError, match="did not change"):
        d.mimic_once("pf", "base-1", 25.0, live=False)


class FakeClient:
    access_token = "tok"

    def ensure_auth(self, **kwargs):
        return True


def _pos(sid="SRC-1", ticker="HYPE"):
    return {"baseId": sid, "ticker": ticker, "side": "SHORT",
            "leverage": 4, "entry": 85.269, "entrySim": 5}


def _bot(tmp_path, positions, notes, live=False, driver=None):
    mc = MirrorConfig(email="e", app_password="p", device_id="d",
                      data_dir=str(tmp_path))
    mg = _cfg(live=live)
    return AutoBot(mc, mg,
                   driver=driver or ChromeDriver(
                       "http://x", page_factory=lambda url: FakePage()),
                   fetch_positions_fn=lambda *a: positions,
                   fetch_detail_fn=lambda *a, **k: {"symbol": "HYPEUSDT"},
                   client_factory=lambda: FakeClient(),
                   notify_fn=lambda t: notes.append(t) or True)


def test_cycle_dry_run_marks_seen_no_money(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos()], notes, live=False)
    out = bot.run_once()
    assert out["skipped"] == 1 and out["errors"] == 0
    assert any("DRY-RUN" in n for n in notes)
    assert bot.committed_usd() == 0.0


def test_cycle_live_mimics_and_commits(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos()], notes, live=True)
    out = bot.run_once()
    assert out["mimicked"] == 1
    assert bot.committed_usd() == round(18.38 * 0.25, 2)
    assert bot.open_count() == 1
    assert any("MIMICKED HYPE" in n for n in notes)


def test_cycle_respects_max_concurrent(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos("A", "HYPE"), _pos("B", "LINK")], notes,
               live=True)
    bot.cfg.max_concurrent = 1
    out = bot.run_once()
    assert out["mimicked"] == 1 and out["skipped"] == 1


def test_cycle_close_alerts_once(tmp_path):
    notes = []
    bot = _bot(tmp_path, [_pos()], notes, live=False)
    bot.run_once()
    n0 = len(notes)
    bot2 = _bot(tmp_path, [], notes, live=False)
    out = bot2.run_once()
    assert out["alerts"] == 1 and len(notes) == n0 + 1
    assert "close your copy" in notes[-1]
    out2 = bot2.run_once()
    assert out2["alerts"] == 0


def test_cycle_driver_failure_alerts_not_crashes(tmp_path):
    notes = []

    class _Bad:
        def mimic_once(self, *a, **k):
            raise RuntimeError("cdp down")

    bot = _bot(tmp_path, [_pos()], notes, live=True, driver=_Bad())
    out = bot.run_once()
    assert out["errors"] >= 1
    assert any("MIMIC FAILED" in n for n in notes)
