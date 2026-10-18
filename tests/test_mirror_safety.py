"""Package safety: the watch path must be unable to move money.

Scans the detect-only modules for trading-capable markers. mirror/auto.py
is the explicit exception: it drives the operator's signed-in browser and
is covered by its own guards (MIMIC_LIVE defaults false, caps, verified
confirms — see tests/test_mirror_auto.py).
"""
import pathlib

MIRROR_DIR = pathlib.Path(__file__).resolve().parent.parent / "mirror"

WATCH_FILES = ("__init__.py", "__main__.py", "brain.py", "config.py",
               "feed.py", "service.py", "store.py", "watch.py")

FORBIDDEN = (
    "share_paper_trade",
    "close_paper_trade",
    "update_paper_trade",
    "ticker/create",
    "ticker/modify",
    "/sell",
    "/dex/mimic/copy",
    "/dex/position/create",
    "/dex/position/close",
    "/dex/position/update",
    "share_position",
    "record_mirror",
)


def test_no_trading_markers_in_watch_path():
    violations = []
    for name in WATCH_FILES:
        path = MIRROR_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker in text:
                violations.append(f"{name}: {marker}")
    assert violations == [], f"trading power in detect-only path: {violations}"


def test_entrypoint_defaults_to_watch():
    text = (MIRROR_DIR / "__main__.py").read_text(encoding="utf-8")
    assert 'os.getenv("MIRROR_MODE", "watch")' in text


def test_auto_live_defaults_off():
    import os

    from mirror.auto import MimicConfig
    for var in ("MIMIC_LIVE", "MIMIC_CDP_URL", "MIMIC_ACCOUNT_USD",
                "MIMIC_MAX_USD", "MIMIC_MAX_PCT", "MIMIC_MAX_CONCURRENT",
                "MIMIC_MIN_REMAINING_USD"):
        os.environ.pop(var, None)
    assert MimicConfig.from_env().live is False
