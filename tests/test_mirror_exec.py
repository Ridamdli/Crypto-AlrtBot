"""HL fill parser + trading-session unit tests. No network."""
import pytest

from mirror.hl import HlBridge
from mirror.recorder import TradingSession, decode_exp


def _env(statuses):
    return {"response": {"data": {"statuses": statuses}}}


def test_parse_fill_ok():
    fill = HlBridge._parse_fill(_env([{"filled": {"totalSz": "1.5",
                                                  "avgPx": "85.0",
                                                  "oid": 7}}]), "HYPE")
    assert fill == {"coin": "HYPE", "size": 1.5, "avg_px": "85.0",
                    "oid": 7, "raw": fill["raw"]}


def test_parse_fill_error_raises():
    with pytest.raises(RuntimeError, match="No liquidity"):
        HlBridge._parse_fill(_env([{"error": "No liquidity"}]), "HYPE")


def test_parse_fill_garbage_raises():
    with pytest.raises(RuntimeError):
        HlBridge._parse_fill({"weird": True}, "HYPE")
    with pytest.raises(RuntimeError):
        HlBridge._parse_fill(_env([]), "HYPE")


def test_bridge_needs_creds():
    b = HlBridge()
    with pytest.raises(RuntimeError, match="misconfigured"):
        b._client_or_throw()


def test_decode_exp_roundtrip():
    import base64 as _b64
    import json as _json
    import time
    payload = _b64.urlsafe_b64encode(_json.dumps(
        {"expires": int(time.time()) + 600}).encode()).decode().rstrip("=")
    assert decode_exp(f"h.{payload}.s") is not None
    assert decode_exp("garbage") is None
    assert decode_exp("") is None


def test_session_ensure_fresh_token_no_refresh(monkeypatch, tmp_path):
    import base64 as _b64
    import json as _json
    import time
    payload = _b64.urlsafe_b64encode(_json.dumps(
        {"expires": int(time.time()) + 600}).encode()).decode().rstrip("=")
    s = TradingSession(access_token=f"h.{payload}.s")
    calls = []
    monkeypatch.setattr(s, "refresh", lambda: calls.append(1) or False)
    assert s.ensure() is True and calls == []


def test_session_ensure_expired_refreshes(monkeypatch):
    s = TradingSession(refresh_token="r")
    monkeypatch.setattr(s, "refresh", lambda: True)
    s.access_token = "garbage"
    assert s.ensure() is True


def test_session_persists_rotated_refresh(tmp_path, monkeypatch):
    import json as _json
    state = str(tmp_path / "trading_session.json")
    s = TradingSession(refresh_token="r-old", state_path=state)

    class _Resp:
        status = 200

        def read(self):
            return _json.dumps({"accessToken": "a-new",
                                "refreshToken": "r-new"}).encode()

    class _CM:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    import urllib.request as _req
    monkeypatch.setattr(_req, "urlopen", lambda *a, **k: _CM())
    assert s.refresh() is True
    assert s.access_token == "a-new" and s.refresh_token == "r-new"
    saved = _json.loads(open(state, encoding="utf-8").read())
    assert saved["refresh_token"] == "r-new"
    # reload resumes
    s2 = TradingSession(state_path=state)
    assert s2.refresh_token == "r-new" and s2.access_token == "a-new"


def test_session_post_without_session_raises():
    s = TradingSession()
    with pytest.raises(RuntimeError, match="no trading session"):
        s.post("/dex/trade", {})


def test_session_lazy_loads_dropped_in_file(tmp_path, monkeypatch):
    import json as _json
    state = str(tmp_path / "trading_session.json")
    _json.dump({"refresh_token": "r-late",
                "access_token": ""}, open(state, "w"))
    s = TradingSession(state_path=state)
    monkeypatch.setattr(s, "refresh", lambda: True)
    s.access_token = ""
    s.refresh_token = ""
    assert s.ensure() is True
    assert s.refresh_token == "r-late"


class _FakeTriggerEx:
    def __init__(self):
        self.orders = []
        self.cancels = []

    def order(self, coin, is_buy, sz, limit_px, order_type, reduce_only,
              cloid=None, builder=None):
        assert reduce_only is True
        assert order_type["trigger"]["isMarket"] is True
        self.orders.append((coin, is_buy, sz, order_type["trigger"]["tpsl"],
                            cloid))
        n = len(self.orders)
        return {"response": {"data": {"statuses": [
            {"resting": {"oid": 5000 + n}}]}}}

    def cancel_by_cloid(self, coin, cloid):
        self.cancels.append((coin, cloid))
        return {"status": "ok"}


def _tpsl_bridge():
    ex = _FakeTriggerEx()
    b = HlBridge(agent_key="k", wallet_address="w",
                 client_factory=lambda: ex)
    return b, ex


def test_sane_tpsl_accepts_good_geometry():
    assert HlBridge.sane_tpsl("SHORT", 85.0, 79.8, 87.8) == (79.8, 87.8)
    assert HlBridge.sane_tpsl("LONG", 100.0, 103.0, 97.0) == (103.0, 97.0)


def test_sane_tpsl_rejects_inverted_and_garbage():
    with pytest.raises(RuntimeError):
        HlBridge.sane_tpsl("SHORT", 85.0, 87.8, 79.8)  # flipped
    with pytest.raises(RuntimeError):
        HlBridge.sane_tpsl("LONG", 100.0, 97.0, 103.0)  # flipped
    with pytest.raises(RuntimeError):
        HlBridge.sane_tpsl("SHORT", 85.0, None, 87.8)


def test_place_tpsl_rests_both_triggers_short():
    b, ex = _tpsl_bridge()
    out = b.place_tpsl("HYPE", "SHORT", 1.5, 79.8, 87.8)
    assert [o[3] for o in ex.orders] == ["tp", "sl"]
    assert all(o[1] is True for o in ex.orders)  # SHORT closes via buys
    assert all(o[2] == 1.5 for o in ex.orders)
    assert out["tp_oid"] == 5001 and out["sl_oid"] == 5002
    assert out["tp_cloid"].startswith("0x")


def test_place_tpsl_long_sells():
    b, ex = _tpsl_bridge()
    b.place_tpsl("BTC", "LONG", 0.01, 90000.0, 80000.0)
    assert all(o[1] is False for o in ex.orders)


def test_cancel_tpsl_best_effort():
    b, ex = _tpsl_bridge()
    assert b.cancel_tpsl("HYPE", ["0xaaa", "0xbbb"]) == 2
    assert b.cancel_tpsl("HYPE", []) == 0
