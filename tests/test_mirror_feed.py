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


def test_to_share_setup_unwraps_history_post_shape():
    """History endpoint returns a post object (fields under `update`); the
    flat-shape guard used to drop it, blinding the edit sync (UNI TP miss)."""
    from mirror.feed import fetch_source_levels
    post = {"id": "h1", "update": {
        "ticker": "UNI", "directionLong": True, "leverage": 3,
        "entryPrice": 7.5576, "priceTarget": 8.114131,
        "stopLoss": 7.21278, "isOpen": True, "baseId": "B-UNI"}}
    assert to_share_setup(post) == {
        "symbol": "UNIUSDT", "side": "LONG", "leverage": 3,
        "entry": 7.5576, "stop_loss": 7.21278, "tp": 8.114131}

    class _Client:
        def fetch_position_history(self, base_id, token):
            assert base_id == "B-UNI"
            return post

    assert fetch_source_levels(_Client(), "tok", "B-UNI") == {
        "symbol": "UNIUSDT", "side": "LONG", "leverage": 3,
        "entry": 7.5576, "stop_loss": 7.21278, "tp": 8.114131,
        "ticker": "UNI", "is_open": True}
