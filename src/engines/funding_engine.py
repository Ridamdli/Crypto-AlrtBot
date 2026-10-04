from src.utils.logger import get_logger
from src.config import CONFIG

logger = get_logger(__name__)

class FundingEngine:
    """
    Uses funding rate as contextual signal per PRD §16.
    Penalises a LONG when funding is extremely positive (longs overpaying)
    and a SHORT when funding is extremely negative.
    Returns a confidence penalty (0 = no penalty).
    """
    def __init__(self):
        self.cfg = CONFIG.funding

    def evaluate(self, funding_rate: float, side: str) -> dict:
        penalty = 0
        note = "normal"

        if side == "LONG" and funding_rate >= self.cfg.extreme_positive_threshold:
            penalty = self.cfg.confidence_penalty
            note = f"extreme positive funding ({funding_rate:.4%}) penalises LONG"
        elif side == "SHORT" and funding_rate <= self.cfg.extreme_negative_threshold:
            penalty = self.cfg.confidence_penalty
            note = f"extreme negative funding ({funding_rate:.4%}) penalises SHORT"

        logger.debug(f"Funding eval: rate={funding_rate:.4%} side={side} penalty={penalty}")
        return {"funding_rate": funding_rate, "penalty": penalty, "note": note}
