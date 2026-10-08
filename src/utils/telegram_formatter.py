from src.models.signal import SignalModel

# Telegram legacy-Markdown special chars that must be escaped in dynamic text.
# Without this, values like strategy ids (ema_trend_20_50) break entity parsing
# and the API rejects the whole message with 400 Bad Request.
# Narrow set on purpose: over-escaping (e.g. dots) renders stray backslashes.
_MD_SPECIAL = ("_", "*", "[", "]", "`")


def escape_markdown(text) -> str:
    """Escape Telegram legacy-Markdown special chars in dynamic content."""
    s = str(text)
    for ch in _MD_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    return s


def _account_bank() -> float:
    """Capital the position is sized against (risk engine account size)."""
    try:
        from src.config import CONFIG
        return float(CONFIG.risk.account_size)
    except Exception:
        return 1000.0


def _size_block(signal) -> str:
    """Position size as % of capital (replaces raw margin display)."""
    try:
        notional = float(signal.position_notional)
    except (TypeError, ValueError, AttributeError):
        try:
            return f"MARGIN\n${float(signal.margin):.2f}\n\n"
        except (TypeError, ValueError, AttributeError):
            return ""
    bank = _account_bank()
    pct = notional / bank * 100 if bank else 0.0
    return f"SIZE\n${notional:.2f} ({pct:.1f}% of bank)\n\n"


def _liq_block(signal) -> str:
    """Estimated isolated-margin liquidation price for the position."""
    try:
        from src.papertrade.liquidation import estimate_liq_price, liq_distance_pct
        liq = estimate_liq_price(float(signal.entry), signal.side, float(signal.leverage))
    except (TypeError, ValueError, AttributeError):
        return ""
    if liq is None:
        return ""
    try:
        dist = liq_distance_pct(float(signal.entry), liq, signal.side)
        dist_str = f" (~{dist:.1f}% adverse)" if dist is not None else ""
    except (TypeError, ValueError):
        dist_str = ""
    return f"LIQ (est)\n{liq:.6g}{dist_str}\n\n"


class TelegramFormatter:
    @staticmethod
    def format_signal(signal: SignalModel, rank: int = 1) -> str:
        """
        Formats a validated signal into the exact PRD Telegram template.
        Now includes strategy information.
        All dynamic content is Markdown-escaped so the API never 400s.
        """
        side_emoji = "🟢" if signal.side == "LONG" else "🔴"

        conditions_str = "\n".join([f"✅ {escape_markdown(c)}" for c in signal.conditions])
        invalidation_str = "\n".join([f"❌ {escape_markdown(i)}" for i in signal.invalidation_conditions])
        management_str = "\n".join([f"→ {escape_markdown(m)}" for m in signal.management_rules])

        # Strategy info
        strategy_info = ""
        if hasattr(signal, 'strategy_id') and signal.strategy_id:
            strategy_info = f"\nSTRATEGY\n{escape_markdown(signal.strategy_id)} (v{escape_markdown(signal.strategy_version)})"
        if hasattr(signal, 'strategy_metadata') and signal.strategy_metadata:
            meta = signal.strategy_metadata
            if isinstance(meta, dict) and meta:
                meta_str = ", ".join(f"{escape_markdown(k)}: {escape_markdown(v)}" for k, v in meta.items())
                strategy_info += f"\nSTRATEGY META\n{meta_str}"

        conflict_info = ""
        if hasattr(signal, 'conflict_resolved') and signal.conflict_resolved:
            conflict_info = f"\n⚠️ CONFLICT RESOLVED\n{escape_markdown(signal.conflict_details)}"

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
            f"{_size_block(signal)}"
            f"{_liq_block(signal)}"
            f"RISK\n${signal.risk_amount:.2f}\n"
            f"\n"
            f"R:R\n{signal.risk_reward_tp1}R / {signal.risk_reward_tp2}R / {signal.risk_reward_tp3}R\n"
            f"\n"
            f"CONFIDENCE\n{signal.confidence}/100\n"
            f"{strategy_info}"
            f"{conflict_info}"
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

    @staticmethod
    def format_conflict_signal(signals: list, rank: int = 1) -> str:
        """Format a conflict signal showing both sides."""
        if not signals:
            return ""
        
        symbol = signals[0].get("symbol", "UNKNOWN")
        sides = [s.get("side", "?") for s in signals]
        long_count = sides.count("LONG")
        short_count = sides.count("SHORT")
        
        msg = (
            f"⚠️ #{rank} CONFLICT SIGNAL\n"
            f"\n"
            f"{symbol}\n"
            f"LONG: {long_count} | SHORT: {short_count}\n"
            f"\n"
            f"Conflicting strategies detected:\n"
        )
        
        for s in signals:
            strategy = escape_markdown(s.get("strategy_id", "unknown"))
            side = escape_markdown(s.get("side", "?"))
            conf = escape_markdown(s.get("confidence", 0))
            msg += f"  • {strategy}: {side} (conf: {conf})\n"
        
        msg += (
            f"\n"
            f"No clear consensus. Manual review required.\n"
            f"\n"
            f"ID: `CONFLICT-{signals[0].get('signal_id', 'unknown')}`"
        )
        return msg
