"""Pariston auto-mimic bot: one job — mirror every Pariston trade.

Watches Pariston's paper portfolio (feed + positions, any login can read)
and mirrors opens, adds, TP/SL moves and closes onto the operator's
Hyperliquid account via a trade-only agent key (cannot withdraw).

Modes (MIRROR_MODE): `watch` (detect-only notifier) and `auto`
(full loop: detect -> capital brain -> HL execution -> Invo recording).
Live money moves only with MIMIC_LIVE=true; default is dry-run.
"""
