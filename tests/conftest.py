"""Repo-wide storage guard: no test may modify data_store/ or mirror_data/.

The audit found isolation scattered per test module with no global backstop,
so a single bare constructor could silently append to live storage (it did:
invo_activity.jsonl grew during full-suite runs). This autouse fixture
snapshots both storage roots around every test and fails the test that
changed them.

`PG_TEST=1` (plus TEST_DATABASE_URL) runs the same suite against the Postgres
backend instead: DATABASE_URL is pointed at the test branch and every table is
truncated before each test, because the JSON stores get isolation from
tmp_path and the DB stores do not. Default runs are untouched.

shortcut: detects create/delete/size/mtime change only — a same-size rewrite
inside one filesystem mtime tick would slip; switch to content hashing if
that ever matters.
"""
import os
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


def _pg_on() -> bool:
    return bool(os.getenv("PG_TEST"))


@pytest.fixture(scope="session", autouse=True)
def pg_backend():
    """PG_TEST=1: run the suite on the Postgres backend (TEST_DATABASE_URL).

    Otherwise the suite is the file-backend suite by definition: `.env` carries
    DATABASE_URL for deploys, so drop it before any store is constructed.
    """
    from src.db.engine import reset_engine

    if not _pg_on():
        # Blank, not popped: modules call load_dotenv() at import, which would
        # restore a popped DATABASE_URL. Blank reads as "no database".
        os.environ["DATABASE_URL"] = ""
        reset_engine()
        yield
        return
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    assert url, "PG_TEST=1 requires TEST_DATABASE_URL (Neon test branch)"
    os.environ["DATABASE_URL"] = url
    reset_engine()
    yield
    reset_engine()
    os.environ["DATABASE_URL"] = ""


@pytest.fixture(autouse=True)
def pg_reset(pg_backend):
    """Empty every table before each test — the DB equivalent of tmp_path."""
    if not _pg_on():
        yield
        return
    from sqlalchemy import text

    from src.db import get_session_factory
    from src.db import models  # noqa: F401  (registers tables)
    from src.db.base import Base

    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_session_factory()() as s, s.begin():
        s.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


# Tests that assert on JSON-store artifacts: files the Postgres backend never
# writes (sched_state.json, outcomes.json, paper_portfolio.json,
# invo_exit_sync.json), a corrupt file failing safe, the 2,000-entry in-memory
# list, or the file lock's owner token. They cover the fallback backend, so
# they keep running in the default (JSON) suite and are skipped only when
# PG_TEST=1. Listed by node name so the skip is visible in one place.
_JSON_ONLY = {
    "test_ledger_corrupt_file_fails_safe",
    "test_seen_keys_survive_decision_eviction",
    "test_legacy_file_backfills_keys_without_rewrite",
    "test_legacy_rows_without_applied_read_as_unknown",
    "test_corrupt_file_keys_fail_safe_empty",
    "test_monitor_skips_final_and_invalidated",
    "test_record_share_preserves_base_ids_on_failed_write",
    "test_scheduler_marks_gone_positions_closed",
    "test_close_absent_remotely_never_reposts",
    "test_close_present_remotely_sells_once",
    "test_close_list_unavailable_preserves_existing_behavior",
    "test_429_retry_after_honored_end_to_end",
    "test_locked_sync_applies_and_is_idempotent",
    "test_share_guard_legacy_when_snapshot_fails_final_outcome",
    "test_settle_books_final_once_and_updates_equity",
    "test_failed_send_not_ledgered",
    "test_disabled_send_not_ledgered",
    "test_successful_send_ledgered",
    "test_failed_send_retries_next_cycle",
    "test_sync_state_save_is_atomic_on_failure",
    "test_sync_state_save_replaces_atomically",
    "test_track_all_persists_ledger",
    "test_second_acquirer_denied_while_held",
    "test_wrong_token_cannot_release",
    "test_stale_lock_taken_over",
    "test_corrupt_lockfile_never_blindly_deleted",
    "test_lock_payload_carries_owner_and_timestamp",
}


def pytest_collection_modifyitems(config, items):
    if not _pg_on():
        return
    skip = pytest.mark.skip(reason="asserts JSON-store artifacts (file backend only)")
    seen = set()
    for item in items:
        if item.name in _JSON_ONLY:
            seen.add(item.name)
            item.add_marker(skip)
    unknown = _JSON_ONLY - seen
    if unknown:
        print(f"warning: JSON-only list names no collected test: {sorted(unknown)}")
