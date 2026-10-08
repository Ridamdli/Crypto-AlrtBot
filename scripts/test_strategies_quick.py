from src.strategies import create_strategy
from src.config import AppConfig

# Test creating each strategy
config = AppConfig()
for family in ['donchian', 'momentum', 'ema_trend', 'bollinger_atr', 'rsi_mean_reversion', 'volume_breakout']:
    try:
        strategy = create_strategy(family, {'params': {}}, 'BTCUSDT')
        print(f'OK {family}')
    except Exception as e:
        print(f'ERROR {family}: {e}')