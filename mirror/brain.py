"""Capital brain: flat base size plus hard guards. Pure logic, no I/O.

Contract (agreed defaults: base 5.0 sim, max 10 open, 25% bank cap):
- Unknown/empty bank -> SKIP (never trade blind).
- At/over max open positions -> SKIP.
- One trade may not exceed bank * max_risk_pct. If the base size fits,
  use it (full mimic). If not, shrink to fit (partial mimic) so the
  mirror still follows Pariston instead of dropping the trade.
- Zero/negative fittable size -> SKIP.
"""
from typing import Optional, Tuple


def decide_size(
    bank: Optional[float],
    open_count: Optional[int],
    base_size: float = 5.0,
    max_open: int = 10,
    max_risk_pct: float = 0.25,
) -> Tuple[Optional[float], str]:
    """Return (size_sim | None, reason). None means skip this trade."""
    if bank is None:
        return None, "skip:bank-unknown"
    if bank <= 0:
        return None, "skip:bank-empty"
    if open_count is None:
        open_count = 0
    if open_count >= max_open:
        return None, f"skip:max-open-{open_count}/{max_open}"
    cap = bank * max_risk_pct
    if cap <= 0:
        return None, "skip:risk-cap-zero"
    if base_size <= cap:
        return round(base_size, 2), "full-mimic"
    size = round(cap, 2)
    if size <= 0:
        return None, "skip:risk-cap-zero"
    return size, f"shrunk-to-fit:{size}<{base_size}"
