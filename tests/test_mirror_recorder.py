"""Recorder queue fallback: a missing trading session must delay a record,
not lose it. No network — the HTTP layer is replaced with a lambda.

W1 finding: 78/78 opens were real HL fills that never got recorded because
Railway never had a trading JWT. The raise still happens (so the operator
still sees record_failed), but the payload now survives on disk.
"""

import base64
import json

import pytest

from mirror.recorder import TradingSession


def _jwt(exp: int) -> str:
    seg = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{seg}.s"


def _session(tmp_path) -> TradingSession:
    return TradingSession(state_path=str(tmp_path / "trading_session.json"))


def _queued(tmp_path) -> list[dict]:
    path = tmp_path / "pending_records.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_no_session_queues_and_still_raises(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(RuntimeError, match="no trading session"):
        s.record_open({"clientTxId": "open:1"})
    recs = _queued(tmp_path)
    assert len(recs) == 1
    assert recs[0]["path"] == "/dex/position/create"
    assert recs[0]["body"] == {"clientTxId": "open:1"}


def test_flush_sends_queued_in_order_then_clears(tmp_path):
    s = _session(tmp_path)
    for i in range(2):
        with pytest.raises(RuntimeError):
            s.record_close({"clientTxId": f"close:{i}"})
    sent = []
    s._post_now = lambda path, body: sent.append((path, body["clientTxId"]))
    s.access_token = _jwt(9_999_999_999)
    assert s.flush() == 2
    assert sent == [("/dex/position/close", "close:0"),
                    ("/dex/position/close", "close:1")]
    assert not (tmp_path / "pending_records.jsonl").exists()


def test_flush_stops_on_transient_failure_and_keeps_remainder(tmp_path):
    s = _session(tmp_path)
    for i in range(2):
        with pytest.raises(RuntimeError):
            s.record_open({"clientTxId": f"open:{i}"})

    def flaky(path, body):
        if body["clientTxId"] == "open:1":
            raise RuntimeError("HTTP 503: upstream")

    s._post_now = flaky
    s.access_token = _jwt(9_999_999_999)
    assert s.flush() == 1
    rest = _queued(tmp_path)
    assert len(rest) == 1
    assert rest[0]["body"]["clientTxId"] == "open:1"


def test_flush_drops_permanent_rejection_instead_of_blocking(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(RuntimeError):
        s.record_open({"clientTxId": "open:bad"})
    s._post_now = lambda path, body: (_ for _ in ()).throw(
        RuntimeError("/dex/position/create HTTP 400: bad payload"))
    s.access_token = _jwt(9_999_999_999)
    assert s.flush() == 1
    assert not (tmp_path / "pending_records.jsonl").exists()


def test_post_flushes_backlog_before_sending_new(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(RuntimeError):
        s.record_open({"clientTxId": "open:old"})
    order = []
    s._post_now = lambda path, body: order.append(body["clientTxId"]) or {"success": True}
    s.access_token = _jwt(9_999_999_999)
    s.record_open({"clientTxId": "open:new"})
    assert order == ["open:old", "open:new"]
