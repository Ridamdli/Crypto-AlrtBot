"""Tests for the Invo auto-share client (all network mocked — no creds needed)."""
import pytest

from src.invo.client import InvoClient, InvoAuthError


def _sig(**kw):
    base = {"symbol": "BTCUSDT", "side": "LONG", "leverage": 10,
            "tp1": 95000.0, "stop_loss": 89000.0}
    base.update(kw)
    return base


def test_build_position_payload_matches_app_schema():
    c = InvoClient()
    p = c.build_position_payload("BTCUSDT", "LONG", 10, tp_px=95000.0, sl_px=89000.0)
    assert p["coin"] == "BTC"
    assert p["assetIndex"] == 0
    assert p["entry"] == {"side": "long", "marginMode": "isolated",
                          "leverage": 10, "tpPx": 95000.0, "slPx": 89000.0}
    assert "clientTxId" in p


def test_unknown_coin_refuses_to_guess_index():
    c = InvoClient()
    with pytest.raises(ValueError, match="No verified Hyperliquid assetIndex"):
        c.build_position_payload("ZECUSDT", "LONG", 10)


def test_bad_side_rejected():
    c = InvoClient()
    with pytest.raises(ValueError):
        c.build_position_payload("BTCUSDT", "SIDEWAYS", 10)


def test_dry_run_posts_nothing(tmp_path):
    c = InvoClient(storage_dir=str(tmp_path))
    calls = []
    c._post = lambda path, payload: calls.append((path, payload)) or {}
    out = c.share_position(_sig(), dry_run=True)
    assert out["dry_run"] is True
    assert calls == []


def test_live_share_requires_login_first(monkeypatch, tmp_path):
    c = InvoClient(storage_dir=str(tmp_path))
    assert c.access_token is None
    monkeypatch.setattr(c, "ensure_auth",
                        lambda: (_ for _ in ()).throw(InvoAuthError("no creds")))
    with pytest.raises(InvoAuthError):
        c.share_position(_sig(), dry_run=False)


def test_paper_share_payload_matches_captured_shape():
    c = InvoClient()
    p = c.build_paper_share_payload(
        symbol="BTCUSDT", long=False, leverage=7,
        entry_sim=1.1585205451150278, price_target=29,
        stop_loss=None, liquidation_price=93233.43915343916,
        portfolio_id="c43ec3e9-db34-4935-8219-4c81d7bcfb9e",
    )
    assert p == {
        "ticker": "BTC",
        "portfolioId": "c43ec3e9-db34-4935-8219-4c81d7bcfb9e",
        "directionLong": False,
        "entrySim": 1.1585205451150278,
        "priceTarget": 29,
        "stopLoss": None,
        "leverage": 7,
        "liquidationPrice": 93233.43915343916,
    }


def test_paper_headers_carry_required_app_fields():
    h = InvoClient._paper_headers("tok123")
    assert h["authorization"] == "Bearer tok123"
    for k in ("nonce", "timestamp", "x-app-build-number", "x-app-release",
              "x-app-version", "x-platform"):
        assert h[k]


def test_paper_share_dry_run_posts_nothing(monkeypatch):
    c = InvoClient()
    calls = []
    monkeypatch.setattr(c.session, "post",
                        lambda *a, **k: calls.append((a, k)) or None)
    out = c.share_paper_trade(
        {"symbol": "BTCUSDT", "side": "SHORT", "leverage": 7,
         "stop_loss": None, "entry": 81500.0},
        token="tok", portfolio_id="pid",
        entry_sim=1.15, price_target=29, dry_run=True,
    )
    assert out["dry_run"] is True
    assert calls == []
    assert out["payload"]["ticker"] == "BTC"
    assert out["payload"]["directionLong"] is False


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body
        self._body = body

    def json(self):
        import json as _json
        return _json.loads(self._body)


def test_paper_share_success_parses_base_ids(monkeypatch, tmp_path):
    from src.invo.client import InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    monkeypatch.setattr(c.session, "post", lambda *a, **k: _Resp(200, (
        '{"success": true, "baseIds": ["8b3ae3cd-af4a-46cd-a100-1bae4a93ed8e"],'
        ' "remainingSim": 99, "error": null}'
    )))
    out = c.share_paper_trade(
        {"symbol": "BTCUSDT", "side": "SHORT", "leverage": 7, "stop_loss": None},
        token="tok", portfolio_id="pid",
        entry_sim=1.15, price_target=29, dry_run=False,
    )
    assert out["dry_run"] is False
    assert out["response"]["baseIds"] == ["8b3ae3cd-af4a-46cd-a100-1bae4a93ed8e"]
    assert out["response"]["remainingSim"] == 99


def test_paper_share_conflict_raises_with_base_ids(monkeypatch, tmp_path):
    from src.invo.client import InvoConflictError, InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    monkeypatch.setattr(c.session, "post", lambda *a, **k: _Resp(200, (
        '{"success": false, "baseIds": [], "remainingSim": null, "error": '
        '{"msg": "An open investment in this asset already exists.", "code": null, '
        '"data": {"conflicting_base_ids": ["a3050a07-225f-4858-bf6c-19546c9872cf"],'
        ' "failedInvestments": null}}}'
    )))
    import pytest as _pt
    with _pt.raises(InvoConflictError) as exc:
        c.share_paper_trade(
            {"symbol": "BTCUSDT", "side": "SHORT", "leverage": 7, "stop_loss": None},
            token="tok", portfolio_id="pid",
            entry_sim=1.15, price_target=29, dry_run=False,
        )
    assert exc.value.conflicting_base_ids == ["a3050a07-225f-4858-bf6c-19546c9872cf"]


def test_share_ledger_roundtrip(tmp_path):
    from src.invo.client import load_share_ledger, record_share
    record_share(str(tmp_path), "SIG-1", {"baseIds": ["abc"], "symbol": "BTCUSDT"})
    assert load_share_ledger(str(tmp_path))["SIG-1"]["baseIds"] == ["abc"]


