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
        "conditions": ["EMA bearish: fast(0.25) < slow(0.26)", "R:R TP: 2.0R"],
        "invalidation_conditions": ["15m candle closes above 0.258058 (Stop Loss)"],
        "management_rules": ["TP -> close 100% of position"],
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


# ── sender hardening: kill switch, retry, dead letter ─────────────
import json as _json
import os as _os

import requests as _requests

from src.utils import telegram_bot as _botmod
from src.utils.telegram_bot import TelegramBot


def _creds(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.setattr(_botmod.time, "sleep", lambda s: None)
    return TelegramBot(storage_dir=str(tmp_path))


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _requests.exceptions.HTTPError(f"{self.status_code}", response=self)


def _dead_letters(tmp_path):
    p = _os.path.join(str(tmp_path), "telegram_dead_letter.json")
    if not _os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return _json.load(f)


def test_kill_switch_skips_without_post_or_dead_letter(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    calls = []
    monkeypatch.setattr(_botmod.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(200))
    assert bot.send_message("hello") is False
    assert calls == []
    assert _dead_letters(tmp_path) == []


def test_send_succeeds_first_try(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(_botmod.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(200))
    assert bot.send_message("hello") is True
    assert len(calls) == 1
    assert _dead_letters(tmp_path) == []


def test_transient_failure_retries_then_succeeds(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise _requests.exceptions.ConnectionError("down")
        return _Resp(200)

    monkeypatch.setattr(_botmod.requests, "post", flaky)
    assert bot.send_message("hello") is True
    assert len(attempts) == 2
    assert _dead_letters(tmp_path) == []


def test_400_fails_fast_and_dead_letters(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(_botmod.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(400, "bad entities"))
    assert bot.send_message("hello") is False
    assert len(calls) == 1  # no pointless retries on malformed payloads
    recs = _dead_letters(tmp_path)
    assert len(recs) == 1
    assert "400" in recs[0]["error"]
    assert recs[0]["message"] == "hello"
    blob = _json.dumps(recs)
    assert "tok" not in blob and "chat" not in blob  # no credentials stored


def test_persistent_500_exhausts_retries_then_dead_letters(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(_botmod.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(503, "unavailable"))
    assert bot.send_message("hello") is False
    assert len(calls) == 1 + len(_botmod.RETRY_DELAYS)
    assert len(_dead_letters(tmp_path)) == 1


def test_missing_credentials_skip_without_dead_letter(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.setattr(_botmod.time, "sleep", lambda s: None)
    bot = TelegramBot(storage_dir=str(tmp_path))
    calls = []
    monkeypatch.setattr(_botmod.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(200))
    assert bot.send_message("hello") is False
    assert calls == []
    assert _dead_letters(tmp_path) == []


def test_dead_letter_cap_respected(monkeypatch, tmp_path):
    bot = _creds(monkeypatch, tmp_path)
    bot.dead_letter_cap = 3
    monkeypatch.setattr(_botmod.requests, "post", lambda *a, **k: _Resp(500, "x"))
    for _ in range(5):
        assert bot.send_message("hello") is False
    assert len(_dead_letters(tmp_path)) == 3
    assert bot.dead_letter_count() == 3


def test_format_signal_shows_single_tp():
    """Single-TP contract: exactly one TP line, one R:R value, full close."""
    sig = _make_signal()
    assert sig.tp == 0.2518844  # legacy tp1 mapped
    assert sig.risk_reward == 2.0
    msg = TelegramFormatter.format_signal(sig, rank=8)
    assert "TP1" not in msg and "TP2" not in msg and "TP3" not in msg
    assert "R:R\n2.0R\n" in msg
    assert "0.251884" in msg  # :.6g formatting
