import sqlite3
from datetime import datetime

conn = sqlite3.connect('data_store/historical/historical_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check data quality for the 'missing' symbols
for sym in ['ZECUSDT', 'HYPEUSDT', 'UNIUSDT', 'NEARUSDT']:
    cursor.execute('SELECT interval, COUNT(*) FROM klines WHERE symbol=? GROUP BY interval', (sym,))
    rows = cursor.fetchall()
    for r in rows:
        print(f'{sym} {r[0]}: {r[1]} candles')
    print()

# Check for gaps in 15m data
for sym in ['ZECUSDT', 'HYPEUSDT', 'UNIUSDT', 'NEARUSDT']:
    cursor.execute('SELECT timestamp FROM klines WHERE symbol=? AND interval="15m" ORDER BY timestamp', (sym,))
    ts = [row[0] for row in cursor.fetchall()]
    if len(ts) > 1:
        gaps = sum(1 for i in range(1, len(ts)) if ts[i] - ts[i-1] != 15*60*1000)
        print(f'{sym} 15m gaps: {gaps}/{len(ts)}')
    
    # OHLC validity
    cursor.execute('SELECT COUNT(*) FROM klines WHERE symbol=? AND interval="15m" AND (high < low OR high < open OR high < close OR low > open OR low > close)', (sym,))
    invalid = cursor.fetchone()[0]
    print(f'{sym} invalid OHLC: {invalid}')
    print()