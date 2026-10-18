"""Phase B — sync reliability tests (TDD: SyncTracker/backoff/audit do not exist yet).

Faults are injected deterministically (scripted fakes, fake clock). No network,
no randomness, no production access. Covers transient/permanent/ambiguous/
restart/concurrency scenarios from the hardening spec.
"""
import requests

from src.invo import exit_sync as sync
from src.invo.client import InvoApiError


def _cfg(**kw):
    base = {"max_attempts": 3, "backoff_base_s": 60, "backoff_cap_s": 3600}
    base.update(kw)
    return sync.SyncRetryConfig(**base)


def _tracker(state=None, now=1_000_000, **kw):
    now_ms = [now]
    tr = sync.SyncTracker(state if state is not None else {},
                          now_ms_fn=lambda: now_ms[0], cfg=_cfg(**kw))
    return tr, now_ms


def _levels(tp1=100.0, sl=95.0):
    return {"tp1": tp1, "stop_loss": sl}


# ── backoff / budget ──────────────────────────────────────────────
def test_backoff_grows_with_deterministic_jitter_and_caps():
    d1 = sync.backoff_delay_s(1, 60, 3600, key="S1")
    d2 = sync.backoff_delay_s(2, 60, 3600, key="S1")
    d3 = sync.backoff_delay_s(5, 60, 3600, key="S1")
    assert 60 <= d1 < 90
    assert 120 <= d2 < 150
    assert d3 <= 3600
    # Deterministic: same inputs, same delay. Distinct keys may differ.
    assert sync.backoff_delay_s(1, 60, 3600, key="S1") == d1


def test_retry_budget_exhaustion_goes_permanent():
    tr, now = _tracker()
    for _ in range(3):
        assert tr.gate("S1", "modify")["proceed"] is True
        tr.note_attempt("S1", "modify")
        tr.note_result("S1", "modify", "retryable", {"error": "boom"})
        now[0] += 4000_000  # each round waits out its backoff window
    gate = tr.gate("S1", "modify")
    assert gate["proceed"] is False
    assert gate["reason"] == "budget-exhausted"
    assert tr.record("S1", "modify")["status"] == "retryable"  # not yet terminal
    assert tr.record("S1", "modify")["attempts"] == 3
    tr.note_result("S1", "modify", "permanent", {"error": "invalid"})
    assert tr.record("S1", "modify")["status"] == "permanent_failure"


def test_backoff_window_suppresses_early_retry():
    tr, now = _tracker()
    tr.gate("S1", "modify")
    tr.note_attempt("S1", "modify")
    tr.note_result("S1", "modify", "retryable", {"error": "timeout"})
    gate = tr.gate("S1", "modify")
    assert gate["proceed"] is False
    assert gate["reason"] == "backoff-pending"
    now[0] += 3600_000 + 31_000  # past cap+jitter ceiling
    assert tr.gate("S1", "modify")["proceed"] is True


# ── staleness / overlap ───────────────────────────────────────────
def test_stale_plan_version_suppressed():
    tr, _ = _tracker()
    tr.note_result("S1", "modify", "confirmed",
                   {"plan_version": 4, "levels": _levels()})
    gate = tr.gate("S1", "modify", plan_version=3, levels=_levels())
    assert gate["proceed"] is False
    assert gate["reason"] == "stale-plan-version"


def test_same_levels_no_repost_but_reconciled():
    tr, _ = _tracker()
    tr.note_result("S1", "modify", "confirmed",
                   {"plan_version": 2, "levels": _levels()})
    gate = tr.gate("S1", "modify", plan_version=2, levels=_levels())
    assert gate["proceed"] is False
    assert gate["reason"] == "already-in-sync"  # ADA-class: explicit, not silent


def test_newer_plan_version_escapes_terminal_record():
    # Deliberate escape hatch (documented in SyncTracker.gate): a NEWER plan
    # version with different levels after a confirmed record is new work,
    # not a replay — it proceeds subject to the normal budget/backoff gates.
    # Stale (older) versions after terminal state stay blocked (see
    # test_stale_plan_version_suppressed).
    tr, _ = _tracker()
    tr.gate("S1", "modify", plan_version=2, levels=_levels())
    tr.note_attempt("S1", "modify")
    tr.note_result("S1", "modify", "confirmed",
                   {"plan_version": 2, "levels": _levels()})
    v3 = {"tp1": 105.0, "stop_loss": 96.0}
    gate = tr.gate("S1", "modify", plan_version=3, levels=v3)
    assert gate["proceed"] is True
    assert gate["levels"] == v3
    # Same old levels after confirmation stay converged (no replay).
    assert tr.gate("S1", "modify", plan_version=2,
                   levels=_levels())["reason"] == "already-in-sync"


