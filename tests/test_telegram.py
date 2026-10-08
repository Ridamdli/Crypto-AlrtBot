"""Regression tests for Telegram delivery formatting.

Guards against the production incident where strategy ids containing
underscores (e.g. ema_trend_20_50) broke Telegram legacy-Markdown parsing
and the API rejected messages with 400 Bad Request.
"""
import re

from src.models.signal import SignalModel
from src.utils.telegram_formatter import escape_markdown, TelegramFormatter


def _make_signal(**overrides):
    base = {
        "signal_id": "SIG-20261008-ADAUSDT-C696C6",
        "timestamp": "2026-10-08T00:09:02Z",
        "symbol": "ADAUSDT",
        "side": "SHORT",
        "strategy_id": "ema_trend_20_50",
        "strategy_version": "1.0.0",
        "entry": 0.256,
        "tp1": 0.2518844,
        "tp2": 0.2498266,
        "tp3": 0.2477688,
        "stop_loss": 0.2580578,
        "leverage": 10,
        "margin": 124.4,
        "position_notional": 1244.05,
        "risk_amount": 10.0,
        "risk_reward_tp1": 2.0,
        "risk_reward_tp2": 3.0,
        "risk_reward_tp3": 4.0,
        "confidence": 67,
        "conditions": ["EMA bearish: fast(0.25) < slow(0.26)", "R:R TP1/TP2/TP3: 2.0R / 3.0R / 4.0R"],
        "invalidation_conditions": ["15m candle closes above 0.258058 (Stop Loss)"],
        "management_rules": ["TP1 -> close 50% of position"],
        "strategy_metadata": {"fast_ema": 0.2547, "pullback_pct": 0.005},
    }
    base.update(overrides)
    return SignalModel(**base)


def test_escape_markdown_covers_legacy_specials():
    out = escape_markdown("ema_trend_20_50 [x] *y* `z`")
    assert "ema_trend_20_50" not in out
    assert out == "ema\\_trend\\_20\\_50 \\[x\\] \\*y\\* \\`z\\`"


def test_format_signal_has_no_unescaped_specials_in_dynamic_fields():
    msg = TelegramFormatter.format_signal(_make_signal(), rank=1)
    # strategy id must appear escaped
    assert "ema\\_trend\\_20\\_50" in msg
    assert "ema_trend_20_50" not in msg
    # every underscore / asterisk / bracket in the payload must be escaped
    for ch in ("_", "*", "[", "]"):
        for m in re.finditer(re.escape(ch), msg):
            assert msg[m.start() - 1] == "\\", f"unescaped {ch!r} in formatted message"
    # code-span backticks around the signal id stay balanced
    assert msg.count("`") % 2 == 0


def test_format_signal_shows_size_pct_and_liq_not_margin():
    sig = _make_signal()
    sig.leverage = 10
    sig.margin = 124.4
    sig.position_notional = 1244.05
    sig.entry = 0.256
    msg = TelegramFormatter.format_signal(sig, rank=1)
    assert "MARGIN" not in msg
    assert "$1244.05 (124.4% of bank)" in msg
    assert "LIQ (est)" in msg
    # liq SHORT 10x @0.256 ≈ 0.256*(1+0.1-0.004) = 0.280576
    assert "0.280576" in msg


def test_format_signal_without_sizing_falls_back_safely():
    sig = _make_signal()
    object.__setattr__(sig, "position_notional", None)
    object.__setattr__(sig, "leverage", None)
    object.__setattr__(sig, "margin", None)
    msg = TelegramFormatter.format_signal(sig, rank=1)
    assert "LIQ (est)" not in msg


def test_format_conflict_signal_escapes_dynamic_fields():
    msg = TelegramFormatter.format_conflict_signal(
        [{"symbol": "BTCUSDT", "strategy_id": "ema_trend_20_50", "side": "LONG", "confidence": 72,
          "signal_id": "SIG-X"}]
    )
    assert "ema_trend_20_50" not in msg
    assert "ema\\_trend\\_20\\_50" in msg
