from typing import Dict, Any, List
from src.utils.logger import get_logger

logger = get_logger(__name__)

class ScoringEngine:
    def __init__(self):
        # Base scores for different features
        self.weights = {
            "regime_alignment": 20,
            "volume_expansion": 30,
            "oi_support": 25,
            "rr_quality": 25
        }

    def score_candidate(self, candidate_data: Dict[str, Any]) -> int:
        """
        Calculates a confidence score 0-100 for a trade setup candidate.
        """
        score = 0
        
        # 1. Regime alignment (e.g. if BTC is bullish and we go LONG)
        regime = candidate_data.get("btc_regime", "neutral")
        side = candidate_data.get("side", "LONG")
        if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
            score += self.weights["regime_alignment"]
        elif regime == "neutral":
            score += self.weights["regime_alignment"] // 2
            
        # 2. Volume expansion
        vol_data = candidate_data.get("volume_data", {})
        if vol_data.get("is_expanded"):
            # Bonus for huge volume
            ratio = vol_data.get("ratio", 1.0)
            bonus = min(10, int((ratio - 1.5) * 10)) if ratio > 1.5 else 0
            score += min(self.weights["volume_expansion"], 15 + bonus)
            
        # 3. OI Support
        oi_data = candidate_data.get("oi_data", {})
        if oi_data.get("signal") in ["bullish", "bearish"]:
            score += self.weights["oi_support"]
        elif oi_data.get("divergence"):
            # Divergence might be good for reversals, but depends on model. We give partial credit.
            score += self.weights["oi_support"] // 2
            
        # 4. R:R Quality (looking at TP1 R:R)
        risk_data = candidate_data.get("risk_data", {})
        rr_tps = risk_data.get("risk_reward_ratios", {})
        tp1_rr = rr_tps.get("rr_tp1", 0)
        
        if tp1_rr >= 1.5:
            score += self.weights["rr_quality"]
        elif tp1_rr >= 1.0:
            score += int(self.weights["rr_quality"] * 0.7)
        elif tp1_rr > 0:
            score += int(self.weights["rr_quality"] * 0.3)
            
        return min(100, score)

    def rank_candidates(self, candidates: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
        """
        Scores a list of candidates and returns the top N.
        """
        for cand in candidates:
            cand["confidence"] = self.score_candidate(cand)
            
        # Filter out anything below 50 confidence
        viable = [c for c in candidates if c["confidence"] >= 50]
        
        # Sort descending by confidence
        viable.sort(key=lambda x: x["confidence"], reverse=True)
        
        return viable[:top_n]