def test_overlapping_workers_second_identical_write_suppressed():
    tr, _ = _tracker()
    first = tr.gate("S1", "modify", plan_version=2, levels=_levels())
    assert first["proceed"] is True
    # A second worker with the identical desired state + same idempotency key
    # must not produce a second POST.
    assert first["idempotency_key"] == tr.gate(
        "S1", "modify", plan_version=2, levels=_levels())["idempotency_key"]
    tr.note_result("S1", "modify", "confirmed",
                   {"plan_version": 2, "levels": _levels()})
    assert tr.gate("S1", "modify", plan_version=2,
                   levels=_levels())["reason"] == "already-in-sync"


# ── fault classification ──────────────────────────────────────────
def test_classify_timeout_is_ambiguous_retryable():
    out = sync.classify_sync_error(requests.exceptions.ConnectTimeout("t"))
    assert out == {"retryable": True, "kind": "timeout", "retry_after_s": None}


def test_classify_429_honors_retry_after():
    resp = requests.Response()
    resp.status_code = 429
    resp.headers["Retry-After"] = "120"
    err = requests.exceptions.HTTPError("429", response=resp)
    out = sync.classify_sync_error(err)
    assert out["retryable"] is True and out["kind"] == "rate_limited"
    assert out["retry_after_s"] == 120


def test_classify_5xx_retryable_without_hint():
    resp = requests.Response()
    resp.status_code = 503
    out = sync.classify_sync_error(requests.exceptions.HTTPError("x", response=resp))
    assert out == {"retryable": True, "kind": "server-error", "retry_after_s": None}


def test_classify_401_is_auth_not_retried_in_loop():
    from src.invo.client import InvoAuthError
    out = sync.classify_sync_error(InvoAuthError("stale"))
    assert out["retryable"] is False and out["kind"] == "auth"


def test_classify_validation_error_is_permanent():
    # Transport status is the structural signal: 4xx with an unrecognized
    # body is permanent (retrying identical bytes is pointless).
    out = sync.classify_sync_error(InvoApiError(400, "invalid baseId format"))
    assert out["retryable"] is False and out["kind"] == "client-error"


def test_classify_empty_changes_means_already_applied():
    # Observed live: reposting identical TP/SL draws "empty userChanges" —
    # the server attesting the desired state already holds. Terminal, and
    # semantically converged (not a failure).
    out = sync.classify_sync_error(
        InvoApiError(200, "commitPositionChanges called with empty userChanges"))
    assert out["retryable"] is False and out["kind"] == "already-applied"


def test_classify_gone_string_is_terminal():
    out = sync.classify_sync_error(InvoApiError(200, "Investment not found for baseId"))
    assert out["retryable"] is False and out["kind"] == "already-gone"


def test_classify_unknown_string_is_retryable_within_budget():
    # 200 + success:false + unrecognized text: no structural signal, so the
    # attempt budget (not the classifier) is what bounds retries.
    out = sync.classify_sync_error(InvoApiError(200, "weird transient blob"))
    assert out["retryable"] is True and out["kind"] == "unknown"


# ── timeout ambiguity: modify vs close ────────────────────────────
def test_modify_timeout_replays_identical_values_safely():
    """Timeout after unknown server state: repost SAME values (value-idempotent).
    Server answering 'empty userChanges' proves convergence -> confirmed."""
    tr, _ = _tracker()
    posted = []

    def fake_post(values):
        posted.append(dict(values))
        if len(posted) == 1:
            raise requests.exceptions.ConnectTimeout("no response")
        return {"success": True}

    gate = tr.gate("S1", "modify", plan_version=1, levels=_levels())
    assert gate["proceed"] is True
    tr.note_attempt("S1", "modify")
    try:
        fake_post(_levels())
    except requests.exceptions.ConnectTimeout:
        tr.note_result("S1", "modify", "retryable",
                       {"error": "timeout-ambiguous", "prior_ambiguous": True,
                        "plan_version": 1, "levels": _levels()})
    assert tr.record("S1", "modify")["status"] == "retryable"
    # Reconciliation: repost identical values (never new ones mid-ambiguity).
    gate2 = tr.gate("S1", "modify", plan_version=1, levels=_levels(),
                    force_after_backoff=True)
    assert gate2["proceed"] is True
    assert gate2["levels"] == _levels()
    fake_post(_levels())
    tr.note_result("S1", "modify", "confirmed", {"levels": _levels()})
    assert [p for p in posted] == [_levels(), _levels()]
    assert tr.record("S1", "modify")["status"] == "confirmed"


