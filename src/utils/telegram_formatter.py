from src.models.signal import SignalModel

class TelegramFormatter:
    @staticmethod
    def format_signal(signal: SignalModel) -> str:
        """
        Formats a validated signal into a Telegram Markdown string.
        """
        emoji = "🟢" if signal.side == "LONG" else "🔴"
        
        conditions = "\n".join([f"- {c}" for c in signal.conditions])
        invalidation = "\n".join([f"- {i}" for i in signal.invalidation_conditions])
        management = "\n".join([f"- {m}" for m in signal.management_rules])

        msg = f"""
🚀 **SIGNAL ALERT** 🚀
{emoji} **{signal.symbol} - {signal.side}**
ID: `{signal.signal_id}`
Confidence: {signal.confidence}/100

**Entry & Risk:**
Entry: `{signal.entry:.4f}`
Stop Loss: `{signal.stop_loss:.4f}`
Rec. Leverage: {signal.leverage}x

**Targets:**
TP1: `{signal.tp1:.4f}` (R:R {signal.risk_reward_tp1}x)
TP2: `{signal.tp2:.4f}` (R:R {signal.risk_reward_tp2}x)
TP3: `{signal.tp3:.4f}` (R:R {signal.risk_reward_tp3}x)

**Conditions:**
{conditions}

**Invalidation:**
{invalidation}

**Management:**
{management}
"""
        return msg.strip()
