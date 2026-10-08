"""
Bollinger/ATR Volatility Breakout Strategy

Long: Price breaks above upper Bollinger Band after valid compression
Short: Price breaks below lower Bollinger Band after valid compression

Includes bandwidth/compression filter, ATR stop, volume confirmation.
"""
from typing import List, Dict, Any
import pandas as pd
from src.strategies.base import BaseStrategy, SignalCandidate
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BollingerATRStrategy(BaseStrategy):
    family = "bollinger_atr"
    supported_directions = ["LONG", "SHORT"]
    supported_timeframes = ["15m", "1h", "4h"]
    
    default_params = {
        "bb_period": 20,
        "bb_std": 2.0,
        "bandwidth_lookback": 20,
        "bandwidth_threshold": 0.1,  # Max bandwidth for compression
        "atr_period": 14,
        "atr_multiplier": 2.0,
        "volume_threshold": 1.5,
        "regime_filter": True,
    }
    
    param_schema = {
        "bb_period": {"type": "int", "min": 10, "max": 50, "description": "Bollinger Band period"},
        "bb_std": {"type": "float", "min": 1.0, "max": 3.0, "description": "Bollinger Band standard deviations"},
        "bandwidth_lookback": {"type": "int", "min": 10, "max": 50, "description": "Bandwidth lookback for compression"},
        "bandwidth_threshold": {"type": "float", "min": 0.01, "max": 0.5, "description": "Max bandwidth for compression"},
        "atr_period": {"type": "int", "min": 7, "max": 30, "description": "ATR period"},
        "atr_multiplier": {"type": "float", "min": 1.0, "max": 4.0, "description": "ATR multiplier for stop"},
        "volume_threshold": {"type": "float", "min": 1.0, "max": 3.0, "description": "Volume expansion threshold"},
        "regime_filter": {"type": "bool", "description": "Filter by BTC regime"},
    }
    
    def _calculate_bollinger_bands(self, df: pd.DataFrame, period: int, std_mult: float) -> tuple:
        """Calculate Bollinger Bands (middle, upper, lower)."""
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_mult)
        lower = sma - (std * std_mult)
        return sma, upper, lower
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
    def _calculate_bandwidth(self, df: pd.DataFrame, period: int, std_mult: float) -> pd.Series:
        """Calculate Bollinger Band width (bandwidth)."""
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_mult)
        lower = sma - (std * std_mult)
        bandwidth = (upper - lower) / sma
        return bandwidth
    
    def generate_candidates(
        self,
        klines_15m: List[Dict[str, Any]],
        klines_1h: List[Dict[str, Any]],
        klines_4h: List[Dict[str, Any]],
        btc_regime: str,
        oi_data: Dict[str, Any],
        funding_rate: float,
        current_price: float,
    ) -> List[SignalCandidate]:
        from src.strategies.base import SignalCandidate
        
        candidates = []
        
        if len(klines_15m) < self.params["bb_period"] + 2:
            return candidates
        
        df_15m = pd.DataFrame(klines_15m)
        if not df_15m.empty:
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df_15m[col] = df_15m[col].astype(float)
        
        # Calculate Bollinger Bands
        bb_period = self.params["bb_period"]
        bb_std = self.params["bb_std"]
        sma, upper, lower = self._calculate_bollinger_bands(df_15m, bb_period, bb_std)
        
        sma_current = float(sma.iloc[-1])
        upper_current = float(upper.iloc[-1])
        lower_current = float(lower.iloc[-1])
        
        # Bandwidth (compression filter)
        bandwidth = self._calculate_bandwidth(df_15m, bb_period, self.params["bb_std"])
        bandwidth_current = float(bandwidth.iloc[-1])
        bandwidth_avg = bandwidth.rolling(window=self.params["bandwidth_lookback"]).mean().iloc[-1]
        
        compression = bandwidth_current < self.params["bandwidth_threshold"] if not pd.isna(bandwidth_current) else False
        
        # ATR
        atr_15m = self._calculate_atr(pd.DataFrame(klines_15m), self.params["atr_period"])
        
        # Volume
        volume_ratio = self._calculate_volume_ratio(pd.DataFrame(klines_15m))
        
        if self.params["volume_threshold"] and volume_ratio < self.params["volume_threshold"]:
            return []
        
        # Regime filter
        if self.params["regime_filter"]:
            pass  # Handled by pipeline
        
        min_risk_dist = atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005
        
        candidates = []
        
        # LONG: Breakout above upper band with compression
        if current_price > upper_current and compression:
            entry_price = current_price
            
            # SL: ATR-based or middle band
            atr_sl = current_price - (atr_15m * self.params["atr_multiplier"]) if atr_15m > 0 else 0
            structural_sl = sma_current * 0.995
            sl_price = max(structural_sl, current_price - atr_15m * self.params["atr_multiplier"]) if atr_15m > 0 else structural_sl
            sl_price = min(sl_price, current_price - (atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005))
            
            risk_dist = current_price - sl_price
            if risk_dist > 0:
                tp1 = current_price + risk_dist * 2.0
                tp2 = current_price + risk_dist * 3.0
                tp3 = current_price + risk_dist * 4.0
                
                from src.strategies.base import SignalCandidate
                candidates.append(SignalCandidate(
                    symbol=self.symbol,
                    side="LONG",
                    entry_price=entry_price,
                    stop_loss=sl_price,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    confidence=75,
                    strategy_id=f"bollinger_atr_{self.params['bb_period']}_{self.params['bb_std']}",
                    strategy_version="1.0.0",
                    timeframe="15m",
                    parameters=self.params.copy(),
                    conditions=[
                        f"BB breakout: close > upper band ({upper_current:.4f})",
                        f"Bandwidth compression: {bandwidth_current:.4f} < {self.params['bandwidth_threshold']}",
                        f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                    ],
                    invalidation_conditions=[
                        f"Close below middle band ({sma_current:.4f})",
                        "Bandwidth expands",
                        "Volume dries up",
                    ],
                    management_rules=[
                        "TP1: close 50%",
                        "TP2: close 25%",
                        "TP3: close 25%",
                        "After TP1: SL to entry",
                        "After TP2: trailing SL to TP1",
                    ],
                    metadata={
                        "upper_band": upper_current,
                        "lower_band": lower_current,
                        "middle_band": sma_current,
                        "bandwidth": bandwidth_current,
                        "bandwidth_avg": bandwidth_avg,
                        "compression": compression,
                        "atr": atr_15m,
                        "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                    }
                ))
        
        # SHORT: Breakout below lower band with compression
        if current_price < lower_current and compression:
            entry_price = current_price
            
            atr_sl = current_price + (atr_15m * self.params["atr_multiplier"]) if atr_15m > 0 else 0
            structural_sl = sma_current * 1.005
            sl_price = min(structural_sl, current_price + atr_15m * self.params["atr_multiplier"]) if atr_15m > 0 else structural_sl
            sl_price = max(sl_price, current_price + (atr_15m * 0.5 if atr_15m > 0 else current_price * 0.005))
            
            risk_dist = sl_price - current_price
            if risk_dist > 0:
                tp1 = current_price - risk_dist * 2.0
                tp2 = current_price - risk_dist * 3.0
                tp3 = current_price - risk_dist * 4.0
                
                from src.strategies.base import SignalCandidate
                candidates.append(SignalCandidate(
                    symbol=self.symbol,
                    side="SHORT",
                    entry_price=entry_price,
                    stop_loss=sl_price,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    confidence=75,
                    strategy_id=f"bollinger_atr_{self.params['bb_period']}_{self.params['bb_std']}",
                    strategy_version="1.0.0",
                    timeframe="15m",
                    parameters=self.params.copy(),
                    conditions=[
                        f"BB breakout: close < lower band ({lower_current:.4f})",
                        f"Bandwidth compression: {bandwidth_current:.4f} < {self.params['bandwidth_threshold']}",
                        f"Volume ratio: {self._calculate_volume_ratio(pd.DataFrame(klines_15m)):.2f}x",
                    ],
                    invalidation_conditions=[
                        f"Close above middle band ({sma_current:.4f})",
                        "Bandwidth expands",
                        "Volume dries up",
                    ],
                    management_rules=[
                        "TP1: close 50%",
                        "TP2: close 25%",
                        "TP3: close 25%",
                        "After TP1: SL to entry",
                        "After TP2: trailing SL to TP1",
                    ],
                    metadata={
                        "upper_band": upper_current,
                        "lower_band": lower_current,
                        "middle_band": sma_current,
                        "bandwidth": bandwidth_current,
                        "bandwidth_avg": bandwidth_avg,
                        "compression": compression,
                        "atr": atr_15m,
                        "volume_ratio": self._calculate_volume_ratio(pd.DataFrame(klines_15m)),
                    }
                ))
        
        return candidates
    
    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr = df['tr'].rolling(window=period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, window: int = 20) -> float:
        if len(df) < window + 1:
            return 0.0
        current_vol = df.iloc[-1]['volume']
        rolling_avg = df.iloc[-(window + 1):-1]['volume'].mean()
        return current_vol / rolling_avg if rolling_avg > 0 else 0.0
    
    def _calculate_bollinger_bands(self, df: pd.DataFrame, period: int, std_mult: float) -> tuple:
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_mult)
        lower = sma - (std * std_mult)
        return sma, upper, lower
    
    def _calculate_bandwidth(self, df: pd.DataFrame, period: int, std_mult: float) -> pd.Series:
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_mult)
        lower = sma - (std * std_mult)
        bandwidth = (upper - lower) / sma
        return bandwidth