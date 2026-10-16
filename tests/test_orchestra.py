"""Regression tests for the 14-coin orchestra wiring.

Every enabled coin must carry exactly 3 picks from registered families,
BTC must trade with its doc-aligned picks, and research must stay gated
to the validated-history subset.
"""
from src.config import CONFIG
from src.data.symbol_registry import get_symbol_registry
from src.strategies import STRATEGY_REGISTRY
from src.pipeline import SignalPipeline


def test_tradable_universe_is_14_coins():
    tradable = CONFIG.get_tradable_symbols()
    assert len(tradable) == 14
    for sym in ["BTCUSDT", "ZECUSDT", "HYPEUSDT", "UNIUSDT", "NEARUSDT",
                "LINKUSDT", "DOTUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
                "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]:
        assert sym in tradable


def test_research_universe_stays_gated_to_validated_history():
    available = CONFIG.get_available_symbols()
    assert len(available) == 8
    for sym in ["ZECUSDT", "HYPEUSDT", "UNIUSDT", "NEARUSDT", "LINKUSDT", "DOTUSDT"]:
        assert sym not in available


def test_every_tradable_coin_has_3_registered_picks():
    for sym in CONFIG.get_tradable_symbols():
        picks = CONFIG.get_symbol_config(sym).priority_strategies
        assert len(picks) == 3, f"{sym} picks: {picks}"
        for fam in picks:
            assert fam in STRATEGY_REGISTRY, f"{sym} picks unknown family {fam}"


def test_btc_picks_match_doc_matrix():
    picks = CONFIG.get_symbol_config("BTCUSDT").priority_strategies
    assert picks == ["momentum", "ema_trend", "rsi_mean_reversion"]


def test_pipeline_covers_btc_with_its_picks():
    pipe = SignalPipeline(config=CONFIG)
    assert pipe._get_strategies_for_symbol("BTCUSDT") == ["momentum", "ema_trend", "rsi_mean_reversion"]
    assert pipe.symbol_registry.is_tradable("BTCUSDT")


def test_live_gate_excludes_unvalidated_by_default(monkeypatch):
    monkeypatch.delenv("LIVE_REQUIRE_VALIDATED", raising=False)
    pipe = SignalPipeline(config=CONFIG)
    universe = CONFIG.get_tradable_symbols()  # all 14 pass the volume filter here
    live = pipe._live_scan_symbols(universe)
    assert sorted(live) == sorted(CONFIG.get_available_symbols())
    assert len(live) == 8
    for sym in ["ZECUSDT", "HYPEUSDT", "UNIUSDT", "NEARUSDT", "LINKUSDT", "DOTUSDT"]:
        assert sym not in live


def test_live_gate_opt_out_restores_full_universe(monkeypatch):
    monkeypatch.setenv("LIVE_REQUIRE_VALIDATED", "false")
    pipe = SignalPipeline(config=CONFIG)
    live = pipe._live_scan_symbols(CONFIG.get_tradable_symbols())
    assert len(live) == 14


def _falling_klines(n=40, start=100.0, end=95.0, volume=1000.0):
    """Flat then steadily declining closes; last bar carries a volume spike."""
    klines = []
    flat = 30
    for i in range(n):
        if i < flat:
            p = start
        else:
            frac = (i - flat + 1) / (n - flat)
            p = start + (end - start) * frac
        klines.append({"timestamp": i * 900000, "open": p, "high": p + 0.5,
                       "low": p - 0.5, "close": p,
                       "volume": 3000.0 if i == n - 1 else volume})
    return klines


class _FakeFetcher:
    def __init__(self, klines_15m):
        self._k = klines_15m

    def fetch_standardized_klines(self, symbol, timeframe, limit=100,
                                  end_time=None):
        return [{"timestamp": i * 900000, "open": 30000.0, "high": 30050.0,
                 "low": 29950.0, "close": 30000.0, "volume": 1000.0}
                for i in range(60)]


class _FakeTFManager:
    def __init__(self, klines_15m):
        self._k = klines_15m

    def fetch_multi_timeframe(self, symbol, limit=100, end_time=None):
        return {"15m": self._k, "1h": [], "4h": []}


class _FakeClient:
    def get_open_interest_hist(self, symbol, period="15m", limit=10,
                               end_time=None):
        return [{"sumOpenInterestValue": str(1000 + i * 10)}
                for i in range(10)]  # steadily rising OI

    def get_funding_rate(self, symbol, limit=1, end_time=None):
        return [{"fundingRate": 0.0001}]

    def get_mark_price(self, symbol):
        return {"lastFundingRate": 0.0001}


def _falling_pipeline():
    from src.pipeline import SignalPipeline
    klines = _falling_klines()
    return SignalPipeline(config=CONFIG, client=_FakeClient(),
                          fetcher=_FakeFetcher(klines),
                          tf_manager=_FakeTFManager(klines))


def test_pipeline_short_carries_down_trend_and_bearish_oi():
    """P0.1 wiring: falling prices + rising OI must yield SHORT signals
    with price_trend 'down' and bearish OI (fails on the 'up' placeholder)."""
    pipe = _falling_pipeline()
    signals = pipe.run_pipeline(symbols=["DOGEUSDT"])
    shorts = [s for s in signals
              if s["side"] == "SHORT" and s["strategy_id"].startswith("momentum")]
    assert shorts, "fixture must produce a momentum SHORT"
    for s in shorts:
        assert s["price_trend"] == "down"
        assert s["oi_data"]["signal"] == "bearish"
        assert s["oi_data"]["status"] == "CONFIRMED"


def test_pipeline_trend_adds_full_momentum_credit():
    """The emitted trend must move the score by the full momentum credit
    (8 pts) versus the trend-less default; the OI signal itself is pinned
    bearish by the companion test above (old 'up' hardcode scored it
    bullish for half OI credit)."""
    from src.config import CONFIG as _CONFIG
    pipe = _falling_pipeline()
    signals = pipe.run_pipeline(symbols=["DOGEUSDT"])
    shorts = [s for s in signals
              if s["side"] == "SHORT" and s["strategy_id"].startswith("momentum")]
    assert shorts
    for s in shorts:
        stripped = {k: v for k, v in s.items() if k != "price_trend"}
        assert (pipe.scoring_engine.score_candidate(s, _CONFIG)
                - pipe.scoring_engine.score_candidate(stripped, _CONFIG)) == 8
