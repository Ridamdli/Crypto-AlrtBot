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
from src.engines.entry_engine import EntryEngine
from src.engines.sl_engine import StopLossEngine
from src.engines.tp_engine import TakeProfitEngine
from src.engines.risk_engine import RiskEngine
from src.engines.scoring_engine import ScoringEngine
from src.engines.invalidation_engine import InvalidationEngine
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SignalPipeline:
    def __init__(self):
        # Initialize Data Layer
        self.client = BinanceFuturesClient()
        self.universe_filter = UniverseFilter(self.client)
        self.fetcher = OHLCVFetcher(self.client)
        self.tf_manager = TimeframeManager(self.fetcher)
        self.futures_service = FuturesDataService(self.client)

        # Initialize Engines
        self.btc_regime = BTCRegimeEngine()
        self.volume_engine = VolumeEngine()
        self.oi_engine = OIEngine()
        self.entry_engine = EntryEngine()
        self.sl_engine = StopLossEngine()
        self.tp_engine = TakeProfitEngine()
        self.risk_engine = RiskEngine()
        self.scoring_engine = ScoringEngine()
        self.invalidation_engine = InvalidationEngine()

    def run_pipeline(self) -> List[Dict[str, Any]]:
        logger.info("Starting Signal Pipeline...")
        
        # 1. BTC Regime
        logger.info("Evaluating BTC Regime...")
        btc_4h = self.fetcher.fetch_standardized_klines("BTCUSDT", "4h", limit=60)
        global_regime = self.btc_regime.evaluate_regime(btc_4h)
        logger.info(f"Global BTC Regime: {global_regime}")

        if global_regime == "neutral":
            logger.info("Market is neutral. Tightening criteria or halting (proceeding with caution).")

        # 2. Universe Selection
        symbols = self.universe_filter.get_eligible_symbols()
        # Limit to first 10 for speed in MVP
        symbols = symbols[:10] 
        
        candidates = []

        for symbol in symbols:
            try:
                # 3. Data Gathering
                tf_data = self.tf_manager.fetch_multi_timeframe(symbol, limit=50)
                klines_15m = tf_data.get("15m", [])
                
                if not klines_15m:
                    continue

                current_price = klines_15m[-1]["close"]
                
                # 4. Volume Engine
                vol_data = self.volume_engine.analyze_volume(klines_15m)
                if not vol_data["is_expanded"]:
                    continue # Skip if volume is flat
                    
                # 5. Open Interest
                oi_hist = self.client.get_open_interest_hist(symbol, period="15m", limit=10)
                
                # Determine basic short-term trend for OI correlation
                price_trend = "up" if current_price > klines_15m[-10]["close"] else "down"
                oi_data = self.oi_engine.analyze_oi(oi_hist, price_trend)
                
                side = "LONG" if price_trend == "up" else "SHORT" # Simplified structure check
                
                # 6. Entry, SL, TP
                entry_data = self.entry_engine.calculate_entry("pullback", current_price, klines_15m, side)
                entry_price = entry_data["entry_price"]
                
                sl_price = self.sl_engine.calculate_sl("structural", entry_price, side, klines_15m)
                tp_dict = self.tp_engine.calculate_tp(entry_price, sl_price, side)
                
                # 7. Risk Engine
                risk_data = self.risk_engine.calculate_risk_parameters(entry_price, sl_price, tp_dict)
                if "error" in risk_data:
                    continue
                    
                # 8. Rules Generation
                rules = self.invalidation_engine.generate_rules(side, entry_price, sl_price)

                # Assemble candidate
                candidate = {
                    "signal_id": f"SIG-{datetime.now().strftime('%Y%m%d')}-{symbol}-{uuid.uuid4().hex[:4].upper()}",
                    "timestamp": datetime.now().isoformat() + "Z",
                    "symbol": symbol,
                    "side": side,
                    "entry": entry_price,
                    "tp1": tp_dict.get("tp1"),
                    "tp2": tp_dict.get("tp2"),
                    "tp3": tp_dict.get("tp3"),
                    "stop_loss": sl_price,
                    
                    "btc_regime": global_regime,
                    "volume_data": vol_data,
                    "oi_data": oi_data,
                    "risk_data": risk_data
                }
                
                # Merge nested dictionaries into top-level for final output matching Signal Contract
                candidate.update({
                    "leverage": risk_data["leverage"],
                    "margin": risk_data["margin"],
                    "position_notional": risk_data["position_notional"],
                    "risk_amount": risk_data["risk_amount"],
                    "risk_reward_tp1": risk_data["risk_reward_ratios"].get("rr_tp1"),
                    "risk_reward_tp2": risk_data["risk_reward_ratios"].get("rr_tp2"),
                    "risk_reward_tp3": risk_data["risk_reward_ratios"].get("rr_tp3"),
                })
                
                candidate.update(rules)
                
                candidates.append(candidate)
                
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")

        # 9. Scoring & Ranking
        final_signals = self.scoring_engine.rank_candidates(candidates, top_n=3)
        logger.info(f"Pipeline finished. Found {len(final_signals)} viable signals.")
        return final_signals