def test_paper_share_uses_sim_dollar_size():
    """entrySim = position size in sim dollars (verified live 2026-10-08:
    posting entrySim=10.0 debited remainingSim by exactly 10.0)."""
    from src.invo.client import InvoClient
    c = InvoClient()
    p = c.build_paper_share_payload(
        symbol="BTCUSDT", long=True, leverage=1,
        entry_sim=10.0, price_target=85000,
        stop_loss=78000, liquidation_price=0.0,
        portfolio_id="70bf5669-fa49-4014-b86e-43b2d51f757c",
    )
    assert p["entrySim"] == 10.0
    assert p["priceTarget"] == 85000
    assert p["stopLoss"] == 78000
    assert p["directionLong"] is True
    assert p["ticker"] == "BTC"


def test_otp_self_login_flow(monkeypatch, tmp_path):
    from src.invo.client import InvoClient
    c = InvoClient(email="u@gmail.com", storage_dir=str(tmp_path))

    class R:
        def __init__(self, status, body):
            self.status_code = status
            self.text = body
        def json(self):
            import json as _j
            return _j.loads(self.text)

    calls = []
    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/auth/login/email/start"):
            assert json == {"email": "u@gmail.com"}
            return R(200, '{"success": true}')
        if url.endswith("/auth/login/email"):
            assert json == {"email": "u@gmail.com", "code": "123456",
                            "deviceId": "dev-1", "deviceType": None, "deviceInfo": None}
            return R(200, '{"accessToken": "A", "refreshToken": "R", "userId": "U"}')
        raise AssertionError(url)
    monkeypatch.setattr(c.session, "post", fake_post)

    import src.invo.mailbox as _mb
    monkeypatch.setattr(_mb, "MailboxReader", lambda *a, **k: type(
        "M", (), {"latest_otp": lambda self, **kk: "123456"})())
    c.ensure_auth(email="u@gmail.com", app_password="xxxx", device_id="dev-1")
    assert c.access_token == "A"
    assert c.refresh_token == "R"
    assert any(u.endswith("/auth/login/email/start") for u in calls)


def test_ensure_auth_needs_creds(tmp_path):
    from src.invo.client import InvoAuthError, InvoClient
    import pytest as _pt
    # isolated storage: must not pick up any real local session file
    with _pt.raises(InvoAuthError):
        InvoClient(storage_dir=str(tmp_path)).ensure_auth()


def test_password_login_is_dead():
    from src.invo.client import InvoAuthError, InvoClient
    import pytest as _pt
    with _pt.raises(InvoAuthError):
        InvoClient(email="u@gmail.com").login()


def test_session_persists_and_otp_cooldown(tmp_path):
    from src.invo.session import SessionStore
    st = SessionStore(str(tmp_path))
    assert st.load() == {}
    st.save({"access_token": "A", "refresh_token": "R"})
    assert st.load()["access_token"] == "A"
    assert st.otp_allowed() is True
    st.mark_otp()
    assert st.otp_allowed() is False
    assert st.otp_allowed(cooldown_s=0) is True


def test_activity_log_appends_and_trims(tmp_path):
    from src.invo.session import log_activity, read_activity
    for i in range(5):
        log_activity(str(tmp_path), "evt", {"i": i}, cap=3)
    rows = read_activity(str(tmp_path), limit=10)
    assert [r["detail"]["i"] for r in rows] == [2, 3, 4]


def test_conflict_precheck_is_local_only(tmp_path, monkeypatch):
    import json as _j
    import os as _o
    from src.invo.client import InvoClient
    import time as _t
    shares = {"SIG-1": {"symbol": "BTCUSDT", "portfolioId": "P",
                        "baseIds": ["b1"], "shared_at": int(_t.time())}}
    with open(_o.path.join(str(tmp_path), "invo_shares.json"), "w", encoding="utf-8") as f:
        _j.dump(shares, f)
    import src.invo.client as _mod
    monkeypatch.setattr(_mod, "load_share_ledger",
                        lambda *a, **k: shares)
    c = InvoClient()
    assert c.already_shared_open("BTCUSDT", "P", {}) is True
    assert c.already_shared_open("BTCUSDT", "P", {"SIG-1": {"final": True}}) is False
    assert c.already_shared_open("ETHUSDT", "P", {}) is False
    assert c.already_shared_open("BTCUSDT", "OTHER", {}) is False


def test_scheduler_mapping_matches_manual_test(monkeypatch, tmp_path):
    """The bot's scheduler path must build the identical payload shape as the
    manually verified live post (entry present so liquidationPrice computes)."""
    import os as _os
    from types import SimpleNamespace
    from src.scheduler import BotScheduler
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "true")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pid")
    monkeypatch.setenv("INVO_SIM_SIZE", "10.0")
    monkeypatch.chdir(tmp_path)
    seen = {}

    from src.invo import client as _mod
    orig = _mod.InvoClient.share_paper_trade

    def spy(self, signal, **kw):
        seen["signal"] = dict(signal)
        seen["kw"] = {k: v for k, v in kw.items() if k != "token"}
        return orig(self, signal, token="tok", **kw)
    monkeypatch.setattr(_mod.InvoClient, "share_paper_trade", spy)

    sched = BotScheduler.__new__(BotScheduler)
    sig = SimpleNamespace(signal_id="SIG-X", symbol="ETHUSDT", side="SHORT",
                          leverage=10, stop_loss=2600.0, tp1=2500.0, entry=2577.04)
    sched._share_paper_to_invo([sig])
    assert seen["signal"]["entry"] == 2577.04
    assert seen["kw"]["price_target"] == 2500.0
    assert seen["kw"]["entry_sim"] == 10.0
    assert seen["kw"]["dry_run"] is True


def test_precheck_stale_entries_do_not_block_forever(tmp_path, monkeypatch):
    import time as _t
    from src.invo.client import InvoClient
    now = int(_t.time())
    shares = {
        "FRESH": {"symbol": "BTCUSDT", "portfolioId": "P", "baseIds": ["b1"],
                  "shared_at": now - 3600},
        "STALE": {"symbol": "ETHUSDT", "portfolioId": "P", "baseIds": ["b2"],
                  "shared_at": now - 100000},
    }
    import src.invo.client as _mod
    monkeypatch.setattr(_mod, "load_share_ledger", lambda *a, **k: shares)
    c = InvoClient(storage_dir=str(tmp_path))
    assert c.already_shared_open("BTCUSDT", "P", {}) is True
    assert c.already_shared_open("ETHUSDT", "P", {}) is False
    assert c.already_shared_open("BTCUSDT", "P", {"FRESH": {"final": True}}) is False


