"""Phase B — volume lockfile tests (TDD: src/invo/volume_lock.py missing).

Portable lock (O_CREAT|O_EXCL + mtime expiry): no fcntl dependency, works on
Windows dev machines and Linux containers alike. Best-effort by design —
callers must fail OPEN (skip the guarded section with a warning), never
deadlock the scan loop. Stale-owner takeover prevents a crashed holder from
wedging the system forever.
"""
import json
import os
import time

from src.invo import volume_lock as vl


def test_acquire_release_roundtrip(tmp_path):
    tok = vl.acquire_lock(str(tmp_path), "sync", owner="w1")
    assert tok
    assert vl.lock_status(str(tmp_path), "sync")["held"] is True
    assert vl.release_lock(str(tmp_path), "sync", tok) is True
    assert vl.lock_status(str(tmp_path), "sync")["held"] is False
    # Double release is harmless.
    assert vl.release_lock(str(tmp_path), "sync", tok) is False


def test_second_acquirer_denied_while_held(tmp_path):
    tok1 = vl.acquire_lock(str(tmp_path), "sync", owner="w1")
    assert tok1
    assert vl.acquire_lock(str(tmp_path), "sync", owner="w2") is None
    st = vl.lock_status(str(tmp_path), "sync")
    assert st["held"] is True and st["owner"] == "w1"


def test_wrong_token_cannot_release(tmp_path):
    tok = vl.acquire_lock(str(tmp_path), "sync", owner="w1")
    assert vl.release_lock(str(tmp_path), "sync", "not-the-token") is False
    assert vl.lock_status(str(tmp_path), "sync")["held"] is True
    assert vl.release_lock(str(tmp_path), "sync", tok) is True


def test_stale_lock_taken_over(tmp_path):
    tok1 = vl.acquire_lock(str(tmp_path), "sync", owner="crashed",
                           stale_after_s=60)
    assert tok1
    path = os.path.join(str(tmp_path), "sync.lock")
    old = time.time() - 3600
    os.utime(path, (old, old))  # simulate a holder dead for an hour
    st = vl.lock_status(str(tmp_path), "sync", stale_after_s=60)
    assert st["held"] is True and st["stale"] is True
    tok2 = vl.acquire_lock(str(tmp_path), "sync", owner="w2",
                           stale_after_s=60)
    assert tok2 and tok2 != tok1
    assert vl.lock_status(str(tmp_path), "sync")["owner"] == "w2"


def test_fresh_leftover_lock_still_blocks(tmp_path):
    vl.acquire_lock(str(tmp_path), "sync", owner="maybe-alive",
                    stale_after_s=3600)
    # A fresh lock file (e.g. sibling starting up) must NOT be stolen.
    assert vl.acquire_lock(str(tmp_path), "sync", owner="w2",
                           stale_after_s=3600) is None


def test_corrupt_lockfile_never_blindly_deleted(tmp_path):
    path = os.path.join(str(tmp_path), "sync.lock")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{corrupt")
    # Fresh but unparseable: deny (a live holder may own it; fail safe).
    assert vl.acquire_lock(str(tmp_path), "sync", owner="w1") is None
    # Stale and unparseable: torn write from a dead holder; take over.
    old = time.time() - 3600
    os.utime(path, (old, old))
    tok = vl.acquire_lock(str(tmp_path), "sync", owner="w1",
                          stale_after_s=60)
    assert tok
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["owner"] == "w1"


def test_lock_payload_carries_owner_and_timestamp(tmp_path):
    before = time.time()
    vl.acquire_lock(str(tmp_path), "sync", owner="w9")
    with open(os.path.join(str(tmp_path), "sync.lock"), encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["owner"] == "w9"
    assert before <= payload["acquired_at"] <= time.time()
    assert payload["token"]
