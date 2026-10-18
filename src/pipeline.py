from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone
import hashlib
import os

from src.data.binance_client import BinanceFuturesClient
from src.data.universe_filter import UniverseFilter
from src.data.ohlcv_fetcher import OHLCVFetcher
from src.data.timeframe_manager import TimeframeManager
from src.data.futures_data import FuturesDataService
from src.data.symbol_registry import get_symbol_registry

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

from src.strategies import STRATEGY_REGISTRY, create_strategy, STRATEGY_TIMEFRAMES

from src.config import CONFIG, AppConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _price_trend_15m(klines_15m, lookback: int = 10) -> str:
    """15m price direction over the OI window: 'up' | 'down' | 'neutral'.

    Same comparison as MomentumFilterStrategy._get_trend (last close vs
    close ``lookback`` bars back, completed candles only, scan-time data).
    Anything unreadable degrades to 'neutral' (half credit, no OI
    confirmation) — never raises into the scan.
    """
    try:
        closes = [float(k.get("close", 0)) for k in (klines_15m or [])]
    except (TypeError, ValueError, AttributeError):
        return "neutral"
    if len(closes) < lookback + 1:
        return "neutral"
    if closes[-1] > closes[-(lookback + 1)]:
        return "up"
    if closes[-1] < closes[-(lookback + 1)]:
        return "down"
    return "neutral"


