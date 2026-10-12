"""Open one live mimic of an already-open Pariston position. REAL MONEY.

Two-key safety: needs BOTH env MIMIC_LIVE=true AND --live flag, otherwise
dry-run (plan printed, nothing posted). Single coin per run. Aborts if
the wallet already holds that coin (no blind adds).
Env: HL_AGENT_KEY, HL_WALLET_ADDRESS, INVO_TRADING_REFRESH (optional),
     MIRROR_INVO_EMAIL / MIRROR_INVO_GMAIL_APP_PASSWORD /
     MIRROR_INVO_DEVICE_ID, MIMIC_* caps.
Usage: python scripts/mimic_open_once.py [--coin LINK] [--live]
"""
import os
import sys

sys.path.insert(0, ".")
PREF = ("LINK", "HYPE", "UNI", "DOT", "AVAX", "NEAR", "SOL", "XRP")


def main() -> None:
    live = "--live" in sys.argv and os.getenv("MIMIC_LIVE", "false").lower() == "true"
    want = ""
    if "--coin" in sys.argv:
        want = sys.argv[sys.argv.index("--coin") + 1].upper().replace("USDT", "")

    from src.invo.client import InvoClient
    from mirror.auto import (AutoBot, MimicConfig, PARISTON_OWNER_ID,
                             PARISTON_PORTFOLIO_ID)
    from mirror.config import MirrorConfig
    from mirror.feedmon import FeedWatcher
    from mirror.hl import HlBridge
    from mirror.recorder import TradingSession

    email = os.getenv("MIRROR_INVO_EMAIL", "")
    pw = os.getenv("MIRROR_INVO_GMAIL_APP_PASSWORD", "")
    dev = os.getenv("MIRROR_INVO_DEVICE_ID", "")
    data_dir = os.getenv("MIRROR_DATA_DIR", "mirror_data")
    client = InvoClient(email=email, storage_dir=data_dir)
    client.ensure_auth(email=email, app_password=pw, device_id=dev)
    token = client.access_token or ""
    from mirror.feed import fetch_open_positions
    positions = fetch_open_positions(client, token, PARISTON_PORTFOLIO_ID)
    print("pariston open:", [(p["ticker"], p["side"], p["leverage"]) for p in positions])
    if want:
        positions = [p for p in positions if p["ticker"] == want]
    else:
        positions.sort(key=lambda p: PREF.index(p["ticker"])
                       if p["ticker"] in PREF else 99)
        positions = positions[:1]
    if not positions:
        raise SystemExit("no matching open Pariston position")
    pos = positions[0]

    mc = MirrorConfig(email=email, app_password=pw, device_id=dev,
                      data_dir=data_dir)
    mg = MimicConfig.from_env()
    hl = HlBridge(os.getenv("HL_AGENT_KEY", ""),
                  os.getenv("HL_WALLET_ADDRESS", ""))
    sess = TradingSession(refresh_token=os.getenv("INVO_TRADING_REFRESH", ""))
    bot = AutoBot(mc, mg, hl=hl, session=sess,
                  watcher=FeedWatcher(fetch_fn=lambda t: []),
                  notify_fn=print)
    coin = pos["ticker"]
    if hl.position(coin) is not None:
        raise SystemExit(f"ABORT: wallet already holds {coin} (manual review)")
    mg_live = MimicConfig(live=live, account_usd=mg.account_usd,
                          max_usd_per_mimic=mg.max_usd_per_mimic,
                          max_pct_per_mimic=mg.max_pct_per_mimic,
                          max_concurrent=mg.max_concurrent,
                          min_remaining_usd=mg.min_remaining_usd,
                          feed_size=mg.feed_size)
    bot.cfg = mg_live
    ev = {"action": "open", "post_id": f"bootstrap-{pos['baseId']}",
          "base_id": pos["baseId"], "base_short_id": "",
          "ticker": coin, "side": pos.get("side", "SHORT"),
          "leverage": pos.get("leverage", 10),
          "owner_id": PARISTON_OWNER_ID, "portfolio_id": PARISTON_PORTFOLIO_ID}
    summary: dict = {"mimicked": 0, "closed": 0, "skipped": 0,
                     "alerts": 0, "errors": 0}
    bot._mimic_open(ev, summary)
    print("SUMMARY:", summary)
    if live:
        print("HL position now:", hl.position(coin))


if __name__ == "__main__":
    main()
