"""Pariston-watch helper tests: fixture-shaped payloads, no network."""
from mirror.feed import normalize_position, to_share_setup


def test_normalize_position():
    inv = {"baseId": "b1", "ticker": "zec", "directionLong": False,
           "leverage": 10, "entryPrice": 1293.8, "entrySim": 5}
    assert normalize_position(inv) == {
        "baseId": "b1", "ticker": "ZEC", "side": "SHORT", "leverage": 10,
        "entry": 1293.8, "entrySim": 5}


def test_to_share_setup_maps_tp_sl():
    detail = {"ticker": "ZEC", "directionLong": False, "leverage": 10,
              "entryPrice": 1293.8, "stopLoss": 1306.01,
              "priceTarget": 1267.50}
    assert to_share_setup(detail) == {
        "symbol": "ZECUSDT", "side": "SHORT", "leverage": 10,
        "entry": 1293.8, "stop_loss": 1306.01, "tp": 1267.50}


def test_to_share_setup_long_keeps_usdt_suffix():
    detail = {"ticker": "BTCUSDT", "directionLong": True, "leverage": 9,
              "entryPrice": 67000.0, "stopLoss": None, "priceTarget": 70000.0}
    setup = to_share_setup(detail)
    assert setup["symbol"] == "BTCUSDT" and setup["side"] == "LONG"
