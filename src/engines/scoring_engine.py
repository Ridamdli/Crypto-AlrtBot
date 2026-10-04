from typing import List, Dict, Any
from src.utils.logger import get_logger
from src.config import CONFIG

logger = get_logger(__name__)

class ScoringEngine:
    def __init__(self):
        self.cfg = CONFIG.scoring
        self.min_confidence = CONFIG.signals.min_confidence

    def score_candidate(self, candidate_data: Dict[str, Any]) -> int:
        """
        Calculates a confidence score 0-100 for a trade setup candidate.
        Weights per PRD §23: regime(20), structure(15), momentum(15),
        volume(15), oi(15), funding(5), liquidity(5), rr(10).
        """
        score = 0
        regime = candidate_data.get("btc_regime", "neutral")
        side = candidate_data.get("side", "LONG")

        # 1. BTC Regime (20pts)
        if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
            score += self.cfg.regime_weight
        elif regime == "neutral":
            score += self.cfg.regime_weight // 2

        # 2. Higher-timeframe structure (15pts) – simplified: always partial credit for now
        score += int(self.cfg.structure_weight * 0.7)

        # 3. 15m Momentum (15pts) – credit if price trend matches side
        price_trend = candidate_data.get("price_trend", "")
        if (side == "LONG" and price_trend == "up") or (side == "SHORT" and price_trend == "down"):
            score += self.cfg.momentum_weight
        else:
            score += self.cfg.momentum_weight // 2

        # 4. Volume (15pts)
        vol_data = candidate_data.get("volume_data", {})
        ratio = vol_data.get("ratio", 0)
        if ratio >= CONFIG.volume.strong_ratio:
            score += self.cfg.volume_weight
        elif ratio >= CONFIG.volume.min_ratio:
            score += int(self.cfg.volume_weight * 0.7)

        # 5. Open Interest (15pts)
        oi_data = candidate_data.get("oi_data", {})
        oi_signal = oi_data.get("signal", "neutral")
        if (side == "LONG" and oi_signal == "bullish") or (side == "SHORT" and oi_signal == "bearish"):
            score += self.cfg.oi_weight
        elif not oi_data.get("divergence"):
            score += self.cfg.oi_weight // 2

        # 6. Funding (5pts) – penalty if extreme vs side direction
        funding_penalty = candidate_data.get("funding_penalty", 0)
        score += max(0, self.cfg.funding_weight - funding_penalty)

        # 7. Liquidity (5pts) – full credit if passed universe filter
        score += self.cfg.liquidity_weight

        # 8. R:R (10pts)
        risk_data = candidate_data.get("risk_data", {})
        rr = risk_data.get("risk_reward_ratios", {}).get("rr_tp1", 0)
        if rr >= 2.0:
            score += self.cfg.rr_weight
        elif rr >= CONFIG.risk.min_rr_tp1:
            score += int(self.cfg.rr_weight * 0.7)

        return min(100, score)

    def rank_candidates(self, candidates: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
        """Score, filter by min confidence, and return top N."""
        for cand in candidates:
            cand["confidence"] = self.score_candidate(cand)

        viable = [c for c in candidates if c["confidence"] >= self.min_confidence]
        viable.sort(key=lambda x: x["confidence"], reverse=True)

        top = viable[:top_n]
        logger.info(f"Scored {len(candidates)} candidates → {len(viable)} viable → returning {len(top)}")
        return top
