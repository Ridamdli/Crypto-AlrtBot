"""Mirror entrypoint: `python -m mirror` (watch) or `python -m mirror auto`.

Modes (MIRROR_MODE):
- watch (default): detect-only notifier. Cannot trade (package safety
  test enforces it for the watch path).
- auto: headless auto-mimic — feed detection + capital brain + HL agent
  execution + Invo recording. Live money moves only with MIMIC_LIVE=true;
  default is dry-run (detect + size + audit, zero orders). Needs
  HL_AGENT_KEY + HL_WALLET_ADDRESS; INVO_TRADING_REFRESH enables recording
  (session persists rotations under the data dir, gitignored).
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
        agent_key = os.getenv("HL_AGENT_KEY", "")
        wallet = os.getenv("HL_WALLET_ADDRESS", "")
        if mg.live and not (agent_key and wallet):
            raise SystemExit("mirror misconfigured for LIVE: missing "
                             "HL_AGENT_KEY / HL_WALLET_ADDRESS (refused to "
                             "run live without execution credentials)")
        if not mg.live:
            print("[Mirror] dry-run mode: detection + sizing + audit, "
                  "zero orders (set MIMIC_LIVE=true to execute).")
        hl = HlBridge(agent_key, wallet)
        sess = TradingSession(
            refresh_token=os.getenv("INVO_TRADING_REFRESH", ""),
            access_token=os.getenv("INVO_TRADING_TOKEN", ""),
            state_path=os.path.join(cfg.data_dir, "trading_session.json"))
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
