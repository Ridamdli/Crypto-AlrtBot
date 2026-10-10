"""One-shot: bot scheduler hook -> live paper share (user-authorized test).
Uses Railway env (production path exactly). Synthetic SOL LONG signal.
"""
from types import SimpleNamespace

from src.models.signal import SignalModel
from src.scheduler import BotScheduler


def main():
    sig = SignalModel(
        signal_id="SIG-TEST-SCHED-2",
        timestamp="2026-10-10T12:00:00Z",
        symbol="SOLUSDT",
        side="LONG",
        strategy_id="donchian_20",
        entry=110.20,
        tp1=112.40,
        tp2=113.50,
        tp3=114.60,
        stop_loss=108.55,
        leverage=5,
        margin=20.0,
        position_notional=100.0,
        risk_amount=1.65,
        risk_reward_tp1=1.33,
        risk_reward_tp2=2.0,
        risk_reward_tp3=2.67,
        confidence=75,
        conditions=["test"],
        invalidation_conditions=["test"],
        management_rules=["test"],
    )
    sched = BotScheduler.__new__(BotScheduler)
    sched._share_paper_to_invo([sig])
    print("HOOK DONE")


if __name__ == "__main__":
    main()
