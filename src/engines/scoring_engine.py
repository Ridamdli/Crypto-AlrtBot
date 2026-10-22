from typing import Any

from src.config import AppConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Bump when any component rule or weight changes: audits group records by this
# string, and old records keep the version they were written with.
SCORING_ENGINE_VERSION = "scoring.v1"


class ScoringEngine:
    def __init__(self, config: AppConfig):
        self.cfg = config.scoring
        self.min_confidence = config.signals.min_confidence

    def score_breakdown(self, candidate_data: dict[str, Any], config: AppConfig) -> dict[str, Any]:
        """
        Per-component points plus the inputs the scorer saw, so a published
        score can be explained from the record alone (W-DD Option A).

        Invariants: sum(points.values()) == total_raw; confidence ==
        min(100, total_raw); `inputs` is a snapshot, not a pointer to
        top-level fields that later writes may rewrite.
        """
        points: dict[str, int] = {}
        regime = candidate_data.get("btc_regime", "neutral")
        side = candidate_data.get("side", "LONG")

        # 1. BTC Regime (20pts)
        if (regime == "bullish" and side == "LONG") or (regime == "bearish" and side == "SHORT"):
            points["regime"] = self.cfg.regime_weight
        elif regime == "neutral":
            points["regime"] = self.cfg.regime_weight // 2
        else:
            points["regime"] = 0

        # 2. Higher-timeframe structure (15pts) – simplified: always partial credit for now
        points["structure"] = int(self.cfg.structure_weight * 0.7)

        # 3. 15m Momentum (15pts) – credit if price trend matches side
        price_trend = candidate_data.get("price_trend", "")
        if (side == "LONG" and price_trend == "up") or (side == "SHORT" and price_trend == "down"):
            points["momentum"] = self.cfg.momentum_weight
        else:
            points["momentum"] = self.cfg.momentum_weight // 2

        # 4. Volume (15pts)
        vol_data = candidate_data.get("volume_data", {})
        ratio = vol_data.get("ratio", 0)
        if ratio >= config.volume.strong_ratio:
            points["volume"] = self.cfg.volume_weight
        elif ratio >= config.volume.min_ratio:
            points["volume"] = int(self.cfg.volume_weight * 0.7)
        else:
            points["volume"] = 0

        # 5. Open Interest (15pts)
        oi_data = candidate_data.get("oi_data", {})
        oi_status = oi_data.get("status", "UNKNOWN")
        oi_signal = oi_data.get("signal", "neutral")
        if oi_status == "CONFIRMED":
            if (side == "LONG" and oi_signal == "bullish") or (side == "SHORT" and oi_signal == "bearish"):
                points["oi"] = self.cfg.oi_weight
            else:
                # OI confirmed but opposite direction - still some info value
                points["oi"] = self.cfg.oi_weight // 2
        elif oi_status == "REJECTED":
            # OI divergence - small penalty
            points["oi"] = -(self.cfg.oi_weight // 2)
        else:
            # UNKNOWN: no OI bonus or penalty
            points["oi"] = 0

        # 6. Funding (5pts) – penalty if extreme vs side direction
        funding_penalty = candidate_data.get("funding_penalty", 0)
        points["funding"] = max(0, self.cfg.funding_weight - funding_penalty)

        # 7. Liquidity (5pts) – full credit if passed universe filter
        points["liquidity"] = self.cfg.liquidity_weight

        # 8. R:R (10pts)
        risk_data = candidate_data.get("risk_data", {})
        rr = risk_data.get("risk_reward_ratios", {}).get("rr_tp", 0)
        if rr >= 2.0:
            points["rr"] = self.cfg.rr_weight
        elif rr >= config.risk.min_rr:
            points["rr"] = int(self.cfg.rr_weight * 0.7)
        else:
            points["rr"] = 0

        total_raw = sum(points.values())
        return {
            "engine": SCORING_ENGINE_VERSION,
            "points": points,
            "inputs": {
                "btc_regime": regime,
                "price_trend": price_trend,
                "volume_ratio": ratio,
                "oi_status": oi_status,
                "oi_signal": oi_signal,
                "oi_change_pct": oi_data.get("oi_change_pct"),
                "funding_penalty": funding_penalty,
                "funding_rate": candidate_data.get("funding_rate"),
                "rr_tp": rr,
            },
            "total_raw": total_raw,
            "clamped": min(100, total_raw) != total_raw,
        }

    def score_candidate(self, candidate_data: dict[str, Any], config: AppConfig) -> int:
        """
        Calculates a confidence score 0-100 for a trade setup candidate.
        Weights per PRD §23: regime(20), structure(15), momentum(15),
        volume(15), oi(15), funding(5), liquidity(5), rr(10).
        """
        return min(100, self.score_breakdown(candidate_data, config)["total_raw"])

    def rank_candidates(self, candidates: list[dict[str, Any]], top_n=None, config: AppConfig = None) -> list[dict[str, Any]]:
        """Score, filter by min confidence, and return every viable signal.

        top_n=None (default) means uncapped: all qualifying setups publish.
        A numeric top_n keeps only the first N by confidence (legacy mode).
        """
        cfg = config or AppConfig()
        for cand in candidates:
            breakdown = self.score_breakdown(cand, cfg)
            # Confidence derives from the recorded breakdown so the two can
            # never disagree in an audit.
            cand["confidence"] = min(100, breakdown["total_raw"])
            cand["scoring_breakdown"] = breakdown

        viable = [c for c in candidates if c["confidence"] >= self.min_confidence]
        viable.sort(key=lambda x: x["confidence"], reverse=True)

        out = viable if top_n is None else viable[:top_n]
        logger.info(f"Scored {len(candidates)} candidates -> {len(viable)} viable -> returning {len(out)}")
        return out
