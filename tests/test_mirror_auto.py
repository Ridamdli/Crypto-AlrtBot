"""Auto-mimic tests: brain + feed classification + full cycle. No network.

HL bridge and trading session are faked; the cycle runs end to end
against synthetic Pariston feed posts.
"""
import pytest

from mirror.auto import (AutoBot, MimicConfig, build_record_open,
                         decide_live_size, is_pariston)
from mirror.config import MirrorConfig
from mirror.feedmon import FeedWatcher, classify_post


def _cfg(**kw):
    base = dict(account_usd=18.38, max_usd_per_mimic=5.0,
                max_pct_per_mimic=25.0, max_concurrent=2,
                min_remaining_usd=3.0)
    base.update(kw)
    return MimicConfig(**base)


def test_brain_full_mimic():
    margin, reason = decide_live_size(18.38, 0.0, _cfg())
    assert margin == round(18.38 * 0.25, 2) and reason == "full-mimic"


def test_brain_flat_cap_binds():
    margin, _ = decide_live_size(100.0, 0.0, _cfg())
    assert margin == 5.0


def test_brain_floor_halts():
    margin, reason = decide_live_size(18.38, 16.0, _cfg())
    assert margin is None and "floor" in reason


def test_brain_blind_never():
    margin, reason = decide_live_size(0.0, 0.0, _cfg())
    assert margin is None and "bank" in reason


POST_OPEN = {
    "id": "post-1",
    "owner": {"id": "other", "username": "someone"},
    "update": {
        "ticker": "HYPE", "directionLong": False, "leverage": 4,
        "entryPrice": 85.269, "priceTarget": 79.8, "stopLoss": 87.8,
        "isOpen": True, "verifiedTrade": True,
        "baseId": "SRC-1", "baseShortId": "SH-1",
        "changes": {"isAdded": True},
        "owner": {"id": "7fd09aef-d45f-47dd-aa7f-72143e7d5a05",
                  "username": "paristoon"},
        "portfolio": {"id": "17cc19aa-d854-4857-9cca-7f05f21ec2da"},
    },
}


def test_classify_open_and_pariston_filter():
    ev = classify_post(POST_OPEN)
    assert ev["action"] == "open" and ev["ticker"] == "HYPE"
    assert ev["side"] == "SHORT" and is_pariston(ev) is True
    stranger = classify_post({"id": "x", "update": dict(
        POST_OPEN["update"], owner={"id": "zzz", "username": "who"},
        portfolio={"id": "qqq"})})
    assert is_pariston(stranger) is False


def test_classify_close_and_non_trade():
    close = {"id": "c1", "update": {"ticker": "HYPE", "isOpen": False,
                                    "closingPrice": 80.0, "verifiedTrade": True,
                                    "baseId": "SRC-1"}}
    assert classify_post(close)["action"] == "close"
    assert classify_post({"id": "n", "content": "hello"}) is None
    assert classify_post({"id": "u", "update": {"ticker": "BTC"}}) is None


def test_watcher_indexes_first_poll():
    seen = [[POST_OPEN]]
    w = FeedWatcher(fetch_fn=lambda token: seen.pop(0) if seen else [])
    news, updates = w.poll("tok")
    assert news == [] and updates == []  # first poll only indexes
    seen.append([dict(POST_OPEN, id="post-2")])
    news, _ = w.poll("tok")
    assert [e["post_id"] for e in news] == ["post-2"]


class FakeHl:
    def __init__(self, holdings=None):
        self.opens = []
        self.closes = []
        self.orders = []
        self.cancels = []
        self.holdings = set(holdings or [])

    def equity(self):
        return None

    def meta(self):
        return {"HYPE": {"assetIndex": 10, "szDecimals": 2, "maxLeverage": 10}}

    def position(self, coin):
        if coin in self.holdings or any(c == coin for c, _, _, _ in self.opens):
            return {"szi": "-1.5", "entryPx": "85.0"}
        return None

    def cancel_tpsl(self, coin, cloids):
        self.cancels.append((coin, list(cloids or [])))
        return len(cloids or [])

    def cancel_coin_triggers(self, coin):
        self.cancels.append((coin, "sweep"))
        return 0

    def place_tpsl(self, coin, side, size, tp, sl):
        self.orders.append((coin, side, size, tp, sl))
        n = len(self.orders)
        return {"tp_oid": 1000 + n, "sl_oid": 2000 + n,
                "tp_cloid": f"0xtp{n:03d}", "sl_cloid": f"0xsl{n:03d}"}

    def market_open(self, coin, side, margin_usd, leverage):
        assert margin_usd > 0 and leverage >= 1
        self.opens.append((coin, side, margin_usd, leverage))
        return {"coin": coin, "size": 1.5, "avg_px": 85.0, "oid": 1,
                "leverage": leverage, "raw": {"ok": True}}

    def close(self, coin):
        if self.position(coin) is None:
            raise RuntimeError(f"no open position for {coin}")
        self.closes.append(coin)
        return {"coin": coin, "raw": {"ok": True}}


