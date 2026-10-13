"""Dashboard tests: health verdicts, version reporter, auth, formula pins.

Read-only w.r.t. production: the HTTP tests spin a local in-process
server against a tmp DATA_DIR. Dummy tokens only, never real secrets.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

import src.dashboard as dash
from src.dashboard import (
    Handler,
    ThreadingHTTPServer,
    compute_analytics,
    compute_outcome_stats,
    compute_scan_health,
)

NOW = 1_700_000_000.0
CAD = 900


def _ts(age_s):
    return datetime.fromtimestamp(NOW - age_s, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _scan(age_s, status="ok"):
    return {"timestamp": _ts(age_s), "scan_type": "15M REFRESH",
            "status": status, "candidates": 0, "published": 0}


# ── Stage 1: health-state matrix (deterministic fixtures) ──


def test_health_fresh_ok():
    r = compute_scan_health([_scan(60)], CAD, NOW)
    assert r["state"] == "HEALTHY"


def test_health_zero_candidate_ok_stays_healthy():
    s = _scan(60)
    s["candidates"] = 0
    s["published"] = 0
    assert compute_scan_health([s], CAD, NOW)["state"] == "HEALTHY"


def test_health_fresh_failed_is_error_not_outage():
    r = compute_scan_health([_scan(60, "failed")], CAD, NOW)
    assert r["state"] == "ERROR"
    assert "alive" in r["detail"]


def test_health_aged_ok_is_stale():
    r = compute_scan_health([_scan(4000)], CAD, NOW)
    assert r["state"] == "STALE"


def test_health_aged_failed_is_stale_mentioning_failure():
    r = compute_scan_health([_scan(4000, "failed")], CAD, NOW)
    assert r["state"] == "STALE"
    assert "failed" in r["detail"]


def test_health_empty_history_is_unknown():
    assert compute_scan_health([], CAD, NOW)["state"] == "UNKNOWN"


def test_health_malformed_timestamp_is_unknown():
    assert compute_scan_health(
        [{"timestamp": "not-a-time", "status": "ok"}], CAD, NOW)["state"] == "UNKNOWN"
    assert compute_scan_health(
        [{"status": "ok"}], CAD, NOW)["state"] == "UNKNOWN"


def test_health_future_skew_is_unknown():
    s = {"timestamp": _ts(-400), "status": "ok", "scan_type": "t",
         "candidates": 0, "published": 0}
    r = compute_scan_health([s], CAD, NOW)
    assert r["state"] == "UNKNOWN"
    assert "skew" in r["detail"]


def test_health_unrecognized_status_is_attention():
    r = compute_scan_health([_scan(60, "weird")], CAD, NOW)
    assert r["state"] == "ATTENTION"
    assert "weird" in r["detail"]


def test_health_bad_cadence_falls_back():
    for bad in (0, -5, None, "junk"):
        r = compute_scan_health([_scan(60)], bad, NOW)
        assert r["state"] == "HEALTHY"
    r = compute_scan_health([_scan(4000)], 0, NOW)
    assert r["state"] == "STALE"


def test_health_never_raises_on_garbage():
    assert compute_scan_health("garbage", CAD, NOW)["state"] in (
        "UNKNOWN", "ATTENTION")
    assert compute_scan_health([None], CAD, NOW)["state"] == "UNKNOWN"


# ── Stage 1: version reporter + auth regression ──


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dash, "ADMIN_TOKEN", "test-token")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, path, token=None):
    url = base + path
    if token:
        url += ("&" if "?" in path else "?") + "token=" + token
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_healthz_open_no_auth(server):
    code, body = _get(server, "/healthz")
    assert code == 200 and json.loads(body) == {"ok": True}


def test_root_requires_token(server):
    code, body = _get(server, "/")
    assert code == 401
    code, _ = _get(server, "/", token="test-token")
    assert code == 200


def test_apis_require_token(server):
    for p in ("/api/status", "/api/signals", "/api/analytics",
              "/api/outcomes", "/api/paper", "/api/invo", "/api/version"):
        code, _ = _get(server, p)
        assert code == 401, p


def test_version_reports_env_or_unknown(server, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    code, body = _get(server, "/api/version", token="test-token")
    assert code == 200
    assert json.loads(body) == {"commit_sha": "abc123",
                                "source": "railway-env"}
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    code, body = _get(server, "/api/version", token="test-token")
    assert json.loads(body) == {"commit_sha": "", "source": "unknown"}


def test_status_carries_cadence_and_health(server, monkeypatch):
    monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "900")
    code, body = _get(server, "/api/status", token="test-token")
    assert code == 200
    st = json.loads(body)
    assert st["expected_scan_cadence_s"] == 900
    assert st["scan_health"]["state"] == "UNKNOWN"  # empty history
    assert "now_utc" in st


def test_status_survives_garbage_cadence(server, monkeypatch):
    monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "junk")
    code, body = _get(server, "/api/status", token="test-token")
    assert code == 200
    assert json.loads(body)["expected_scan_cadence_s"] == 900


# ── Stage 2: formula pins (existing semantics, unchanged) ──


def _sig(symbol="BTCUSDT", side="LONG", conf=70, day="2026-10-01",
         strategy="momentum"):
    return {"signal_id": f"SIG-{symbol}-{side}", "symbol": symbol,
            "side": side, "confidence": conf, "timestamp": f"{day}T00:00:00Z",
            "strategy_id": strategy}


def test_analytics_denominator_is_distinct_days():
    sigs = [_sig(day="2026-10-01"), _sig(day="2026-10-01"),
            _sig(day="2026-10-03")]
    a = compute_analytics(sigs)
    assert a["total_signals"] == 3
    assert a["signals_per_day"] == round(3 / 2, 2)
    assert a["avg_confidence"] == 70.0
    assert a["by_side"] == {"LONG": 3}


def test_outcome_win_rate_entered_only():
    ledger = {
        "a": {"final": True, "entry_triggered": True, "net_realized_r": 2.0,
              "signal_timestamp": "2026-10-01T00:00:00Z", "signal_id": "a"},
        "b": {"final": True, "entry_triggered": True, "net_realized_r": -1.0,
              "signal_timestamp": "2026-10-02T00:00:00Z", "signal_id": "b"},
        "c": {"final": True, "entry_triggered": False, "net_realized_r": 0,
              "signal_timestamp": "2026-10-03T00:00:00Z", "signal_id": "c"},
        "d": {"final": False, "entry_triggered": True, "net_realized_r": 5.0,
              "signal_timestamp": "2026-10-04T00:00:00Z", "signal_id": "d"},
    }
    s = compute_outcome_stats(ledger)
    assert s["entered"] == 2 and s["wins"] == 1
    assert s["win_rate"] == 0.5
    assert s["total_net_r"] == 1.0
    assert s["entered_total"] == 3  # open entered counted separately


def test_outcome_profit_factor_branches():
    assert compute_outcome_stats({})["profit_factor"] is None
    wins_only = {
        "a": {"final": True, "entry_triggered": True, "net_realized_r": 1.5,
              "signal_timestamp": "t", "signal_id": "a"}}
    assert compute_outcome_stats(wins_only)["profit_factor"] == float("inf")


# ── Stage 3: section independence at the HTTP layer ──


def test_paper_failure_does_not_affect_sibling_apis(server, monkeypatch):
    from src.papertrade import portfolio as _pf
    def _boom(self):
        raise RuntimeError("boom")
    monkeypatch.setattr(_pf.PaperPortfolio, "summary", _boom)
    code, body = _get(server, "/api/paper", token="test-token")
    assert code == 500
    assert json.loads(body) == {"error": "boom"}
    for p in ("/api/status", "/api/signals", "/api/analytics",
              "/api/outcomes", "/api/invo", "/api/version"):
        code, _ = _get(server, p, token="test-token")
        assert code == 200, p


def test_api_shapes_match_client_validators(server):
    t = "test-token"
    _, body = _get(server, "/api/signals", token=t)
    assert isinstance(json.loads(body), list)
    _, body = _get(server, "/api/analytics", token=t)
    assert "by_symbol" in json.loads(body)
    _, body = _get(server, "/api/outcomes", token=t)
    assert "stats" in json.loads(body)
    _, body = _get(server, "/api/invo", token=t)
    assert "activity" in json.loads(body)
    _, body = _get(server, "/api/status", token=t)
    assert "scan_health" in json.loads(body)


def test_page_wiring_ids_present():
    for needle in ('id="health"', 'id="asof-overview"', 'id="asof-signals"',
                   'id="asof-results"', 'id="asof-paper"', 'id="asof-invo"',
                   'id="asof-workers"', 'fetchOne', 'LASTE', 'staleNote',
                   'skeletonize', '.skel', ':focus-visible'):
        assert needle in dash.PAGE, needle


# ── Regression: /api/outcomes must be strict JSON (no Infinity) ──


def _strict_loads(body):
    def _reject(value):
        raise ValueError(f"non-finite token: {value}")
    return json.loads(body, parse_constant=_reject)


def test_outcomes_wins_only_serializes_strict_json(server, tmp_path=None):
    import src.dashboard as _d
    ledger = {
        "a": {"final": True, "entry_triggered": True, "net_realized_r": 2.0,
              "signal_timestamp": "2026-10-13T00:00:00Z", "signal_id": "a"},
    }
    # Python-level semantics preserved: inf signals "wins, zero losses".
    assert compute_outcome_stats(ledger)["profit_factor"] == float("inf")
    orig = _d.load_outcomes
    _d.load_outcomes = lambda: ledger
    try:
        code, body = _get(server, "/api/outcomes", token="test-token")
    finally:
        _d.load_outcomes = orig
    assert code == 200
    assert "Infinity" not in body and "NaN" not in body
    payload = _strict_loads(body)
    assert payload["stats"]["profit_factor"] is None


def test_outcomes_finite_profit_factor_unchanged(server):
    import src.dashboard as _d
    ledger = {
        "a": {"final": True, "entry_triggered": True, "net_realized_r": 2.0,
              "signal_timestamp": "2026-10-13T00:00:00Z", "signal_id": "a"},
        "b": {"final": True, "entry_triggered": True, "net_realized_r": -1.0,
              "signal_timestamp": "2026-10-14T00:00:00Z", "signal_id": "b"},
    }
    assert compute_outcome_stats(ledger)["profit_factor"] == 2.0
    orig = _d.load_outcomes
    _d.load_outcomes = lambda: ledger
    try:
        code, body = _get(server, "/api/outcomes", token="test-token")
    finally:
        _d.load_outcomes = orig
    assert code == 200
    assert _strict_loads(body)["stats"]["profit_factor"] == 2.0


def test_page_script_braces_balanced():
    js = dash.PAGE.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
