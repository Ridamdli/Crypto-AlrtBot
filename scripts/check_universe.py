from src.config import CONFIG
from src.data.symbol_registry import get_symbol_registry

registry = get_symbol_registry(CONFIG)
available = registry.get_available_symbols()
print('Available symbols:', available)
print('Count:', len(available))

# Also check all symbols status
all_status = registry.get_all_symbols_status()
for sym, status in all_status.items():
    print(f'{sym}: enabled={status["enabled"]}, availability={status["data_availability"]}')