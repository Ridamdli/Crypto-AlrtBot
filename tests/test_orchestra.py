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