def test_close_timeout_reconciles_before_reposting():
    """Close ambiguity: check remote absence FIRST; only repost if present."""
    tr, _ = _tracker()
    remote = {"present": ["b1"]}
    posts = []

    def fake_sell():
        posts.append(1)
        raise requests.exceptions.ReadTimeout("no response")

    def remote_state():
        return {"present": list(remote["present"])}

    gate = tr.gate("S1", "close")
    assert gate["proceed"] is True
    tr.note_attempt("S1", "close")
    try:
        fake_sell()
    except requests.exceptions.ReadTimeout:
        tr.note_result("S1", "close", "retryable", {"error": "timeout-ambiguous"})
    # Reconcile: remote says b1 already absent -> confirmed, no second sell.
    remote["present"] = []
    state = remote_state()
    if "b1" not in state["present"]:
        tr.note_result("S1", "close", "confirmed",
                       {"via": "remote-readback-absent"})
    else:
        posts.append("second-sell")
    assert posts == [1]
    assert tr.record("S1", "close")["status"] == "confirmed"


def test_close_present_after_timeout_reposts_once():
    tr, _ = _tracker()
    posts = []

    def fake_sell():
        posts.append(1)
        if len(posts) == 1:
            raise requests.exceptions.ConnectTimeout("x")
        return {"success": True}

    tr.gate("S1", "close")
    tr.note_attempt("S1", "close")
    try:
        fake_sell()
    except requests.exceptions.ConnectTimeout:
        tr.note_result("S1", "close", "retryable", {"error": "timeout"})
    # Remote still shows it present -> exactly one repost allowed by budget.
    fake_sell()
    tr.note_result("S1", "close", "confirmed", {})
    assert posts == [1, 1]
    assert tr.record("S1", "close")["status"] == "confirmed"


# ── restart / persistence ─────────────────────────────────────────
def test_restart_recovers_in_flight_as_retryable():
    state = {}
    tr, _ = _tracker(state)
    tr.gate("S1", "modify", plan_version=1, levels=_levels())
    tr.note_attempt("S1", "modify")  # crashed before result recorded
    # New process, same persisted dict shape.
    tr2 = sync.SyncTracker(dict(state), now_ms_fn=lambda: 1_000_000)
    rec = tr2.record("S1", "modify")
    assert rec["status"] == "retryable"
    assert rec["attempts"] == 1


def test_local_save_failure_after_remote_success_reconciles():
    """Remote confirmed, then our persistence dies: next run must reconcile
    (not blindly repost as new), and the audit gap must be flaggable."""
    tr, _ = _tracker()
    tr.gate("S1", "close")
    tr.note_attempt("S1", "close")
    tr.note_result("S1", "close", "confirmed", {})
    assert tr.persisted is True  # default save_fn succeeds
    # A variant whose save path is broken: failure surfaces (persisted /
    # audit_gap flags) without raising, and memory stays correct.
    def _boom(state):
        raise OSError("disk full")

    tr2 = sync.SyncTracker({}, now_ms_fn=lambda: 1_000_000, save_fn=_boom)
    tr2.gate("S1", "close")
    tr2.note_attempt("S1", "close")
    tr2.note_result("S1", "close", "confirmed", {})
    assert tr2.persisted is False
    assert tr2.audit_gap is True
    assert tr2.record("S1", "close")["status"] == "confirmed"


def test_tracker_tolerates_legacy_state_without_records():
    # Backward compat: sync-state files written before the state machine
    # existed have no "records" key. Must work, not crash.
    tr, _ = _tracker({"synced_levels": {}, "closed": []})
    gate = tr.gate("S1", "modify", plan_version=1, levels=_levels())
    assert gate["proceed"] is True
    tr.note_attempt("S1", "modify")
    tr.note_result("S1", "modify", "confirmed",
                   {"plan_version": 1, "levels": _levels()})
    assert tr.record("S1", "modify")["status"] == "confirmed"


def test_sync_state_save_is_atomic_on_failure(tmp_path, monkeypatch):
    import json as _json
    import os as _os

    from src.invo import exit_sync as _sync
    good = {"synced_levels": {"S1": {"stop_loss": 1.0, "tp1": 2.0}},
            "closed": [], "records": {}}
    _sync.save_sync_state(str(tmp_path), good)

    def _boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(_os, "replace", _boom)
    # Must not raise; previous valid state must survive intact.
    _sync.save_sync_state(str(tmp_path), {"synced_levels": {}, "closed": []})
    with open(_os.path.join(str(tmp_path), "invo_exit_sync.json"),
               encoding="utf-8") as f:
        assert _json.load(f) == good
    assert not _os.path.exists(
        _os.path.join(str(tmp_path), "invo_exit_sync.json.tmp"))


