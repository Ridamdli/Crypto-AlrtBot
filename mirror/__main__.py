"""Mirror entrypoint: `python -m mirror` (start command on its Railway service).

Runs the detect-only watcher: polls Pariston's portfolio, notifies on new
shares and source closes. It cannot trade — no share/close/modify code
exists anywhere in this package (enforced by tests/test_mirror_safety.py).
Live mimics happen only as explicit, per-trade approved UI confirms in an
operator session, never from here.
"""
import os

from mirror.config import MirrorConfig
from mirror.watch import WatcherBot


def main() -> None:
    cfg = MirrorConfig.from_env()
    missing = cfg.missing_auth()
    if missing:
        raise SystemExit(f"mirror misconfigured: missing {missing}")
    bot = WatcherBot(cfg)
    bot.set_telegram(os.getenv("MIRROR_TELEGRAM_BOT_TOKEN", ""),
                     os.getenv("MIRROR_TELEGRAM_CHAT_ID", ""))
    bot.run_forever()


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