class FakeSession:
    def __init__(self):
        self.opens = []
        self.closes = []

    def record_open(self, payload):
        assert payload["mimicMeta"]["sourcePaperTradeBaseId"] == "SRC-1"
        assert payload["mimicMeta"]["initialSourcePaperUpdateId"].startswith("post")
        self.opens.append(payload)
        return {"success": True}

    def record_close(self, payload):
        self.closes.append(payload)
        return {"success": True}


def test_account_prefers_live_equity(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))

    class _Hl(FakeHl):
        def equity(self):
            return 25.0

    bot = AutoBot(mc, _cfg(live=False, account_usd=18.38), hl=_Hl(),
                  session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    assert bot._account_usd() == 25.0


def test_account_falls_back_to_env_without_wallet(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))

    class _Hl(FakeHl):
        def equity(self):
            return None

    bot = AutoBot(mc, _cfg(live=False, account_usd=18.38), hl=_Hl(),
                  session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    assert bot._account_usd() == 18.38


def test_decrypt_layout_roundtrip(tmp_path):
    import importlib.util
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    spec = importlib.util.spec_from_file_location(
        "decrypt_refresh", "scripts/decrypt_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import os as _os
    key = AESGCM.generate_key(bit_length=128)
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, b"jwt.test.sig", None)
    import base64 as _b64
    enc = _b64.b64encode(nonce + ct).decode()
    out = mod.decrypt(enc, _b64.b64encode(key).decode())
    assert out == "jwt.test.sig"


def test_cycle_dry_run_detects_sizes_no_orders(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    feed = PostFeed([POST_OPEN])
    bot = AutoBot(mc, _cfg(live=False), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=feed), notify_fn=got.append)
    out = bot.run_once("tok")
    assert out["errors"] == 0
    assert hl.opens == []  # dry-run posts nothing (first poll: index only)
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    out = bot.run_once("tok")
    assert hl.opens == []
    assert any("DRY-RUN" in n for n in got)
    out2 = bot.run_once("tok")
    assert out2["mimicked"] == 0  # same post never re-fires


class PostFeed:
    """Mutable post list behind one watcher (keeps index state)."""

    def __init__(self, posts):
        self.posts = list(posts)

    def __call__(self, token):
        return list(self.posts)


def _live_bot(tmp_path, feed_posts, got, **kw):
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True, **kw), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=PostFeed(feed_posts)),
                  notify_fn=got.append)
    return bot, hl, sess


def test_cycle_live_mimics_records_commits(tmp_path):
    got = []
    feed = PostFeed([POST_OPEN])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")  # indexes
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    out = bot.run_once("tok")
    assert out["mimicked"] == 1
    assert hl.opens == [("HYPE", "SHORT", round(18.38 * 0.25, 2), 4)]
    assert len(sess.opens) == 1
    assert bot.committed_usd() == round(18.38 * 0.25, 2)
    assert bot.open_count() == 1
    assert any("MIMICKED HYPE" in n for n in got)


def test_cycle_close_mirrors_exit(tmp_path):
    got = []
    feed = PostFeed([POST_OPEN])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    bot.run_once("tok")
    assert bot.open_count() == 1
    close_post = {"id": "post-3", "update": {
        "ticker": "HYPE", "directionLong": False, "leverage": 4,
        "isOpen": False, "closingPrice": 80.0, "verifiedTrade": True,
        "baseId": "SRC-1", "baseShortId": "SH-1",
        "owner": {"id": "7fd09aef-d45f-47dd-aa7f-72143e7d5a05",
                  "username": "paristoon"},
        "portfolio": {"id": "17cc19aa-d854-4857-9cca-7f05f21ec2da"}}}
    feed.posts.append(close_post)
    out = bot.run_once("tok")
    assert out["closed"] == 1
    assert hl.closes == ["HYPE"] and len(sess.closes) == 1
    assert bot.open_count() == 0 and bot.committed_usd() == 0.0