def test_record_share_preserves_base_ids_on_failed_write(tmp_path, monkeypatch):
    import json as _json
    import os as _os
    from src.invo.client import load_share_ledger, record_share
    record_share(str(tmp_path), "SIG-1", {"symbol": "X", "baseIds": ["b1"]})

    def _boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(_os, "replace", _boom)
    # Must not raise; previously recorded baseIds must survive intact.
    record_share(str(tmp_path), "SIG-2", {"symbol": "Y", "baseIds": ["b2"]})
    assert load_share_ledger(str(tmp_path))["SIG-1"]["baseIds"] == ["b1"]
    assert "SIG-2" not in load_share_ledger(str(tmp_path))
    assert not _os.path.exists(
        _os.path.join(str(tmp_path), "invo_shares.json.tmp"))


def test_record_share_writes_atomically(tmp_path):
    import json as _json
    import os as _os
    from src.invo.client import load_share_ledger, record_share
    record_share(str(tmp_path), "SIG-1", {"symbol": "X", "baseIds": ["b1"]})
    record_share(str(tmp_path), "SIG-2", {"symbol": "Y", "baseIds": ["b2"]})
    ledger = load_share_ledger(str(tmp_path))
    assert ledger["SIG-1"]["baseIds"] == ["b1"]
    assert ledger["SIG-2"]["baseIds"] == ["b2"]
    assert not _os.path.exists(
        _os.path.join(str(tmp_path), "invo_shares.json.tmp"))


def test_record_share_stamps_time(tmp_path):
    from src.invo.client import load_share_ledger, record_share
    record_share(str(tmp_path), "SIG-9", {"symbol": "X", "baseIds": []})
    rec = load_share_ledger(str(tmp_path))["SIG-9"]
    assert rec["shared_at"] > 0


def test_live_share_posts_exact_path(monkeypatch):
    c = InvoClient(email="u@x.com")
    c.access_token = "tok"
    seen = {}
    def fake_post(path, payload, retry=True):
        seen["path"] = path
        seen["payload"] = payload
        return {"ok": True}
    monkeypatch.setattr(c, "_post", fake_post)
    out = c.share_position(_sig(), dry_run=False)
    assert out["dry_run"] is False
    assert seen["path"] == "/dex/position/create"
    assert seen["payload"]["coin"] == "BTC"


def test_share_and_modify_payloads_never_carry_tp2_tp3():
    import json as _json
    c = InvoClient()
    share = c.build_paper_share_payload(
        symbol="BTCUSDT", long=True, leverage=10, entry_sim=10.0,
        price_target=95000.0, stop_loss=89000.0, liquidation_price=None,
        portfolio_id="pid")
    modify = c.build_paper_modify_payload("b1", 95000.0, 89000.0)
    for payload in (share, modify):
        blob = _json.dumps(payload).lower()
        assert '"tp2"' not in blob and '"tp3"' not in blob
    assert share["priceTarget"] == 95000.0 and share["stopLoss"] == 89000.0


# ── exit-sync: TP1 + SL only (never TP2/TP3) ──────────────────────
def test_modify_payload_matches_app_faG_shape_and_tp1_only():
    c = InvoClient()
    p = c.build_paper_modify_payload("base-1", price_target=112.40, stop_loss=108.55,
                                     liquidation_price=99.9)
    assert p["baseId"] == "base-1"
    assert p["willAutoClose"] is True
    assert p["simDifference"] == 0 and p["simIncrease"] is True
    assert p["priceTarget"] == 112.40
    assert p["stopLoss"] == 108.55
    assert p["liquidationPrice"] == 99.9
    import json as _json
    blob = _json.dumps(p).lower()
    assert "tp2" not in blob and "tp3" not in blob  # TP2/TP3 do not exist here


def test_modify_dry_run_posts_nothing(monkeypatch):
    c = InvoClient()
    calls = []
    monkeypatch.setattr(c.session, "post",
                        lambda *a, **k: calls.append((a, k)) or None)
    out = c.update_paper_trade("base-1", price_target=1.0, stop_loss=2.0,
                               token="tok", dry_run=True)
    assert out["dry_run"] is True and calls == []


def test_modify_success_parses_remaining_sim(monkeypatch, tmp_path):
    c = InvoClient(storage_dir=str(tmp_path))
    seen = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["payload"] = json
        return _Resp(200, '{"success": true, "postId": "p1", '
                          '"remainingSim": 42.5, "error": null}')
    monkeypatch.setattr(c.session, "post", fake_post)
    out = c.update_paper_trade("base-1", price_target=1.0, stop_loss=2.0,
                               token="tok", dry_run=False)
    assert out["dry_run"] is False
    assert seen["url"].endswith("/investments/ticker/modify")
    assert seen["payload"]["baseId"] == "base-1"
    assert "tp2" not in str(seen["payload"]) and "tp3" not in str(seen["payload"])
    assert out["response"]["remainingSim"] == 42.5


def test_modify_failure_raises(monkeypatch, tmp_path):
    import pytest as _pt
    from src.invo.client import InvoApiError
    c = InvoClient(storage_dir=str(tmp_path))
    monkeypatch.setattr(c.session, "post", lambda *a, **k: _Resp(200, (
        '{"success": false, "postId": null, "remainingSim": null, '
        '"error": {"msg": "nope"}}'
    )))
    with _pt.raises(InvoApiError):
        c.update_paper_trade("base-1", price_target=1.0, stop_loss=2.0,
                             token="tok", dry_run=False)


def test_close_posts_sell_shape_and_parses(monkeypatch, tmp_path):
    c = InvoClient(storage_dir=str(tmp_path))
    seen = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["payload"] = json
        return _Resp(200, '{"success": true, "postId": "p2", '
                          '"closingPrice": 7.7, "remainingSim": 50.0, "error": null}')
    monkeypatch.setattr(c.session, "post", fake_post)
    out = c.close_paper_trade("base-9", token="tok", dry_run=False)
    assert seen["url"].endswith("/investments/sell")
    assert seen["payload"] == {"baseId": "base-9", "customClosingPrice": None}
    assert out["response"]["closingPrice"] == 7.7
    out2 = c.close_paper_trade("base-9", token="tok", dry_run=True)
    assert out2["dry_run"] is True


