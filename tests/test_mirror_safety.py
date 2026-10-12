"""Package safety: mirror/ must be unable to move money, by construction.

Scans every module in the package for trading-capable markers and asserts
the entrypoint runs only the detect-only watcher. If anyone adds a share,
close, modify, or DEX call to this package, this test fails first.
"""
import pathlib

MIRROR_DIR = pathlib.Path(__file__).resolve().parent.parent / "mirror"

FORBIDDEN = (
    "share_paper_trade",
    "close_paper_trade",
    "update_paper_trade",
    "ticker/create",
    "ticker/modify",
    "/sell",
    "/dex/mimic/copy",
    "/dex/position/create",
    "share_position",
    "record_mirror",
)


def _sources():
    return sorted(MIRROR_DIR.glob("*.py"))


def test_no_trading_markers_anywhere_in_package():
    files = _sources()
    assert len(files) >= 6, f"mirror package shrank unexpectedly: {files}"
    violations = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker in text:
                violations.append(f"{path.name}: {marker}")
    assert violations == [], f"trading power in detect-only package: {violations}"


def test_entrypoint_runs_watcher_only():
    text = (MIRROR_DIR / "__main__.py").read_text(encoding="utf-8")
    assert "WatcherBot" in text
    assert "MirrorBot" not in text
    assert "MIRROR_MODE" not in text
