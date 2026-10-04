from src.models.signal import SignalModel

class TelegramFormatter:
    @staticmethod
    def format_signal(signal: SignalModel, rank: int = 1) -> str:
        """
        Formats a validated signal into the exact PRD Telegram template.
        """
        side_emoji = "🟢" if signal.side == "LONG" else "🔴"

        conditions_str = "\n".join([f"✅ {c}" for c in signal.conditions])
        invalidation_str = "\n".join([f"❌ {i}" for i in signal.invalidation_conditions])
        management_str = "\n".join([f"→ {m}" for m in signal.management_rules])

        msg = (
            f"🚀 #{rank} FUTURES SIGNAL\n"
            f"\n"
            f"{signal.symbol}\n"
            f"{side_emoji} {signal.side}\n"
            f"\n"
            f"ENTRY\n{signal.entry:.6g}\n"
            f"\n"
            f"TP1\n{signal.tp1:.6g}\n"
            f"TP2\n{signal.tp2:.6g}\n"
            f"TP3\n{signal.tp3:.6g}\n"
            f"\n"
            f"SL\n{signal.stop_loss:.6g}\n"
            f"\n"
            f"LEVERAGE\n{signal.leverage}x\n"
            f"\n"
            f"MARGIN\n${signal.margin:.2f}\n"
            f"\n"
            f"RISK\n${signal.risk_amount:.2f}\n"
            f"\n"
            f"R:R\n{signal.risk_reward_tp1}R / {signal.risk_reward_tp2}R / {signal.risk_reward_tp3}R\n"
            f"\n"
            f"CONFIDENCE\n{signal.confidence}/100\n"
            f"\n"
            f"CONDITIONS\n{conditions_str}\n"
            f"\n"
            f"INVALIDATION\n{invalidation_str}\n"
            f"\n"
            f"MANAGEMENT\n{management_str}\n"
            f"\n"
            f"ID: `{signal.signal_id}`"
        )
        return msg
