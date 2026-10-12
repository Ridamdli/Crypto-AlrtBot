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


def test_session_post_without_session_raises():
    s = TradingSession()
    with pytest.raises(RuntimeError, match="no trading session"):
        s.post("/dex/trade", {})