def test_list_reads_portfolio_sims_via_post(monkeypatch, tmp_path):
    # Verified live against the app's cBf flow: POST {portfolioId} ->
    # {success, portfolioRemainingSim, investments}. Read-only.
    c = InvoClient(storage_dir=str(tmp_path))
    seen = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _Resp(200, '{"success": true, "portfolioRemainingSim": 33.0, '
                          '"investments": [{"baseId": "b1"}]}')
    monkeypatch.setattr(c.session, "post", fake_post)
    monkeypatch.setattr(c.session, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not GET")))
    out = c.list_paper_investments(token="tok", portfolio_id="pid")
    assert seen["url"].endswith("/investments/get_investments_sims")
    assert seen["body"] == {"portfolioId": "pid"}
    assert out["portfolioRemainingSim"] == 33.0
    assert out["investments"] == [{"baseId": "b1"}]


# ── exit-sync planner: state-based, idempotent ────────────────────
def _plan(sid, version, sl, tp1):
    return {"signal_id": sid, "version": version, "stop_loss": sl,
            "tp1": tp1, "tp2": tp1 + 1.0, "tp3": tp1 + 2.0}


def test_sync_emits_modify_only_on_real_differences():
    from src.invo import exit_sync as _sync
    shares = {"S1": {"baseIds": ["b1"]}, "S2": {"baseIds": ["b2"]}}
    state = {"synced_levels": {"S1": {"stop_loss": 1.0, "tp1": 2.0}}, "closed": []}
    plans = [_plan("S1", 3, 1.0, 2.0),   # identical to synced -> silent
             _plan("S2", 1, 1.5, 2.5),   # new levels -> modify
             _plan("S3", 2, 9.0, 9.0)]   # no baseIds -> skipped
    actions = _sync.compute_sync_actions(plans, [], shares, state)
    assert len(actions) == 1
    a = actions[0]
    assert (a["kind"], a["signal_id"], a["base_ids"]) == ("modify", "S2", ["b2"])
    assert a["price_target"] == 2.5 and a["stop_loss"] == 1.5
    assert "tp2" not in a and "tp3" not in a


def test_sync_close_once_then_idempotent():
    from src.invo import exit_sync as _sync
    shares = {"S9": {"baseIds": ["b9"]}}
    state = {"synced_levels": {}, "closed": []}
    first = _sync.compute_sync_actions([], ["S9"], shares, state)
    assert len(first) == 1 and first[0]["kind"] == "close"
    state["closed"].append("S9")
    assert _sync.compute_sync_actions([], ["S9"], shares, state) == []


def test_sync_skips_genesis_and_baseid_less_plans():
    from src.invo import exit_sync as _sync
    shares = {"S1": {"baseIds": []}}
    actions = _sync.compute_sync_actions(
        [_plan("S1", 0, 1.0, 2.0), _plan("S2", 4, 1.0, 2.0)],
        [], shares, {"synced_levels": {}, "closed": []})
    assert actions == []


def test_sync_state_roundtrip(tmp_path):
    from src.invo import exit_sync as _sync
    blank = _sync.load_sync_state(str(tmp_path))
    assert blank == {"synced_levels": {}, "closed": []}
    blank["synced_levels"]["S1"] = {"stop_loss": 1.0, "tp1": 2.0}
    _sync.save_sync_state(str(tmp_path), blank)
    assert _sync.load_sync_state(str(tmp_path)) == blank


def test_string_shaped_errors_parse_without_crashing(monkeypatch, tmp_path):
    # Verified live 2026-10-11: ticker/modify rejects dead positions with a
    # BARE STRING error (not {"msg": ...}). Parsing must not AttributeError.
    from src.invo.client import InvoApiError, err_is_gone, err_message
    assert err_message({"msg": "boo"}) == "boo"
    assert err_message("Investment is closed") == "Investment is closed"
    assert err_is_gone("Investment is closed") is True
    assert err_is_gone("An open investment in this asset already exists.") is False
    c = InvoClient(storage_dir=str(tmp_path))
    monkeypatch.setattr(c.session, "post", lambda *a, **k: _Resp(200, (
        '{"success": false, "postId": null, "remainingSim": null, '
        '"error": "Investment is closed"}'
    )))
    import pytest as _pt
    with _pt.raises(InvoApiError) as exc:
        c.update_paper_trade("base-1", price_target=1.0, stop_loss=2.0,
                             token="tok", dry_run=False)
    assert "closed" in str(exc.value).lower()


def test_scheduler_marks_gone_positions_closed(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    import src.invo.client as _mod
    from src.invo import exit_sync as _sync
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "false")
    monkeypatch.setenv("EXITS_APPLY", "true")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pid")
    monkeypatch.setenv("INVO_EMAIL", "e@x.com")
    monkeypatch.setenv("INVO_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_mod.InvoClient, "ensure_auth", lambda self, **k: None)
    monkeypatch.setattr(_mod.InvoClient, "update_paper_trade",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            _mod.InvoApiError(200, "Investment is closed")))
    import os as _os
    import src.exits.ledger as _led
    # Mirror production layout: the scheduler uses relative "data_store",
    # which resolves under chdir to tmp_path/data_store.
    store = _os.path.join(str(tmp_path), "data_store")
    led = _led.ExitDecisionLedger(storage_dir=store)
    led._state["plans"]["S1"] = {"signal_id": "S1", "version": 1,
                                 "stop_loss": 1.0, "tp1": 2.0,
                                 "tp2": 3.0, "tp3": 4.0}
    led._save()
    import src.invo.client as _cmod
    monkeypatch.setattr(_cmod, "load_share_ledger",
                        lambda *a, **k: {"S1": {"baseIds": ["b1"]}})
    sched = BotScheduler.__new__(BotScheduler)
    sched._sync_exits_to_invo()
    state = _sync.load_sync_state(store)
    assert "S1" in state["closed"]  # stop retrying; reconciled, not failed
    # second run: no API calls at all (idempotent)
    calls = []
    monkeypatch.setattr(_mod.InvoClient, "update_paper_trade",
                        lambda self, *a, **k: calls.append(1))
    sched._sync_exits_to_invo()
    assert calls == []


def _terminal_oc(sid, status, entry_triggered=True, final=True):
    return {"signal_id": sid, "symbol": "BTCUSDT", "status": status,
            "entry_triggered": entry_triggered, "final": final}


def test_terminal_outcomes_release_without_credit(monkeypatch, tmp_path):
    """TP3_HIT / STOPPED_OUT / LIQUIDATED (entered) free their reservations
    with NO credit — local finality is not remote-settlement evidence."""
    from src.scheduler import BotScheduler
    from src.invo.sim_ledger import SimLedger
    monkeypatch.chdir(tmp_path)
    sim = SimLedger(storage_dir="data_store")
    for i, st in enumerate(("TP3_HIT", "STOPPED_OUT", "LIQUIDATED")):
        sim.reserve(f"SIG-T{i}", "BTCUSDT", 10.0)
    outcomes = {f"SIG-T{i}": _terminal_oc(f"SIG-T{i}", st)
                for i, st in enumerate(("TP3_HIT", "STOPPED_OUT", "LIQUIDATED"))}
    assert BotScheduler._release_terminal_reservations(outcomes, sim) == 3
    assert sim.snapshot()["reserved_total"] == 0.0
    # No credit fabricated: released_total stays zero.
    assert sim.snapshot()["released_total"] == 0.0


def test_expired_and_unresolved_preserved(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    from src.invo.sim_ledger import SimLedger
    monkeypatch.chdir(tmp_path)
    sim = SimLedger(storage_dir="data_store")
    sim.reserve("SIG-EXP", "BTCUSDT", 10.0)
    sim.reserve("SIG-ACT", "ETHUSDT", 5.0)
    sim.reserve("SIG-NOTRIG", "SOLUSDT", 7.0)
    outcomes = {
        # EXPIRED final: entry never triggered locally; remote may be open.
        "SIG-EXP": _terminal_oc("SIG-EXP", "EXPIRED"),
        # Non-final ACTIVE: still live on both sides.
        "SIG-ACT": _terminal_oc("SIG-ACT", "ACTIVE", final=False),
        # Terminal status but never entered: inconsistent data, preserve.
        "SIG-NOTRIG": _terminal_oc("SIG-NOTRIG", "TP3_HIT",
                                   entry_triggered=False),
    }
    assert BotScheduler._release_terminal_reservations(outcomes, sim) == 0
    assert sim.snapshot()["reserved_total"] == 22.0


def test_terminal_release_survives_malformed_records(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    from src.invo.sim_ledger import SimLedger
    monkeypatch.chdir(tmp_path)
    sim = SimLedger(storage_dir="data_store")
    sim.reserve("SIG-OK", "BTCUSDT", 10.0)
    outcomes = {
        "SIG-OK": _terminal_oc("SIG-OK", "STOPPED_OUT"),
        "SIG-NONE": None,
        "SIG-STR": "garbage",
        "SIG-NOKEYS": {"final": True},  # no status/entry fields
    }
    assert BotScheduler._release_terminal_reservations(outcomes, sim) == 1
    assert sim.snapshot()["reserved_total"] == 0.0


def test_terminal_release_idempotent(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    from src.invo.sim_ledger import SimLedger
    monkeypatch.chdir(tmp_path)
    sim = SimLedger(storage_dir="data_store")
    sim.reserve("SIG-1", "BTCUSDT", 10.0)
    outcomes = {"SIG-1": _terminal_oc("SIG-1", "TP3_HIT")}
    assert BotScheduler._release_terminal_reservations(outcomes, sim) == 1
    assert BotScheduler._release_terminal_reservations(outcomes, sim) == 0


def test_track_outcomes_wires_terminal_release(monkeypatch, tmp_path):
    """End-to-end through _track_outcomes with stubbed tracker/portfolio:
    natural terminal outcomes free the mirror; EXPIRED stays reserved."""
    from src.scheduler import BotScheduler
    from src.invo.sim_ledger import SimLedger
    import src.tracking.outcome_tracker as _otmod
    import src.papertrade.portfolio as _ppmod
    monkeypatch.chdir(tmp_path)
    sim = SimLedger(storage_dir="data_store")
    sim.reserve("SIG-WIN", "BTCUSDT", 10.0)
    sim.reserve("SIG-EXP", "ETHUSDT", 5.0)

    outcomes = {"SIG-WIN": _terminal_oc("SIG-WIN", "TP3_HIT"),
                "SIG-EXP": _terminal_oc("SIG-EXP", "EXPIRED")}

    class _StubTracker:
        def track_all(self):
            return {"finalized_this_run": 1}
        def load_ledger(self):
            return outcomes

    class _StubPortfolio:
        def settle(self, ledger):
            assert ledger is outcomes
            return {"newly_booked": 1}

    monkeypatch.setattr(_otmod, "OutcomeTracker", lambda *a, **k: _StubTracker())
    monkeypatch.setattr(_ppmod, "PaperPortfolio", lambda *a, **k: _StubPortfolio())
    sched = BotScheduler.__new__(BotScheduler)
    sched._track_outcomes()  # must not raise
    snap = SimLedger(storage_dir="data_store").snapshot()
    assert snap["reserved_total"] == 5.0  # only SIG-EXP remains


def test_scheduler_sync_gated_by_dry_run_and_apply(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    import src.invo.client as _mod
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pid")
    monkeypatch.setenv("INVO_EMAIL", "e@x.com")
    monkeypatch.setenv("INVO_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(_mod.InvoClient, "update_paper_trade",
                        lambda self, *a, **k: calls.append(1) or {"dry_run": False})
    monkeypatch.setattr(_mod.InvoClient, "close_paper_trade",
                        lambda self, *a, **k: calls.append(1) or {"dry_run": False})
    monkeypatch.setattr(_mod.InvoClient, "ensure_auth", lambda self, **k: None)
    sched = BotScheduler.__new__(BotScheduler)
    # dry-run: nothing posted even with decisions present
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "true")
    monkeypatch.setenv("EXITS_APPLY", "true")
    sched._sync_exits_to_invo()
    assert calls == []
    # apply off: nothing posted
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "false")
    monkeypatch.setenv("EXITS_APPLY", "false")
    sched._sync_exits_to_invo()
    assert calls == []


def test_share_success_records_sim_reservation(monkeypatch, tmp_path):
    """A posted share must mirror its sim commitment locally (observational).
    A failed share must leave no reservation behind."""
    from types import SimpleNamespace
    from src.scheduler import BotScheduler
    import src.invo.client as _mod
    from src.invo.sim_ledger import SimLedger
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "false")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pid")
    monkeypatch.setenv("INVO_EMAIL", "e@x.com")
    monkeypatch.setenv("INVO_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("INVO_SIM_SIZE", "10.0")
    monkeypatch.setenv("INVO_DEVICE_ID", "dev")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_mod.InvoClient, "ensure_auth", lambda self, **k: None)

    def fake_share(self, signal, **kw):
        return {"dry_run": False,
                "response": {"baseIds": ["b1"], "remainingSim": 90.0}}
    monkeypatch.setattr(_mod.InvoClient, "share_paper_trade", fake_share)
    sched = BotScheduler.__new__(BotScheduler)
    sig = SimpleNamespace(signal_id="SIG-R1", symbol="BTCUSDT", side="LONG",
                          leverage=10, stop_loss=89000.0, tp1=95000.0,
                          entry=92000.0)
    sched._share_paper_to_invo([sig])
    snap = SimLedger(storage_dir="data_store").snapshot()
    assert snap["reserved_total"] == 10.0
    assert snap["reserved_count"] == 1


def test_share_conflict_records_remote_commitment(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.scheduler import BotScheduler
    import src.invo.client as _mod
    from src.invo.client import InvoConflictError
    from src.invo.sim_ledger import SimLedger
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "false")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pid")
    monkeypatch.setenv("INVO_EMAIL", "e@x.com")
    monkeypatch.setenv("INVO_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("INVO_SIM_SIZE", "10.0")
    monkeypatch.setenv("INVO_DEVICE_ID", "dev")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_mod.InvoClient, "ensure_auth", lambda self, **k: None)

    def fake_conflict(self, signal, **kw):
        raise InvoConflictError(["b9"], {})
    monkeypatch.setattr(_mod.InvoClient, "share_paper_trade", fake_conflict)
    sched = BotScheduler.__new__(BotScheduler)
    sig = SimpleNamespace(signal_id="SIG-C1", symbol="ETHUSDT", side="LONG",
                          leverage=10, stop_loss=2500.0, tp1=2600.0,
                          entry=2550.0)
    sched._share_paper_to_invo([sig])  # must not raise
    snap = SimLedger(storage_dir="data_store").snapshot()
    assert snap["reserved_total"] == 10.0  # committed remotely, mirrored


def test_audit_event_carries_prev_levels(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    from src.invo.sync_audit import read_audit
    monkeypatch.chdir(tmp_path)
    BotScheduler._audit("SIG-P", "modify", "confirmed",
                        {"proceed": True, "idempotency_key": "key1234567890",
                         "attempt": 2},
                        "worker-1", 1234567890000,
                        confirmer="authoritative-response",
                        action={"price_target": 101.0, "stop_loss": 96.0,
                                "plan_version": 3},
                        prev={"tp1": 100.0, "stop_loss": 95.0})
    events, gaps, _ = read_audit("data_store")
    assert gaps == []
    assert len(events) == 1
    ev = events[0]
    assert ev["prev_tp1"] == 100.0 and ev["prev_sl"] == 95.0
    assert ev["desired_tp1"] == 101.0 and ev["desired_sl"] == 96.0
    assert ev["attempt"] == 2 and ev["plan_version"] == 3
    assert ev["confirmation"] == "authoritative-response"
    assert ev["correlation_id"].startswith("SIG-P:modify:")


class _FakeSyncClient:
    def __init__(self, live_base_ids=("b1",)):
        self.updates = []
        self.closes = []
        self.lists = 0
        self._live = set(live_base_ids)
        self.fail_lists = False

    def update_paper_trade(self, base_id, price_target=None, stop_loss=None,
                           token="", dry_run=True, **kw):
        self.updates.append({"base_id": base_id, "price_target": price_target,
                             "stop_loss": stop_loss})
        return {"dry_run": False, "response": {"success": True}}

    def close_paper_trade(self, base_id, token="", dry_run=True, **kw):
        self.closes.append(base_id)
        return {"dry_run": False, "response": {"success": True}}

    def list_paper_investments(self, token, portfolio_id):
        self.lists += 1
        if self.fail_lists:
            raise ConnectionError("feed down")
        return {"investments": [{"baseId": b} for b in sorted(self._live)]}


def _seed_sync_close(tmp_path, sid="SIG-C1", base_ids=("b9",)):
    """Applied supervisor-close event + share record."""
    import json as _json
    import os as _os
    ds = _os.path.join(str(tmp_path), "data_store")
    _os.makedirs(ds, exist_ok=True)
    with open(_os.path.join(ds, "exit_decisions.json"), "w", encoding="utf-8") as f:
        _json.dump({"version": 1, "decisions": [], "plans": {},
                    "events": {sid: [{"kind": "close",
                                      "decision_key": "k1",
                                      "data_ts_ms": 1,
                                      "decided_at_ms": 2}]},
                    "invalidated": [], "last_eval": {}}, f)
    with open(_os.path.join(ds, "invo_shares.json"), "w", encoding="utf-8") as f:
        _json.dump({sid: {"baseIds": list(base_ids), "symbol": "BTCUSDT",
                          "side": "LONG", "portfolioId": "pid"}}, f)


def test_close_absent_remotely_never_reposts(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    monkeypatch.chdir(tmp_path)
    _seed_sync_close(tmp_path)
    sched = BotScheduler.__new__(BotScheduler)
    client = _FakeSyncClient(live_base_ids=())  # b9 already gone server-side
    sched._run_exit_sync_locked(client, "tok", "pid", "worker-1")
    assert client.closes == []
    # Exactly two bounded reads: one close-presence check + the per-run
    # reconcile heartbeat. Both are capped (no per-position reads).
    assert client.lists == 2
    from src.invo import exit_sync as _sync
    assert "SIG-C1" in _sync.load_sync_state("data_store")["closed"]
    from src.invo.sync_audit import read_audit
    events, _, _ = read_audit("data_store")
    rec = [e for e in events if e["signal_id"] == "SIG-C1"]
    assert rec and rec[0]["confirmation"] == "remote-readback"


def test_close_present_remotely_sells_once(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    monkeypatch.chdir(tmp_path)
    _seed_sync_close(tmp_path)
    sched = BotScheduler.__new__(BotScheduler)
    client = _FakeSyncClient(live_base_ids=("b9",))
    sched._run_exit_sync_locked(client, "tok", "pid", "worker-1")
    assert client.closes == ["b9"]
    assert client.lists == 2  # close-presence check + reconcile heartbeat


def test_close_list_unavailable_preserves_existing_behavior(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    monkeypatch.chdir(tmp_path)
    _seed_sync_close(tmp_path)
    sched = BotScheduler.__new__(BotScheduler)
    client = _FakeSyncClient(live_base_ids=("b9",))
    client.fail_lists = True
    sched._run_exit_sync_locked(client, "tok", "pid", "worker-1")
    assert client.closes == ["b9"]  # ambiguous read -> proceed, never assume gone


def _seed_sync_state(tmp_path):
    """Exit plan v2 + share record, as the live scheduler would see them."""
    import json as _json
    import os as _os
    ds = _os.path.join(str(tmp_path), "data_store")
    _os.makedirs(ds, exist_ok=True)
    with open(_os.path.join(ds, "exit_decisions.json"), "w", encoding="utf-8") as f:
        _json.dump({"version": 1, "decisions": [], "plans": {
            "SIG-S1": {"signal_id": "SIG-S1", "version": 2,
                       "stop_loss": 96.0, "tp1": 101.0,
                       "tp2": 102.0, "tp3": 103.0,
                       "decided_at_ms": 1000, "basis_decision_key": "k"}},
            "events": {}, "invalidated": [], "last_eval": {}}, f)
    with open(_os.path.join(ds, "invo_shares.json"), "w", encoding="utf-8") as f:
        _json.dump({"SIG-S1": {"baseIds": ["b1"], "symbol": "BTCUSDT",
                               "side": "LONG", "portfolioId": "pid"}}, f)


def test_429_retry_after_honored_end_to_end(monkeypatch, tmp_path):
    import requests
    from src.scheduler import BotScheduler
    from src.invo import exit_sync as _sync
    monkeypatch.chdir(tmp_path)
    _seed_sync_state(tmp_path)
    sched = BotScheduler.__new__(BotScheduler)

    def _raise_429(self, base_id, price_target=None, stop_loss=None,
                   token="", dry_run=True, **kw):
        resp = requests.Response()
        resp.status_code = 429
        resp.headers["Retry-After"] = "120"
        raise requests.exceptions.HTTPError("429", response=resp)

    monkeypatch.setattr(_FakeSyncClient, "update_paper_trade", _raise_429)
    client = _FakeSyncClient()
    sched._run_exit_sync_locked(client, "tok", "pid", "worker-1")
    state = _sync.load_sync_state("data_store")
    rec = state["records"]["SIG-S1:modify"]
    assert rec["status"] == "retryable"
    # Server demanded 120s although computed backoff would be ~60-89s.
    import time as _time
    remaining_ms = rec["next_retry_at_ms"] - int(_time.time() * 1000)
    assert 110_000 <= remaining_ms <= 125_000


def test_locked_sync_applies_and_is_idempotent(monkeypatch, tmp_path):
    from src.scheduler import BotScheduler
    from src.invo import exit_sync as _sync
    from src.invo.sync_audit import read_audit
    monkeypatch.chdir(tmp_path)
    _seed_sync_state(tmp_path)
    sched = BotScheduler.__new__(BotScheduler)
    client = _FakeSyncClient()
    sched._run_exit_sync_locked(client, "tok", "pid", "worker-1")
    assert client.updates == [{"base_id": "b1", "price_target": 101.0,
                               "stop_loss": 96.0}]
    assert client.closes == []
    state = _sync.load_sync_state("data_store")
    assert state["synced_levels"]["SIG-S1"] == {"stop_loss": 96.0, "tp1": 101.0}
    events, gaps, _ = read_audit("data_store")
    assert gaps == []
    assert any(e["event"] == "confirmed" and e["signal_id"] == "SIG-S1"
               for e in events)
    # Second run: levels unchanged -> zero POSTs (idempotent).
    client2 = _FakeSyncClient()
    sched._run_exit_sync_locked(client2, "tok", "pid", "worker-1")
    assert client2.updates == [] and client2.closes == []


def test_refresh_sends_refresh_token_as_bearer(monkeypatch, tmp_path):
    """Rotation must authenticate with the refresh token, not the (expired)
    access token — otherwise every rotation after access-expiry fails and
    the session dies until the next OTP login."""
    from src.invo.client import InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    c.access_token = "STALE-ACCESS"
    c.refresh_token = "GOOD-REFRESH"
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["auth"] = (headers or {}).get("Authorization")
        return _Resp(200, '{"accessToken": "FRESH-ACCESS"}')

    monkeypatch.setattr(c.session, "get", fake_get)
    assert c._refresh() is True
    assert seen["auth"] == "Bearer GOOD-REFRESH"
    assert c.access_token == "FRESH-ACCESS"


def test_refresh_without_refresh_token_posts_nothing(monkeypatch, tmp_path):
    from src.invo.client import InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    c.access_token = "STALE-ACCESS"
    c.refresh_token = None
    calls = []
    monkeypatch.setattr(c.session, "get",
                        lambda *a, **k: calls.append(1) or _Resp(200, "{}"))
    assert c._refresh() is False
    assert calls == []


def test_fetch_position_history_returns_newest_item(monkeypatch, tmp_path):
    from src.invo.client import InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    c.access_token = "TOK"
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _Resp(200, '{"success": true, "items": ['
                          '{"baseId": "b1", "ticker": "ZEC", "priceTarget": 1.0},'
                          '{"baseId": "b1", "ticker": "ZEC", "priceTarget": 2.0}]}')

    monkeypatch.setattr(c.session, "post", fake_post)
    item = c.fetch_position_history("b1", "TOK")
    assert seen["url"].endswith("/posts/get_investment_history")
    assert seen["body"]["investmentBaseId"] == "b1"
    assert item == {"baseId": "b1", "ticker": "ZEC", "priceTarget": 1.0}


def test_fetch_position_history_empty_items_returns_empty(monkeypatch, tmp_path):
    from src.invo.client import InvoClient
    c = InvoClient(storage_dir=str(tmp_path))
    c.access_token = "TOK"
    monkeypatch.setattr(c.session, "post",
                        lambda *a, **k: _Resp(200, '{"success": true, "items": []}'))
    assert c.fetch_position_history("b9", "TOK") == {}


def _share_sig(sid="SIG-NEW", symbol="DOGEUSDT", side="SHORT"):
    from types import SimpleNamespace
    return SimpleNamespace(signal_id=sid, symbol=symbol, side=side,
                           leverage=10, stop_loss=0.08, entry=0.086,
                           tp1=0.084)


class _FakeShareClient(InvoClient):
    """Fake InvoClient for the share-guard tests (no network).

    Subclasses the real client so already_shared_open keeps its true
    ledger logic; only transport/auth methods are stubbed.
    """

    def __init__(self, investments=None, fail_snapshot=False):
        self.investments = investments if investments is not None else []
        self.fail_snapshot = fail_snapshot
        self.shared = []
        self.access_token = "tok"

    def ensure_auth(self, **kwargs):
        return True

    def list_paper_investments(self, token, portfolio_id):
        if self.fail_snapshot:
            raise RuntimeError("sims down")
        return {"portfolioRemainingSim": 100.0,
                "investments": [{"baseId": f"b-{i}", "ticker": t}
                                for i, t in enumerate(self.investments)]}

    def share_paper_trade(self, signal, token, portfolio_id, entry_sim,
                          price_target, dry_run):
        assert dry_run is False
        self.shared.append(signal["symbol"])
        return {"dry_run": False,
                "response": {"baseIds": ["nb-1"], "remainingSim": 95.0}}


def _share_env(monkeypatch):
    monkeypatch.setenv("INVO_PAPER_ENABLED", "true")
    monkeypatch.setenv("INVO_PAPER_DRY_RUN", "false")
    monkeypatch.setenv("INVO_EMAIL", "e")
    monkeypatch.setenv("INVO_GMAIL_APP_PASSWORD", "p")
    monkeypatch.setenv("INVO_PORTFOLIO_ID", "pf")
    monkeypatch.setenv("INVO_DEVICE_ID", "d")
    monkeypatch.setenv("INVO_SIM_SIZE", "5.0")


def _seed_prior_share(tmp_path, final):
    import json as _json
    import os as _os
    import time as _time
    from src.invo.client import record_share
    _os.makedirs(tmp_path / "data_store", exist_ok=True)
    with open(tmp_path / "data_store" / "outcomes.json", "w",
              encoding="utf-8") as f:
        _json.dump({"SIG-OLD": {"final": final}}, f)
    record_share(str(tmp_path / "data_store"), "SIG-OLD",
                 {"symbol": "DOGEUSDT", "side": "LONG", "portfolioId": "pf",
                  "baseIds": ["b-old"],
                  "shared_at": int(_time.time())})


def _run_share(monkeypatch, tmp_path, client, signals):
    from src.scheduler import BotScheduler
    from src.invo import client as _client_mod
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_client_mod, "InvoClient",
                        lambda *a, **k: client)
    sched = BotScheduler.__new__(BotScheduler)
    sched._share_paper_to_invo(signals)


def test_share_guard_shares_when_invo_empty_despite_open_outcome(
        monkeypatch, tmp_path):
    """The DOGE #8 fix: a non-final outcome must not block a share when the
    Invo slot is actually empty (externally closed position)."""
    _share_env(monkeypatch)
    _seed_prior_share(tmp_path, final=False)
    client = _FakeShareClient(investments=[])
    _run_share(monkeypatch, tmp_path, client, [_share_sig()])
    assert client.shared == ["DOGEUSDT"]


def test_share_guard_skips_coin_open_in_invo(monkeypatch, tmp_path):
    """Ground truth wins both ways: Invo-open coin is skipped even when the
    old outcome is final (server would conflict)."""
    _share_env(monkeypatch)
    _seed_prior_share(tmp_path, final=True)
    client = _FakeShareClient(investments=["DOGE"])
    _run_share(monkeypatch, tmp_path, client, [_share_sig()])
    assert client.shared == []


def test_share_guard_legacy_when_snapshot_fails_open_outcome(
        monkeypatch, tmp_path):
    """Unreadable presence -> legacy ledger screen (safe direction)."""
    _share_env(monkeypatch)
    _seed_prior_share(tmp_path, final=False)
    client = _FakeShareClient(fail_snapshot=True)
    _run_share(monkeypatch, tmp_path, client, [_share_sig()])
    assert client.shared == []


def test_share_guard_legacy_when_snapshot_fails_final_outcome(
        monkeypatch, tmp_path):
    _share_env(monkeypatch)
    _seed_prior_share(tmp_path, final=True)
    client = _FakeShareClient(fail_snapshot=True)
    _run_share(monkeypatch, tmp_path, client, [_share_sig()])
    assert client.shared == ["DOGEUSDT"]


def test_share_guard_same_cycle_dedup(monkeypatch, tmp_path):
    """Two same-coin signals in one cycle -> exactly one POST."""
    _share_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    client = _FakeShareClient(investments=[])
    _run_share(monkeypatch, tmp_path, client,
               [_share_sig("SIG-A"), _share_sig("SIG-B")])
    assert client.shared == ["DOGEUSDT"]


def test_ticker_to_symbol_mapping():
    from src.scheduler import _ticker_to_symbol
    assert _ticker_to_symbol("ZEC") == "ZECUSDT"
    assert _ticker_to_symbol("zec") == "ZECUSDT"
    assert _ticker_to_symbol("BTCUSDT") == "BTCUSDT"
    assert _ticker_to_symbol("") == ""
