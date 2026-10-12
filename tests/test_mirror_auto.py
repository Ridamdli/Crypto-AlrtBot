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
    def __init__(self):
        self.opens = []
        self.closes = []
        self.flat = False

    def meta(self):
        return {"HYPE": {"assetIndex": 10, "szDecimals": 2, "maxLeverage": 10}}

    def position(self, coin):
        return None if self.flat else None

    def market_open(self, coin, side, margin_usd, leverage):
        assert margin_usd > 0 and leverage >= 1
        self.opens.append((coin, side, margin_usd, leverage))
        return {"coin": coin, "size": 1.5, "avg_px": 85.0, "oid": 1,
                "leverage": leverage, "raw": {"ok": True}}

    def close(self, coin):
        self.closes.append(coin)
        return {"coin": coin, "raw": {"ok": True}}


class FakeSession:
    def __init__(self):
        self.opens = []
        self.closes = []

    def record_open(self, payload):
        assert payload["mimicMeta"]["sourcePaperTradeBaseId"] == "SRC-1"
        assert payload["mimicMeta"]["initialSourcePaperUpdateId"] == "post-2"
        self.opens.append(payload)
        return {"success": True}

    def record_close(self, payload):
        self.closes.append(payload)
        return {"success": True}


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
