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
