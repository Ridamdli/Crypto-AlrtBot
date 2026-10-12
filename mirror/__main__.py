"""Mirror entrypoint: `python -m mirror` (watch) or `python -m mirror auto`.

Modes (MIRROR_MODE):
- watch (default): detect-only notifier. Cannot trade (package safety
  test enforces it for the watch path).
- auto: full auto-mimic — watcher + capital brain + CDP executor that
  taps confirm in the operator's signed-in Chrome. Live money moves
  only with MIMIC_LIVE=true; default is dry-run (navigate + read,
  never confirm). Needs Chrome with --remote-debugging-port=9222.
"""
import os

from mirror.config import MirrorConfig


def main() -> None:
    cfg = MirrorConfig.from_env()
    mode = os.getenv("MIRROR_MODE", "watch").lower()
    if mode == "auto":
        from mirror.auto import AutoBot, MimicConfig
        from mirror.feedmon import FeedWatcher
        from mirror.hl import HlBridge
        from mirror.recorder import TradingSession
        mg = MimicConfig.from_env()
        missing = cfg.missing_auth()
        if missing:
            raise SystemExit(f"mirror misconfigured: missing {missing}")
        hl = HlBridge(os.getenv("HL_AGENT_KEY", ""),
                      os.getenv("HL_WALLET_ADDRESS", ""))
        sess = TradingSession(
            refresh_token=os.getenv("INVO_TRADING_REFRESH", ""),
            access_token=os.getenv("INVO_TRADING_TOKEN", ""))
        AutoBot(cfg, mg, hl=hl, session=sess,
                watcher=FeedWatcher()).run_forever()
        return
    from mirror.watch import WatcherBot
    bot = WatcherBot(cfg)
    bot.set_telegram(os.getenv("MIRROR_TELEGRAM_BOT_TOKEN", ""),
                     os.getenv("MIRROR_TELEGRAM_CHAT_ID", ""))
    bot.run_forever()


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
