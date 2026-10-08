"""Tests for the paper-trading portfolio (virtual fills)."""
import json
import os

from src.papertrade.portfolio import PaperPortfolio


def _oc(sid, final=True, net_r=1.5, symbol="BTCUSDT", side="LONG"):
    return {
        "signal_id": sid,
        "symbol": symbol,
        "side": side,
        "strategy_id": "donchian_20",
        "signal_timestamp": "2026-10-01T00:00:00Z",
        "entry_triggered": True,
        "first_event": "TP1",
        "status": "TP1_HIT",
        "net_realized_r": net_r,
        "risk_amount": 10.0,
        "final": final,
    }


def test_settle_books_final_once_and_updates_equity(tmp_path):
    pp = PaperPortfolio(storage_dir=str(tmp_path))
    out = pp.settle({"A": _oc("A", net_r=2.0), "B": _oc("B", net_r=-1.0)})
    assert out["newly_booked"] == 2
    assert out["equity"] == 1000.0 + 20.0 - 10.0
    # idempotent re-run
    out2 = pp.settle({"A": _oc("A", net_r=2.0), "B": _oc("B", net_r=-1.0)})
    assert out2["newly_booked"] == 0
    assert out2["equity"] == 1010.0
    with open(os.path.join(str(tmp_path), "paper_portfolio.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    assert set(ledger["booked"]) == {"A", "B"}


def test_open_outcomes_are_not_booked(tmp_path):
    pp = PaperPortfolio(storage_dir=str(tmp_path))
    out = pp.settle({"C": _oc("C", final=False)})
    assert out["newly_booked"] == 0
    assert out["equity"] == 1000.0


def test_summary_math(tmp_path):
    pp = PaperPortfolio(storage_dir=str(tmp_path))
    pp.settle({
        "A": _oc("A", net_r=2.0),
        "B": _oc("B", net_r=-1.0),
        "D": _oc("D", net_r=0.5),
    })
    s = pp.summary()
    assert s["trades_settled"] == 3
    assert s["wins"] == 2
    assert s["win_rate"] == round(2 / 3, 3)
    assert s["total_pnl"] == 15.0
    assert s["equity"] == 1015.0
    assert len(s["equity_curve"]) == 3
    assert s["equity_curve"][-1]["cum_pnl"] == 15.0
