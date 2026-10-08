import sqlite3
from datetime import datetime

conn = sqlite3.connect('data_store/historical/historical_data.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

missing = ['ZECUSDT', 'HYPEUSDT', 'UNIUSDT', 'NEARUSDT']

for sym in missing:
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
    print()