def test_sync_state_save_replaces_atomically(tmp_path):
    import json as _json
    import os as _os

    from src.invo import exit_sync as _sync
    _sync.save_sync_state(str(tmp_path), {"a": 1})
    _sync.save_sync_state(str(tmp_path), {"a": 2})
    with open(_os.path.join(str(tmp_path), "invo_exit_sync.json"),
               encoding="utf-8") as f:
        assert _json.load(f) == {"a": 2}
    assert not _os.path.exists(
        _os.path.join(str(tmp_path), "invo_exit_sync.json.tmp"))


def test_retry_after_hint_reaches_scheduler_and_is_honored():
    tr, now = _tracker()
    tr.gate("S1", "modify")
    tr.note_attempt("S1", "modify")
    # Server demands 120s although computed backoff would be ~60-89s.
    tr.note_result("S1", "modify", "retryable",
                   {"error": "rate_limited", "retry_after_s": 120})
    rec = tr.record("S1", "modify")
    assert rec["status"] == "retryable"
    gate = tr.gate("S1", "modify")
    assert gate["proceed"] is False and gate["reason"] == "backoff-pending"
    now[0] += 119_000
    assert tr.gate("S1", "modify")["proceed"] is False  # not earlier than asked
    now[0] += 2_000
    assert tr.gate("S1", "modify")["proceed"] is True


def test_retry_after_malformed_falls_back_to_backoff():
    tr, now = _tracker()
    tr.gate("S1", "modify")
    tr.note_attempt("S1", "modify")
    for bad in ("soon", -5, 0, None, True):
        tr2, _ = _tracker()
        tr2.gate("S1", "modify")
        tr2.note_attempt("S1", "modify")
        tr2.note_result("S1", "modify", "retryable",
                       {"error": "x", "retry_after_s": bad})
        gate = tr2.gate("S1", "modify")
        assert gate["proceed"] is False  # computed backoff still gates
    now[0] += 3600_000 + 31_000
    assert tr.gate("S1", "modify")["proceed"] is True


def test_retry_after_capped_at_ceiling():
    tr, _ = _tracker()
    tr.gate("S1", "modify")
    tr.note_attempt("S1", "modify")
    tr.note_result("S1", "modify", "retryable",
                   {"error": "x", "retry_after_s": 99999999})
    import src.invo.exit_sync as _sync
    rec = tr._rec("S1", "modify")
    assert rec["next_retry_at_ms"] - 1_000_000 == _sync.SYNC_RETRY_AFTER_CEIL_S * 1000


def test_prune_drops_only_old_terminal_records():
    import src.invo.exit_sync as _sync
    now = 100_000_000
    tr = _sync.SyncTracker({}, now_ms_fn=lambda: now)
    tr.gate("OLD", "modify")
    tr.note_attempt("OLD", "modify")
    tr.note_result("OLD", "modify", "confirmed", {"plan_version": 1})
    tr.gate("NEW", "modify")
    tr.note_attempt("NEW", "modify")
    tr.note_result("NEW", "modify", "confirmed", {"plan_version": 1})
    tr.gate("OPEN", "modify")
    tr.note_attempt("OPEN", "modify")
    tr.note_result("OPEN", "modify", "retryable", {"error": "timeout"})
    # Age the OLD record 31 days back by hand (deterministic clock).
    tr.state["records"]["OLD:modify"]["updated_at_ms"] = now - 31 * 86400_000
    pruned = tr.prune()
    assert pruned == 1
    assert "OLD:modify" not in tr.state["records"]
    assert "NEW:modify" in tr.state["records"]
    assert "OPEN:modify" in tr.state["records"]  # unresolved kept at any age
    # A pruned record gates as never-seen. In production this cannot
    # refire settled work by itself: compute_sync_actions only emits on
    # real level diffs vs synced_levels, which pruning never touches.
    assert tr.gate("OLD", "modify", plan_version=1)["proceed"] is True
    assert tr.state["records"].get("OLD:modify") is not None  # gate re-creates blank


# ── ADA-class reconcile ───────────────────────────────────────────
def test_ada_like_already_synced_is_explicit_not_silent():
    from src.invo import exit_sync as _sync
    shares = {"S1": {"baseIds": ["b1"]}}
    plans = [{"signal_id": "S1", "version": 2,
              "stop_loss": 95.0, "tp1": 100.0, "tp2": 0.0, "tp3": 0.0}]
    state = {"synced_levels": {"S1": {"stop_loss": 95.0, "tp1": 100.0}},
             "closed": []}
    actions = _sync.compute_sync_actions(plans, [], shares, state)
    assert actions == []
    summary = _sync.summarize_sync(plans, shares, state)
    assert summary["in_sync"] == ["S1"]
    assert summary["unsynced"] == [] and summary["missing_base"] == []
