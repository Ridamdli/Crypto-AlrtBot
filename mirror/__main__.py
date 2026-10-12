"""Mirror entrypoint: `python -m mirror` (start command on its Railway service)."""
from mirror.config import MirrorConfig
from mirror.service import MirrorBot


def main() -> None:
    cfg = MirrorConfig.from_env()
    if not cfg.dry_run:
        missing = cfg.missing_live()
        if missing:
            raise SystemExit(f"mirror misconfigured for LIVE: missing {missing}")
    MirrorBot(cfg).run_forever()


if __name__ == "__main__":
    main()