class SignalPipeline:
    def __init__(
        self,
        config: Optional[AppConfig] = None,
        client: Optional[BinanceFuturesClient] = None,
        fetcher: Optional[OHLCVFetcher] = None,
        tf_manager: Optional[TimeframeManager] = None,
        universe_filter: Optional[UniverseFilter] = None,
    ):
        self.config = config or CONFIG
        self.client = client or BinanceFuturesClient()
        self.universe_filter = universe_filter or UniverseFilter(
            self.client, min_volume_usdt=self.config.universe.min_volume_usdt
        )
        self.fetcher = fetcher or OHLCVFetcher(self.client)
        self.tf_manager = tf_manager or TimeframeManager(
            self.fetcher,
            timeframes=[
                self.config.timeframes.entry,
                self.config.timeframes.trend,
                self.config.timeframes.regime,
            ],
        )
        self.futures_service = FuturesDataService(self.client)

        # Validate configuration
        self.config.validate()

        self.symbol_registry = get_symbol_registry(self.config)

        self.btc_regime = BTCRegimeEngine()
        self.volume_engine = VolumeEngine(
            rolling_window=self.config.volume.rolling_window,
            min_ratio=self.config.volume.min_ratio,
        )
        self.oi_engine = OIEngine(
            min_oi_change_pct=self.config.oi.min_change_pct,
            oi_mode=self.config.oi.mode,
        )
        self.funding_engine = FundingEngine()
        self.entry_engine = EntryEngine()
        self.sl_engine = StopLossEngine(structural_lookback=self.config.risk.sl_lookback)
        self.tp_engine = TakeProfitEngine(r_multiple=self.config.tp.r_multiple)
        self.risk_engine = RiskEngine(
            account_size=self.config.risk.account_size,
            max_risk_pct=self.config.risk.max_risk_pct,
            max_leverage=self.config.risk.max_leverage,
        )
        self.scoring_engine = ScoringEngine(config=self.config)
        self.invalidation_engine = InvalidationEngine()

        # Initialize strategy instances per symbol (cached)
        self._strategy_cache: Dict[str, Dict[str, Any]] = {}

    def _get_strategy_instance(self, symbol: str, strategy_family: str, strategy_config: Dict[str, Any]) -> Any:
        """Get or create a strategy instance for a symbol."""
        cache_key = f"{symbol}:{strategy_family}"
        if cache_key not in self._strategy_cache:
            config = {
                "params": strategy_config.get("params", {}),
            }
            self._strategy_cache[cache_key] = create_strategy(strategy_family, config, symbol)
        return self._strategy_cache[cache_key]

    def _live_scan_symbols(self, all_symbols: List[str]) -> List[str]:
        """Intersect the volume-filtered universe with the live scan set.

        LIVE_REQUIRE_VALIDATED (default true): admit only coins with
        validated history (the AVAILABLE set). Coins without backtest
        evidence (DATA_MISSING) are excluded from live alerts unless the
        operator explicitly opts back into the full 14-coin universe with
        LIVE_REQUIRE_VALIDATED=false.
        """
        tradable = self.symbol_registry.get_tradable_symbols()
        if os.getenv("LIVE_REQUIRE_VALIDATED", "true").lower() == "true":
            validated = set(self.symbol_registry.get_available_symbols())
            gated = sorted(s for s in tradable if s not in validated)
            if gated:
                logger.info(f"Live gate: {len(gated)} unvalidated coins excluded: {gated}")
            tradable = [s for s in tradable if s in validated]
        return [s for s in all_symbols if s in tradable]

    def _get_strategies_for_symbol(self, symbol: str) -> List[str]:
        """Get the list of strategy families to test for a symbol."""
        symbol_cfg = self.symbol_registry.get_symbol_config(symbol)
        # Use priority strategies from config, or default to all enabled
        priority = symbol_cfg.priority_strategies
        if priority:
            return priority
        # Fallback: all enabled strategy families
        return STRATEGY_REGISTRY.list_families()

    def _parse_as_of_time(
        self, as_of_time: Optional[Union[int, str, datetime]]
    ) -> tuple[Optional[int], str]:
        """
        Parses as_of_time into (timestamp_ms, iso_str).
        If as_of_time is None, returns (None, current_utc_iso).
        """
        if as_of_time is None:
            now = datetime.now(timezone.utc)
            return None, now.strftime("%Y-%m-%dT%H:%M:%SZ")

        if isinstance(as_of_time, int):
            # assume milliseconds if > 1e11, else seconds
            ts_ms = as_of_time if as_of_time > 100_000_000_000 else as_of_time * 1000
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            return ts_ms, dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if isinstance(as_of_time, datetime):
            if as_of_time.tzinfo is None:
                as_of_time = as_of_time.replace(tzinfo=timezone.utc)
            ts_ms = int(as_of_time.timestamp() * 1000)
            return ts_ms, as_of_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        if isinstance(as_of_time, str):
            # Parse ISO string
            cleaned = as_of_time.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts_ms = int(dt.timestamp() * 1000)
            return ts_ms, dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        raise ValueError(f"Unsupported as_of_time type: {type(as_of_time)}")

    def run_pipeline(
        self,
        as_of_time: Optional[Union[int, str, datetime]] = None,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        as_of_ts, timestamp_str = self._parse_as_of_time(as_of_time)
        dt_key = timestamp_str[:10].replace("-", "")

        logger.info("=" * 50)
        logger.info(f"Starting Signal Pipeline (as_of_time: {timestamp_str})...")

        # ── Step 1: BTC Global Regime ───────────────────────────────────────
        logger.info("Step 1: Evaluating BTC Regime...")
        btc_4h = self.fetcher.fetch_standardized_klines(
            "BTCUSDT", self.config.timeframes.regime, limit=100, end_time=as_of_ts
        )
        global_regime = self.btc_regime.evaluate_regime(btc_4h)
        logger.info(f"Global BTC Regime: {global_regime.upper()}")

        # ── Step 2: Universe ────────────────────────────────────────────────
        # Live scans cover enabled coins, BTC included.
        # BTC still feeds Step 1 regime detection independently.
        # Explicit `symbols=` callers (research, eval harness, tests) bypass
        # the live-validated gate below. See _live_scan_symbols.
        if symbols is not None:
            active_symbols = [s for s in symbols if self.symbol_registry.is_tradable(s)]
        else:
            logger.info("Step 2: Fetching eligible symbols...")
            all_symbols = self.universe_filter.get_eligible_symbols()
            active_symbols = self._live_scan_symbols(all_symbols)

        logger.info(f"Scanning {len(active_symbols)} eligible symbols")

        candidates = []
        rejected_count = 0
        config_hash = self.config.compute_hash()

        for symbol in active_symbols:
            try:
                # Skip disabled symbols (live path uses tradable set, not history availability)
                if not self.symbol_registry.is_tradable(symbol):
                    logger.debug(f"{symbol}: not enabled for live trading, skipping")
                    rejected_count += 1
                    continue

                # ── Step 3: Multi-TF Data ────────────────────────────────────
                tf_data = self.tf_manager.fetch_multi_timeframe(
                    symbol, limit=100, end_time=as_of_ts
                )
                klines_15m = tf_data.get(self.config.timeframes.entry, [])
                klines_1h = tf_data.get(self.config.timeframes.trend, [])
                klines_4h = tf_data.get(self.config.timeframes.regime, [])

                if len(klines_15m) < 25:
                    logger.debug(f"{symbol}: insufficient 15m candles, skipping")
                    rejected_count += 1
                    continue

                current_price = float(klines_15m[-1]["close"])

                # ── Step 4: Volume Filter (global pre-filter) ──────────────────
                vol_data = self.volume_engine.analyze_volume(klines_15m)
                if not vol_data["is_expanded"]:
                    logger.debug(
                        f"{symbol}: volume ratio {vol_data['ratio']:.2f} below threshold, skipping"
                    )
                    rejected_count += 1
                    continue

                # ── Step 5: OI ────────────────────────────────────────────────
                oi_hist = self.client.get_open_interest_hist(
                    symbol, period="15m", limit=10, end_time=as_of_ts
                )
                price_trend = _price_trend_15m(klines_15m)
                oi_data = self.oi_engine.analyze_oi(oi_hist, price_trend)

                # OI rejection check (for OI_REQUIRED mode)
                if self.oi_engine.should_reject_candidate(oi_data):
                    logger.debug(
                        f"{symbol}: OI status {oi_data.get('status')} ({oi_data.get('reason')}), skipping per OI mode"
                    )
                    rejected_count += 1
                    continue

                # ── Step 6: Funding ───────────────────────────────────────────
                if as_of_ts:
                    funding_hist = self.client.get_funding_rate(
                        symbol, limit=1, end_time=as_of_ts
                    )
                    funding_rate = (
                        float(funding_hist[-1].get("fundingRate", 0))
                        if funding_hist
                        else 0.0
                    )
                else:
                    mark_data = self.client.get_mark_price(symbol)
                    funding_rate = float(mark_data.get("lastFundingRate", 0))

                # ── Step 7: Run Strategies ────────────────────────────────────
                strategy_families = self._get_strategies_for_symbol(symbol)
                symbol_candidates = []

                for strategy_family in strategy_families:
                    try:
                        strategy = self._get_strategy_instance(symbol, strategy_family, {})
                        
                        # Determine timeframe for this strategy
                        timeframe = STRATEGY_TIMEFRAMES.get(strategy_family, "15m")
                        klines_entry = tf_data.get(timeframe, [])
                        
                        # Get the right klines for this strategy's timeframe
                        if timeframe == "15m":
                            klines_entry = klines_15m
                        elif timeframe == "1h":
                            klines_entry = klines_1h
                        elif timeframe == "4h":
                            klines_entry = klines_4h
                        else:
                            klines_entry = klines_15m

                        if len(klines_entry) < 25:
                            continue

                        # OI uses the symbol's 15m trend computed above (same
                        # completed candles the strategies see — no look-ahead).
                        oi_data = self.oi_engine.analyze_oi(oi_hist, price_trend)

                        # Generate candidates from strategy
                        strategy_candidates = strategy.generate_candidates(
                            klines_15m=klines_15m,
                            klines_1h=klines_1h,
                            klines_4h=klines_4h,
                            btc_regime=global_regime,
                            oi_data=oi_data,
                            funding_rate=funding_rate,
                            current_price=current_price,
                        )

                        for cand in strategy_candidates:
                            # Enrich candidate with common fields
                            cand_dict = {
                                "signal_id": "",  # Will be set below
                                "timestamp": timestamp_str,
                                "symbol": symbol,
                                "side": cand.side,
                                "strategy_id": cand.strategy_id,
                                "strategy_version": cand.strategy_version,
                                "configuration_hash": self.config.compute_hash(),
                                "entry": cand.entry_price,
                                "tp": cand.tp,
                                "stop_loss": cand.stop_loss,
                                "leverage": 0,  # Will be calculated
                                "margin": 0,
                                "position_notional": 0,
                                "risk_amount": 0,
                                "risk_reward": 0,
                                "conditions": cand.conditions,
                                "invalidation_conditions": cand.invalidation_conditions,
                                "management_rules": cand.management_rules,
                                "btc_regime": global_regime,
                                "volume_data": vol_data,
                                "oi_data": oi_data,
                                "funding_penalty": 0,  # Will be calculated
                                "volume_ratio": vol_data.get("ratio"),
                                "oi_change_pct": oi_data.get("oi_change_pct"),
                                "funding_rate": funding_rate,
                                # Strategy-specific metadata
                                "strategy_metadata": cand.metadata,
                            }

                            # Calculate risk parameters
                            risk_data = self.risk_engine.calculate_risk_parameters(
                                cand.entry_price, cand.stop_loss, {"tp": cand.tp}
                            )
                            if "error" in risk_data:
                                logger.debug(f"{symbol}: risk error – {risk_data['error']}, skipping")
                                continue

                            # Minimum R:R gate
                            rr_tp = risk_data["risk_reward_ratios"].get("rr_tp", 0)
                            if rr_tp < self.config.risk.min_rr:
                                logger.debug(f"{symbol}: R:R {rr_tp:.2f} below minimum, skipping")
                                continue

                            # Funding penalty
                            funding_eval = self.funding_engine.evaluate(funding_rate, cand.side)
                            funding_penalty = funding_eval["penalty"]

                            # Invalidation rules
                            rules = self.invalidation_engine.generate_rules(cand.side, cand.entry_price, cand.stop_loss)

                            actual_conditions = [
                                f"BTC regime: {global_regime}",
                                f"15m volume ratio: {vol_data['ratio']:.2f}x (threshold {self.config.volume.min_ratio}x)",
                                f"OI signal: {oi_data['signal']} ({oi_data.get('oi_change_pct', 0):.2f}%)",
                                f"Funding rate: {funding_rate:.4%}",
                                f"R:R TP: {rr_tp}R",
                            ] + cand.conditions

                            # Deterministic signal ID
                            dt_key = timestamp_str[:10].replace("-", "")
                            config_hash = self.config.compute_hash()
                            id_seed = f"{timestamp_str}_{symbol}_{cand.side}_{cand.entry_price:.6f}_{self.config.signals.min_confidence}_{config_hash}_{cand.strategy_id}"
                            sig_hash = hashlib.sha256(id_seed.encode()).hexdigest()[:6].upper()
                            signal_id = f"SIG-{dt_key}-{symbol}-{sig_hash}"

                            candidate = {
                                "signal_id": signal_id,
                                "timestamp": timestamp_str,
                                "symbol": symbol,
                                "side": cand.side,
                                "strategy_id": cand.strategy_id,
                                "strategy_version": cand.strategy_version,
                                "configuration_hash": config_hash,
                                "entry": cand.entry_price,
                                "tp": cand.tp,
                                "stop_loss": cand.stop_loss,
                                "leverage": risk_data["leverage"],
                                "margin": risk_data["margin"],
                                "position_notional": risk_data["position_notional"],
                                "risk_amount": risk_data["risk_amount"],
                                "risk_reward": risk_data["risk_reward_ratios"].get("rr_tp"),
                                "conditions": actual_conditions,
                                "invalidation_conditions": rules["invalidation_conditions"],
                                "management_rules": rules["management_rules"],
                                "btc_regime": global_regime,
                                "price_trend": price_trend,
                                "volume_data": vol_data,
                                "oi_data": oi_data,
                                "risk_data": risk_data,
                                "funding_penalty": funding_penalty,
                                "volume_ratio": vol_data.get("ratio"),
                                "oi_change_pct": oi_data.get("oi_change_pct"),
                                "funding_rate": funding_rate,
                                "strategy_id": cand.strategy_id,
                                "strategy_version": cand.strategy_version,
                                "strategy_metadata": cand.metadata,
                            }

                            symbol_candidates.append(candidate)

                    except Exception as e:
                        logger.error(f"Error in strategy {strategy_family} for {symbol}: {e}")
                        continue

                candidates.extend(symbol_candidates)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                rejected_count += 1

        logger.info(f"Scan complete: {len(candidates)} candidates, {rejected_count} rejected")

        # ── Step N: Score (uncapped — every viable setup publishes) ─────────
        final_signals = self.scoring_engine.rank_candidates(
            candidates, top_n=None, config=self.config
        )
        logger.info(f"Pipeline finished. Publishing {len(final_signals)} signals.")
        logger.info("=" * 50)
        return final_signals