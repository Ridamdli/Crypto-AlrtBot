import sqlite3
from datetime import datetime

conn = sqlite3.connect('data_store/historical/historical_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 10 available symbols
available = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'DOTUSDT']

print("=== DATA AVAILABILITY REPORT ===\n")

for sym in available:
    cursor.execute('''
        SELECT interval, 
               MIN(timestamp) as first_ts,
               MAX(timestamp) as last_ts,
               COUNT(*) as count
        FROM klines 
        WHERE symbol=? 
        GROUP BY interval
    ''', (sym,))
    rows = cursor.fetchall()
    for r in rows:
        first_dt = r['first_ts'] / 1000
        last_dt = r['last_ts'] / 1000
        first_str = datetime.fromtimestamp(first_dt).strftime('%Y-%m-%d %H:%M')
        last_str = datetime.fromtimestamp(last_dt).strftime('%Y-%m-%d %H:%M')
        print(f'{sym} {r["interval"]}: {r["count"]} candles, {first_str} to {last_str}')

    # Check for gaps in 15m data
    cursor.execute('SELECT timestamp FROM klines WHERE symbol=? AND interval="15m" ORDER BY timestamp', (sym,))
    ts = [row[0] for row in cursor.fetchall()]
    if len(ts) > 1:
        gaps = sum(1 for i in range(1, len(ts)) if ts[i] - ts[i-1] != 15*60*1000)
        print(f'  15m gaps: {gaps}/{len(ts)}')
    print()

print("\n=== MISSING SYMBOLS ===")
missing = ['ZECUSDT', 'HYPEUSDT', 'UNIUSDT', 'NEARUSDT']
for sym in missing:
    cursor.execute('SELECT COUNT(*) FROM klines WHERE symbol=?', (sym,))
    count = cursor.fetchone()[0]
    print(f'{sym}: {count} candles')