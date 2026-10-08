"""Tests for the Invo auto-share client (all network mocked — no creds needed)."""
import pytest

from src.invo.client import InvoClient, InvoAuthError


def _sig(**kw):
    base = {"symbol": "BTCUSDT", "side": "LONG", "leverage": 10,
            "tp1": 95000.0, "stop_loss": 89000.0}
    base.update(kw)
    return base


def test_build_position_payload_matches_app_schema():
    c = InvoClient()
    p = c.build_position_payload("BTCUSDT", "LONG", 10, tp_px=95000.0, sl_px=89000.0)
    assert p["coin"] == "BTC"
    assert p["assetIndex"] == 0
    assert p["entry"] == {"side": "long", "marginMode": "isolated",
                          "leverage": 10, "tpPx": 95000.0, "slPx": 89000.0}
    assert "clientTxId" in p


def test_unknown_coin_refuses_to_guess_index():
    c = InvoClient()
    with pytest.raises(ValueError, match="No verified Hyperliquid assetIndex"):
        c.build_position_payload("ZECUSDT", "LONG", 10)


def test_bad_side_rejected():
    c = InvoClient()
    with pytest.raises(ValueError):
        c.build_position_payload("BTCUSDT", "SIDEWAYS", 10)


def test_dry_run_posts_nothing():
    c = InvoClient()
    calls = []
    c._post = lambda path, payload: calls.append((path, payload)) or {}
    out = c.share_position(_sig(), dry_run=True)
    assert out["dry_run"] is True
    assert calls == []


def test_live_share_requires_login_first(monkeypatch):
    c = InvoClient()
    assert c.access_token is None
    monkeypatch.setattr(c, "ensure_auth",
                        lambda: (_ for _ in ()).throw(InvoAuthError("no creds")))
    with pytest.raises(InvoAuthError):
        c.share_position(_sig(), dry_run=False)


def test_paper_share_payload_matches_captured_shape():
    c = InvoClient()
    p = c.build_paper_share_payload(
        symbol="BTCUSDT", long=False, leverage=7,
        entry_sim=1.1585205451150278, price_target=29,
        stop_loss=None, liquidation_price=93233.43915343916,
        portfolio_id="c43ec3e9-db34-4935-8219-4c81d7bcfb9e",
    )
    assert p == {
        "ticker": "BTC",
        "portfolioId": "c43ec3e9-db34-4935-8219-4c81d7bcfb9e",
        "directionLong": False,
        "entrySim": 1.1585205451150278,
        "priceTarget": 29,
        "stopLoss": None,
        "leverage": 7,
        "liquidationPrice": 93233.43915343916,
    }


def test_paper_headers_carry_required_app_fields():
    h = InvoClient._paper_headers("tok123")
    assert h["authorization"] == "Bearer tok123"
    for k in ("nonce", "timestamp", "x-app-build-number", "x-app-release",
              "x-app-version", "x-platform"):
        assert h[k]


def test_paper_share_dry_run_posts_nothing(monkeypatch):
    c = InvoClient()
    calls = []
    monkeypatch.setattr(c.session, "post",
                        lambda *a, **k: calls.append((a, k)) or None)
    out = c.share_paper_trade(
        {"symbol": "BTCUSDT", "side": "SHORT", "leverage": 7,
         "stop_loss": None, "entry": 81500.0},
        token="tok", portfolio_id="pid",
        entry_sim=1.15, price_target=29, dry_run=True,
    )
    assert out["dry_run"] is True
    assert calls == []
    assert out["payload"]["ticker"] == "BTC"
    assert out["payload"]["directionLong"] is False


def test_live_share_posts_exact_path(monkeypatch):
    c = InvoClient(email="u@x.com", password="pw")
    c.access_token = "tok"
    seen = {}
    def fake_post(path, payload, retry=True):
        seen["path"] = path
        seen["payload"] = payload
        return {"ok": True}
    monkeypatch.setattr(c, "_post", fake_post)
    out = c.share_position(_sig(), dry_run=False)
    assert out["dry_run"] is False
    assert seen["path"] == "/dex/position/create"
    assert seen["payload"]["coin"] == "BTC"
