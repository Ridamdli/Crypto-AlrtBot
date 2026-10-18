"""Repo-wide storage guard: no test may modify data_store/ or mirror_data/.

The audit found isolation scattered per test module with no global backstop,
so a single bare constructor could silently append to live storage (it did:
invo_activity.jsonl grew during full-suite runs). This autouse fixture
snapshots both storage roots around every test and fails the test that
changed them.

shortcut: detects create/delete/size/mtime change only — a same-size rewrite
inside one filesystem mtime tick would slip; switch to content hashing if
that ever matters.
"""
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_DIRS = ("data_store", "mirror_data")
# Host env must not open scheduler's repo-writing Invo share path;
# tests that need it open set it themselves via monkeypatch.setenv.
_GATED_ENV = ("INVO_PAPER_ENABLED",)


def _snapshot():
    snap = {}
    for name in _GUARD_DIRS:
        root = _REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    st = path.stat()
                except OSError:
                    continue  # vanished mid-walk: reported by the diff below
                snap[path] = (st.st_size, st.st_mtime_ns)
    return snap


@pytest.fixture(autouse=True)
def repo_storage_untouched(monkeypatch):
    for var in _GATED_ENV:
        monkeypatch.delenv(var, raising=False)
    before = _snapshot()
    yield
    after = _snapshot()
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    assert not (created or removed or changed), (
        f"test mutated repo storage — pin it to tmp_path. "
        f"created={created} removed={removed} changed={changed}"
    )