def test_cycle_respects_max_concurrent(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    feed = PostFeed([POST_OPEN])
    bot = AutoBot(mc, _cfg(live=True, max_concurrent=1), hl=hl,
                  session=FakeSession(), watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")  # index
    feed.posts.append(dict(POST_OPEN, id="post-H"))
    feed.posts.append(dict(POST_OPEN, id="post-L",
                           update=dict(POST_OPEN["update"],
                                       ticker="LINK", baseId="SRC-2")))
    out = bot.run_once("tok")
    assert out["mimicked"] == 1 and out["skipped"] == 1


def test_record_builder_shape():
    ev = classify_post(POST_OPEN)
    rec = build_record_open(ev, "HYPE", 10, "SHORT", 4, "0", 1.5,
                            {"o": 1}, {"r": 1}, 123)
    assert rec["coin"] == "HYPE" and rec["assetIndex"] == 10
    assert rec["entry"] == {"side": "short", "marginMode": "isolated",
                            "leverage": 4, "tpPx": None, "slPx": None}
    assert rec["mimicMeta"]["sourcePaperTradeBaseId"] == "SRC-1"
    assert rec["summary"]["intendedLeverage"] == 4


STRANGER = {"id": "post-X", "update": {
    "ticker": "BTC", "directionLong": True, "leverage": 20,
    "entryPrice": 82000.0, "isOpen": True, "verifiedTrade": True,
    "baseId": "SRC-X", "baseShortId": "SH-X",
    "changes": {"isAdded": True},
    "owner": {"id": "b3f0f13f-d3e5-4255-bd5c-d012fa287b57",
              "username": "somebody-else"},
    "portfolio": {"id": "ac454e11-4834-4d79-943b-f070c9b45ec7"}}}

STRANGER_CLOSE = {"id": "post-XC", "update": {
    "ticker": "BTC", "directionLong": True, "leverage": 20,
    "isOpen": False, "closingPrice": 81000.0, "verifiedTrade": True,
    "baseId": "SRC-X", "baseShortId": "SH-X",
    "owner": {"id": "b3f0f13f-d3e5-4255-bd5c-d012fa287b57",
              "username": "somebody-else"},
    "portfolio": {"id": "ac454e11-4834-4d79-943b-f070c9b45ec7"}}}


def test_bootstrap_mimics_unmirrored_opens_once(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)

    class _Client:
        pass

    import mirror.auto as _auto
    import mirror.feed as _feed
    _feed_orig = _feed.fetch_open_positions
    _feed.fetch_open_positions = lambda *a, **k: [
        {"baseId": "B-HYPE", "ticker": "HYPE", "side": "SHORT", "leverage": 4},
        {"baseId": "B-LINK", "ticker": "LINK", "side": "SHORT", "leverage": 5}]
    try:
        out = bot.bootstrap_from_sims(_Client(), "tok")
        assert out["mimicked"] == 2
        assert [c for c, _, _, _ in hl.opens] == ["HYPE", "LINK"]
        out2 = bot.bootstrap_from_sims(_Client(), "tok")
        assert out2["mimicked"] == 0  # restart-safe
        assert bot.open_count() == 2
    finally:
        _feed.fetch_open_positions = _feed_orig


def test_bootstrap_skips_wallet_held_coin(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl(holdings={"HYPE"})
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    import mirror.auto as _auto
    import mirror.feed as _feed
    _feed_orig = _feed.fetch_open_positions
    _feed.fetch_open_positions = lambda *a, **k: [
        {"baseId": "B-HYPE", "ticker": "HYPE", "side": "SHORT", "leverage": 4}]
    try:
        out = bot.bootstrap_from_sims(object(), "tok")
        assert out["mimicked"] == 0 and out["skipped"] == 1
        assert hl.opens == []
        assert any("already holds" in n for n in got)
    finally:
        _feed.fetch_open_positions = _feed_orig


def test_reconcile_heals_manually_closed(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()  # flat everywhere
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    bot.store.audit("mimicked", {"coin": "LINK", "margin_usd": 4.61,
                                 "source": "SRC-9"})
    assert bot.open_count() == 1
    assert bot.reconcile_ledger() == 1
    assert bot.open_count() == 0 and bot.committed_usd() == 0.0
    assert bot.reconcile_ledger() == 0  # idempotent


def test_tpsl_attached_on_open_with_source_levels(tmp_path):
    got = []
    feed = PostFeed([POST_OPEN])  # carries priceTarget 79.8 / stopLoss 87.8
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    out = bot.run_once("tok")
    assert out["mimicked"] == 1
    assert hl.orders == [("HYPE", "SHORT", 1.5, 79.8, 87.8)]
    assert any("TP/SL set HYPE" in n for n in got)


def test_tpsl_update_replaces_resting_triggers(tmp_path):
    got = []
    feed = PostFeed([POST_OPEN])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    bot.run_once("tok")
    assert len(hl.orders) == 1
    upd = {"id": "post-5", "update": {
        "ticker": "HYPE", "directionLong": False, "leverage": 4,
        "priceTarget": 78.0, "stopLoss": 88.0, "verifiedTrade": True,
        "baseId": "SRC-1", "baseShortId": "SH-1",
        "changes": {"isAdded": False},
        "owner": {"id": "7fd09aef-d45f-47dd-aa7f-72143e7d5a05",
                  "username": "paristoon"},
        "portfolio": {"id": "17cc19aa-d854-4857-9cca-7f05f21ec2da"}}}
    feed.posts.append(upd)
    out = bot.run_once("tok")
    assert out["alerts"] == 1
    assert len(hl.orders) == 2  # replaced
    assert ("HYPE", ["0xtp001", "0xsl001"]) in hl.cancels  # cloid cancel
    assert ("HYPE", "sweep") in hl.cancels  # open-order sweep
    assert any("TP/SL synced HYPE" in n for n in got)


def test_hl_minimum_bump_and_skip(tmp_path):
    from mirror import auto as _auto
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    # 3x leverage: $4.59 margin -> $13.77 ok, no bump. Force small account.
    hl = FakeHl()
    bot = AutoBot(mc, _cfg(live=True, account_usd=6.0, max_usd_per_mimic=5.0,
                           max_pct_per_mimic=25.0, min_remaining_usd=3.0),
                  hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    ev = {"action": "open", "post_id": "p1", "base_id": "B1",
          "ticker": "DOGE", "side": "SHORT", "leverage": 3,
          "owner_id": _auto.PARISTON_OWNER_ID,
          "portfolio_id": _auto.PARISTON_PORTFOLIO_ID}
    summary = {"mimicked": 0, "closed": 0, "skipped": 0, "alerts": 0,
               "errors": 0}
    # $6 acct: target min(5, 1.5)=1.5, room 3.0 -> $1.5*3=$4.5 < $10.5,
    # need $3.5 > min(cap 1.5, room 3.0) -> skip
    bot._mimic_open(ev, summary)
    assert summary["skipped"] == 1 and hl.opens == []
    assert any("under-hl-minimum" in n for n in got)


def test_cap_skipped_base_retries_when_slot_frees(tmp_path):
    got = []
    feed = PostFeed([])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    bot = AutoBot(mc, _cfg(live=True, max_concurrent=1), hl=hl,
                  session=FakeSession(), watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    import mirror.auto as _auto
    import mirror.feed as _feed
    _feed_orig = _feed.fetch_open_positions
    opens = [
        {"baseId": "B-HYPE", "ticker": "HYPE", "side": "SHORT", "leverage": 4},
        {"baseId": "B-LINK", "ticker": "LINK", "side": "SHORT", "leverage": 5}]
    _feed.fetch_open_positions = lambda *a, **k: opens
    try:
        out = bot.bootstrap_from_sims(object(), "tok")
        assert out["mimicked"] == 1 and out["skipped"] == 1
        assert "sims-B-LINK" not in bot.store.seen_ids()  # retryable
        bot.store.audit("mimic_closed", {"coin": "HYPE", "margin_usd": 1})
        out2 = bot.bootstrap_from_sims(object(), "tok")
        assert out2["mimicked"] == 1  # LINK got its slot
        assert [c for c, _, _, _ in hl.opens].count("LINK") == 1
    finally:
        _feed.fetch_open_positions = _feed_orig


def test_backfill_attaches_resolved_tpsl(tmp_path, monkeypatch):
    import json as _json
    main = tmp_path / "main_data"
    main.mkdir()
    _json.dump({"SIG-1": {"symbol": "HYPEUSDT", "side": "SHORT",
                          "entry": 85.1, "tp": 79.8, "stop_loss": 87.8}},
               open(main / "outcomes.json", "w"))
    monkeypatch.setenv("MIMIC_MAIN_DATA_DIR", str(main))
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl(holdings={"HYPE"})
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    bot.store.audit("mimicked", {"coin": "HYPE", "margin_usd": 4.68,
                                 "source": "SRC-1", "side": "SHORT"})
    assert bot._backfill_tpsl() == 1
    assert hl.orders == [("HYPE", "SHORT", 1.5, 79.8, 87.8)]
    assert bot._backfill_tpsl() == 0  # idempotent


def test_resolve_rejects_loose_match(tmp_path, monkeypatch):
    import json as _json
    main = tmp_path / "main_data"
    main.mkdir()
    _json.dump({"SIG-1": {"symbol": "HYPEUSDT", "side": "SHORT",
                          "entry": 90.0, "tp": 79.8, "stop_loss": 87.8}},
               open(main / "outcomes.json", "w"))
    monkeypatch.setenv("MIMIC_MAIN_DATA_DIR", str(main))
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    bot = AutoBot(mc, _cfg(live=True), hl=FakeHl(), session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=lambda t: None)
    assert bot._resolve_local_tpsl("HYPE", "SHORT", 85.269) == (None, None)
    assert bot._resolve_local_tpsl("HYPE", "LONG", 85.269) == (None, None)


def test_resolve_prefers_dashboard_signals(tmp_path, monkeypatch):
    import json as _json
    import urllib.request as _req
    monkeypatch.setenv(
        "MIMIC_MAIN_DASHBOARD_URL", "https://dash.example")
    monkeypatch.setenv("MIMIC_MAIN_DASHBOARD_TOKEN", "tok")
    monkeypatch.setenv("MIMIC_MAIN_DATA_DIR", str(tmp_path / "empty"))

    payload = _json.dumps([{"symbol": "HYPEUSDT", "side": "SHORT",
                            "entry": 85.2, "tp": 79.8,
                            "stop_loss": 87.8}]).encode()

    class _Resp:
        status = 200

        def read(self):
            return payload

    class _CM:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _CM()

    monkeypatch.setattr(_req, "urlopen", _fake_urlopen)
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    bot = AutoBot(mc, _cfg(live=True), hl=FakeHl(), session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=lambda t: None)
    assert bot._resolve_local_tpsl("HYPE", "SHORT", 85.0) == (79.8, 87.8)
    assert "token=tok" in seen["url"] and "limit=100" in seen["url"]


def test_resolve_empty_without_dashboard(tmp_path, monkeypatch):
    monkeypatch.delenv("MIMIC_MAIN_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("MIMIC_MAIN_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("MIMIC_MAIN_DATA_DIR", str(tmp_path / "empty"))
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    bot = AutoBot(mc, _cfg(live=True), hl=FakeHl(), session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=lambda t: None)
    assert bot._resolve_local_tpsl("HYPE", "SHORT", 85.0) == (None, None)


def test_brain_splits_book_across_opens():
    margin, reason = decide_live_size(20.0, 0.0, _cfg(), n_opens=9)
    assert margin == round(20.0 * 0.9 / 9, 2) and reason == "full-mimic"
    margin2, _ = decide_live_size(20.0, 0.0, _cfg(), n_opens=1)
    assert margin2 == 5.0


def test_sims_missing_close_never_fires_on_bad_envelope(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    bot.store.audit("mimicked", {"coin": "HYPE", "margin_usd": 4.68,
                                 "source": "SRC-1", "side": "SHORT"})

    class _BadClient:
        def list_paper_investments(self, token, portfolio):
            return {"success": False}

    out = bot._sims_sync(_BadClient(), "tok")
    assert out["errors"] == 1 and bot.open_count() == 1

    class _EmptyClient:
        def list_paper_investments(self, token, portfolio):
            return "garbage"

    out = bot._sims_sync(_EmptyClient(), "tok")
    assert out["errors"] == 1 and bot.open_count() == 1


def test_sims_missing_close_fires(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl(holdings={"HYPE"})
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    bot.store.audit("mimicked", {"coin": "HYPE", "margin_usd": 4.68,
                                 "source": "SRC-1", "side": "SHORT"})

    class _Client:
        def list_paper_investments(self, token, portfolio):
            return {"success": True, "investments": [
                {"baseId": "B-X", "ticker": "LINK", "directionLong": False}]}

    out = bot._sims_sync(_Client(), "tok")
    assert out["closed"] == 1
    assert hl.closes == ["HYPE"]
    assert any("sims-missing" in n for n in got)


def test_close_classifier_needs_no_closing_price():
    from mirror.feedmon import classify_post
    ev = classify_post({"id": "c1", "update": {
        "ticker": "HYPE", "directionLong": False, "verifiedTrade": True,
        "isOpen": False, "baseId": "B1",
        "owner": {"id": "7fd09aef-d45f-47dd-aa7f-72143e7d5a05"},
        "portfolio": {"id": "17cc19aa-d854-4857-9cca-7f05f21ec2da"}}})
    assert ev["action"] == "close"


def test_adoption_brings_wallet_holding_into_lifecycle(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl(holdings={"LINK"})
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)

    class _Client:
        def list_paper_investments(self, token, portfolio):
            return {"success": True, "investments": [
                {"baseId": "B-LINK", "ticker": "LINK",
                 "directionLong": False}]}

    out = bot._sims_sync(_Client(), "tok")
    assert "LINK" in bot.mirrored_coins()
    assert any("ADOPTED LINK" in n for n in got)
    # Second sync: adopted, not duplicated, not closed.
    out2 = bot._sims_sync(_Client(), "tok")
    assert out2["mimicked"] == 0 and out2["closed"] == 0
    assert bot.open_count() == 1


def test_close_heals_already_flat_instead_of_erroring(tmp_path):
    got = []
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl = FakeHl()  # flat: close() raises below
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=FakeSession(),
                  watcher=FeedWatcher(fetch_fn=lambda token: []),
                  notify_fn=got.append)
    bot.store.audit("mimicked", {"coin": "BNB", "margin_usd": 2.03,
                                 "source": "SRC-9", "side": "SHORT"})
    summary = {"mimicked": 0, "closed": 0, "skipped": 0, "alerts": 0,
               "errors": 0}
    bot._close_coin("BNB", bot.mirrored_coins(), summary, "sims-missing")
    assert summary["closed"] == 1 and summary["errors"] == 0
    assert bot.open_count() == 0
    assert any("already flat" in n for n in got)


def test_stranger_posts_never_mimicked(tmp_path):
    """Pariston-only guarantee: other traders' opens, closes and updates
    are ignored entirely — no orders, no records, no close attempts."""
    got = []
    feed = PostFeed([POST_OPEN, STRANGER])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")  # index both
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    feed.posts.append(dict(STRANGER, id="post-X2"))
    feed.posts.append(dict(STRANGER_CLOSE, id="post-XC"))
    out = bot.run_once("tok")
    assert out["mimicked"] == 1
    assert hl.opens == [("HYPE", "SHORT", round(18.38 * 0.25, 2), 4)]
    assert hl.closes == []
    assert all("BTC" not in n for n in got)
    assert bot.open_count() == 1


def test_pariston_increase_adds_capped_position(tmp_path):
    """Pariston adds to a mirrored setup -> second capped mimic (HL nets
    same-coin size automatically)."""
    got = []
    feed = PostFeed([POST_OPEN])
    mc = MirrorConfig(email="", app_password="", device_id="d",
                      data_dir=str(tmp_path))
    hl, sess = FakeHl(), FakeSession()
    bot = AutoBot(mc, _cfg(live=True), hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=feed),
                  notify_fn=got.append)
    bot.run_once("tok")
    feed.posts.append(dict(POST_OPEN, id="post-2"))
    bot.run_once("tok")
    assert bot.open_count() == 1
    increase = {"id": "post-4", "update": dict(
        POST_OPEN["update"], changes={"isAdded": False})}
    feed.posts.append(increase)
    out = bot.run_once("tok")
    assert out["mimicked"] == 1
    assert len(hl.opens) == 2
    assert bot.open_count() == 2
    assert bot.committed_usd() == round(round(18.38 * 0.25, 2) * 2, 2)
