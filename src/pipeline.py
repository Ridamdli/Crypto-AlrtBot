from typing import Dict, Any, List
from datetime import datetime
import uuid

from src.data.binance_client import BinanceFuturesClient
from src.data.universe_filter import UniverseFilter
from src.data.ohlcv_fetcher import OHLCVFetcher
from src.data.timeframe_manager import TimeframeManager
from src.data.futures_data import FuturesDataService

from src.engines.btc_regime import BTCRegimeEngine
from src.engines.volume_engine import VolumeEngine
from src.engines.oi_engine import OIEngine
from src.engines.funding_engine import FundingEngine
from src.engines.entry_engine import EntryEngine
from src.engines.sl_engine import StopLossEngine
from src.engines.tp_engine import TakeProfitEngine
from src.engines.risk_engine import RiskEngine
from src.engines.scoring_engine import ScoringEngine
from src.engines.invalidation_engine import InvalidationEngine
from src.config import CONFIG
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SignalPipeline:
    def __init__(self):
        self.client = BinanceFuturesClient()
        self.universe_filter = UniverseFilter(self.client, min_volume_usdt=CONFIG.universe.min_volume_usdt)
        self.fetcher = OHLCVFetcher(self.client)
        self.tf_manager = TimeframeManager(
            self.fetcher,
            timeframes=[CONFIG.timeframes.entry, CONFIG.timeframes.trend, CONFIG.timeframes.regime]
        )
        self.futures_service = FuturesDataService(self.client)

        self.btc_regime = BTCRegimeEngine()
        self.volume_engine = VolumeEngine(
            rolling_window=CONFIG.volume.rolling_window,
            min_ratio=CONFIG.volume.min_ratio
        )
        self.oi_engine = OIEngine(min_oi_change_pct=CONFIG.oi.min_change_pct)
        self.funding_engine = FundingEngine()
        self.entry_engine = EntryEngine()
        self.sl_engine = StopLossEngine()
        self.tp_engine = TakeProfitEngine()
        self.risk_engine = RiskEngine(
            account_size=CONFIG.risk.account_size,
            max_risk_pct=CONFIG.risk.max_risk_pct,
            max_leverage=CONFIG.risk.max_leverage
        )
        self.scoring_engine = ScoringEngine()
        self.invalidation_engine = InvalidationEngine()

    def run_pipeline(self) -> List[Dict[str, Any]]:
        logger.info("=" * 50)
        logger.info("Starting Signal Pipeline...")

        # ── Step 1: BTC Global Regime ───────────────────────────────────────
        logger.info("Step 1: Evaluating BTC Regime...")
        btc_4h = self.fetcher.fetch_standardized_klines("BTCUSDT", CONFIG.timeframes.regime, limit=100)
        global_regime = self.btc_regime.evaluate_regime(btc_4h)
        logger.info(f"Global BTC Regime: {global_regime.upper()}")

        # ── Step 2: Universe ────────────────────────────────────────────────
        logger.info("Step 2: Fetching eligible symbols...")
        all_symbols = self.universe_filter.get_eligible_symbols()
        # Exclude BTC itself from candidate scan (used as regime reference)
        symbols = [s for s in all_symbols if s != "BTCUSDT"]
        logger.info(f"Scanning {len(symbols)} eligible symbols")

        candidates = []
        rejected_count = 0

        for symbol in symbols:
            try:
                # ── Step 3: Multi-TF Data ────────────────────────────────────
                tf_data = self.tf_manager.fetch_multi_timeframe(symbol, limit=100)
                klines_15m = tf_data.get(CONFIG.timeframes.entry, [])
                klines_1h = tf_data.get(CONFIG.timeframes.trend, [])

                if len(klines_15m) < 25:
                    logger.debug(f"{symbol}: insufficient 15m candles, skipping")
                    rejected_count += 1
                    continue

                current_price = float(klines_15m[-1]["close"])

                # ── Step 4: Volume Filter ─────────────────────────────────────
                vol_data = self.volume_engine.analyze_volume(klines_15m)
                if not vol_data["is_expanded"]:
                    logger.debug(f"{symbol}: volume ratio {vol_data['ratio']:.2f} below threshold, skipping")
                    rejected_count += 1
                    continue

                # ── Step 5: Determine Side (simplified trend structure) ────────
                # Use 1H close comparison for trend direction
                if len(klines_1h) >= 10:
                    price_trend = "up" if current_price > float(klines_1h[-10]["close"]) else "down"
                else:
                    price_trend = "up" if current_price > float(klines_15m[-10]["close"]) else "down"

                side = "LONG" if price_trend == "up" else "SHORT"

                # BTC regime directional filter: if regime opposes side, skip
                if global_regime == "bearish" and side == "LONG":
                    logger.debug(f"{symbol}: BTC bearish, skipping LONG candidate")
                    rejected_count += 1
                    continue
                if global_regime == "bullish" and side == "SHORT":
                    logger.debug(f"{symbol}: BTC bullish, skipping SHORT candidate")
                    rejected_count += 1
                    continue

                # ── Step 6: OI ────────────────────────────────────────────────
                oi_hist = self.client.get_open_interest_hist(symbol, period="15m", limit=10)
                oi_data = self.oi_engine.analyze_oi(oi_hist, price_trend)

                # ── Step 7: Funding ───────────────────────────────────────────
                mark_data = self.client.get_mark_price(symbol)
                funding_rate = float(mark_data.get("lastFundingRate", 0))
                funding_eval = self.funding_engine.evaluate(funding_rate, side)
                funding_penalty = funding_eval["penalty"]

                # ── Step 8: Entry / SL / TP ───────────────────────────────────
                entry_data = self.entry_engine.calculate_entry("pullback", current_price, klines_15m, side)
                entry_price = entry_data["entry_price"]

                sl_price = self.sl_engine.calculate_sl("structural", entry_price, side, klines_15m)
                tp_dict = self.tp_engine.calculate_tp(entry_price, sl_price, side)

                # ── Step 9: Risk ──────────────────────────────────────────────
                risk_data = self.risk_engine.calculate_risk_parameters(entry_price, sl_price, tp_dict)
                if "error" in risk_data:
                    logger.debug(f"{symbol}: risk error – {risk_data['error']}, skipping")
                    rejected_count += 1
                    continue

                # Minimum R:R gate
                rr_tp1 = risk_data["risk_reward_ratios"].get("rr_tp1", 0)
                if rr_tp1 < CONFIG.risk.min_rr_tp1:
                    logger.debug(f"{symbol}: R:R {rr_tp1:.2f} below minimum {CONFIG.risk.min_rr_tp1}, skipping")
                    rejected_count += 1
                    continue

                # ── Step 10: Invalidation Rules ───────────────────────────────
                rules = self.invalidation_engine.generate_rules(side, entry_price, sl_price)

                # Enrich conditions with factual signal context
                actual_conditions = [
                    f"BTC regime: {global_regime}",
                    f"15m volume ratio: {vol_data['ratio']:.2f}x (threshold {CONFIG.volume.min_ratio}x)",
                    f"OI signal: {oi_data['signal']} ({oi_data['oi_change_pct']:.2f}%)",
                    f"Funding rate: {funding_rate:.4%}",
                    f"R:R TP1/TP2/TP3: {rr_tp1}R / {risk_data['risk_reward_ratios'].get('rr_tp2')}R / {risk_data['risk_reward_ratios'].get('rr_tp3')}R",
                ]

                # ── Assemble Candidate ─────────────────────────────────────────
                candidate = {
                    "signal_id": f"SIG-{datetime.now().strftime('%Y%m%d')}-{symbol}-{uuid.uuid4().hex[:4].upper()}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "symbol": symbol,
                    "side": side,
                    "entry": entry_price,
                    "tp1": tp_dict.get("tp1"),
                    "tp2": tp_dict.get("tp2"),
                    "tp3": tp_dict.get("tp3"),
                    "stop_loss": sl_price,

                    "leverage": risk_data["leverage"],
                    "margin": risk_data["margin"],
                    "position_notional": risk_data["position_notional"],
                    "risk_amount": risk_data["risk_amount"],
                    "risk_reward_tp1": risk_data["risk_reward_ratios"].get("rr_tp1"),
                    "risk_reward_tp2": risk_data["risk_reward_ratios"].get("rr_tp2"),
                    "risk_reward_tp3": risk_data["risk_reward_ratios"].get("rr_tp3"),

                    "conditions": actual_conditions,
                    "invalidation_conditions": rules["invalidation_conditions"],
                    "management_rules": rules["management_rules"],

                    # Scoring context (not in Signal Contract, extra allowed)
                    "btc_regime": global_regime,
                    "price_trend": price_trend,
                    "volume_data": vol_data,
                    "oi_data": oi_data,
                    "risk_data": risk_data,
                    "funding_penalty": funding_penalty,
                    "volume_ratio": vol_data.get("ratio"),
                    "oi_change_pct": oi_data.get("oi_change_pct"),
                    "funding_rate": funding_rate,
                }

                candidates.append(candidate)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                rejected_count += 1

        logger.info(f"Scan complete: {len(candidates)} candidates, {rejected_count} rejected")

        # ── Step 11: Score & Rank ──────────────────────────────────────────────
        final_signals = self.scoring_engine.rank_candidates(candidates, top_n=CONFIG.signals.max_daily)
        logger.info(f"Pipeline finished. Publishing {len(final_signals)} signals.")
        logger.info("=" * 50)
        return final_signals
