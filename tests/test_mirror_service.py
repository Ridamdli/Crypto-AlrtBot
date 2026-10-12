"""Mirror cycle tests with fake portfolio watch + fake client. No network."""
from mirror.config import MirrorConfig
from mirror.service import MirrorBot
from mirror.store import MirrorStore

SRC_ZEC = "c7e2295e-e2fd-42db-9c2b-fb08ed2ec459"
SRC_NEAR = "8967c3a3-87c3-4e89-89e5-28e4657b5ec8"


def _pos(sid=SRC_ZEC, ticker="ZEC", side="SHORT"):
    return {"baseId": sid, "ticker": ticker, "side": side, "leverage": 10,
            "entry": 1293.8, "entrySim": 5}


def _setup(symbol="ZECUSDT", side="SHORT"):
    return {"symbol": symbol, "side": side, "leverage": 10,
            "entry": 1293.8, "stop_loss": 1306.01, "tp1": 1267.50}


class FakeClient:
    def __init__(self, bank=100.0, mirror_opens=0):
        self.bank = bank
        self.mirror_opens = mirror_opens
        self.shared = []
        self.closed = []
        self.access_token = "tok"
        self.conflict_tickers = set()

    def ensure_auth(self, **kwargs):
        return True

    def list_paper_investments(self, token, portfolio_id):
        return {"portfolioRemainingSim": self.bank,
                "investments": [{"id": i} for i in range(self.mirror_opens)]}

    def share_paper_trade(self, signal, token, portfolio_id, entry_sim,
                          price_target, dry_run):
        assert dry_run is False
        if signal["symbol"] in self.conflict_tickers:
            from src.invo.client import InvoConflictError
            raise InvoConflictError(["base-x"], {"success": False})
        self.shared.append({"symbol": signal["symbol"], "size": entry_sim,
                            "tp": price_target, "sl": signal.get("stop_loss")})
        return {"dry_run": False,
                "response": {"baseIds": [f"m-{signal['symbol']}"],
                             "remainingSim": self.bank - entry_sim}}

    def close_paper_trade(self, base_id, token, dry_run):
        assert dry_run is False
        self.closed.append(base_id)
        return {"dry_run": False, "response": {"closingPrice": 1.0}}


def _bot(tmp_path, client, positions, dry_run=False):
    cfg = MirrorConfig(email="e", app_password="p", portfolio_id="pf",
                       device_id="d", data_dir=str(tmp_path),
                       dry_run=dry_run)
    return MirrorBot(cfg,
                     fetch_positions_fn=lambda *a: positions,
                     fetch_detail_fn=lambda *a, **k: _setup(),
                     client_factory=lambda: client)


def test_cycle_copies_new_setup_with_tp_sl(tmp_path):
    client = FakeClient()
    bot = _bot(tmp_path, client, [_pos()])
    out = bot.run_once()
    assert out["mirrored"] == 1 and out["errors"] == 0
    assert client.shared == [{"symbol": "ZECUSDT", "size": 5.0,
                              "tp": 1267.50, "sl": 1306.01}]
    assert set(MirrorStore(str(tmp_path)).open_positions()) == {SRC_ZEC}
    out2 = bot.run_once()
    assert out2["mirrored"] == 0 and len(client.shared) == 1


def test_cycle_closes_what_pariston_closed(tmp_path):
    client = FakeClient()
    bot = _bot(tmp_path, client, [_pos()])
    bot.run_once()
    bot2 = _bot(tmp_path, client, [_pos(SRC_NEAR, "NEAR")])
    bot2._fetch_detail = lambda *a, **k: dict(_setup(), symbol="NEARUSDT")
    out = bot2.run_once()
    assert out["closed"] == 1
    assert client.closed == ["m-ZECUSDT"]
    assert set(MirrorStore(str(tmp_path)).open_positions()) == {SRC_NEAR}


def test_cycle_shrinks_when_bank_tight(tmp_path):
    client = FakeClient(bank=10.0)
    bot = _bot(tmp_path, client, [_pos()])
    out = bot.run_once()
    assert out["mirrored"] == 1
    assert client.shared[0]["size"] == 2.5


def test_cycle_skips_at_max_open(tmp_path):
    client = FakeClient(mirror_opens=10)
    bot = _bot(tmp_path, client, [_pos()])
    out = bot.run_once()
    assert out["mirrored"] == 0 and out["skipped"] == 1
    assert client.shared == []


def test_dry_run_posts_nothing_but_marks_seen(tmp_path):
    client = FakeClient()
    bot = _bot(tmp_path, client, [_pos()], dry_run=True)
    out = bot.run_once()
    assert out["mirrored"] == 1 and client.shared == [] and client.closed == []
    assert MirrorStore(str(tmp_path)).open_positions() == {}
    assert SRC_ZEC in MirrorStore(str(tmp_path)).seen_ids()


def test_conflict_adopted_not_error(tmp_path):
    client = FakeClient()
    client.conflict_tickers.add("ZECUSDT")
    bot = _bot(tmp_path, client, [_pos()])
    out = bot.run_once()
    assert out["errors"] == 0 and client.shared == []
    pos = MirrorStore(str(tmp_path)).open_positions()
    assert pos[SRC_ZEC]["already_open"] is True
    assert pos[SRC_ZEC]["mirrorBaseIds"] == ["base-x"]


def test_auth_failure_contained(tmp_path):
    cfg = MirrorConfig(data_dir=str(tmp_path), dry_run=True)

    class _Bad:
        def ensure_auth(self, **k):
            raise RuntimeError("nope")

    bot = MirrorBot(cfg, client_factory=lambda: _Bad())
    out = bot.run_once()
    assert out["errors"] == 1 and out["mirrored"] == 0
