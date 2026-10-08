import sqlite3
import os
from typing import List, Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)

class HistoricalStore:
    def __init__(self, db_path: str = "data_store/historical/historical_data.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def get_connection(self):
        import threading
        if not hasattr(self, "_local"):
            self._local = threading.local()
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-10000") # 10MB cache
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS klines (
                    symbol TEXT,
                    interval TEXT,
                    timestamp INTEGER,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (symbol, interval, timestamp)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS open_interest (
                    symbol TEXT,
                    period TEXT,
                    timestamp INTEGER,
                    sumOpenInterestValue REAL,
                    PRIMARY KEY (symbol, period, timestamp)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS funding_rate (
                    symbol TEXT,
                    timestamp INTEGER,
                    fundingRate REAL,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            
            # Indexes for faster ranges
            conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_sym_int_time ON klines(symbol, interval, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_oi_sym_per_time ON open_interest(symbol, period, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fr_sym_time ON funding_rate(symbol, timestamp)")

    def insert_klines(self, symbol: str, interval: str, klines: List[List[Any]]):
        """Binance raw format: [ts, open, high, low, close, volume, ...]"""
        if not klines: return
        query = """
            INSERT OR IGNORE INTO klines (symbol, interval, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (symbol, interval, int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
            for k in klines
        ]
        with self.get_connection() as conn:
            conn.executemany(query, rows)

    def insert_open_interest(self, symbol: str, period: str, data: List[Dict[str, Any]]):
        if not data: return
        query = """
            INSERT OR IGNORE INTO open_interest (symbol, period, timestamp, sumOpenInterestValue)
            VALUES (?, ?, ?, ?)
        """
        rows = [
            (symbol, period, int(d["timestamp"]), float(d["sumOpenInterestValue"]))
            for d in data if "timestamp" in d and "sumOpenInterestValue" in d
        ]
        with self.get_connection() as conn:
            conn.executemany(query, rows)

    def insert_funding_rate(self, symbol: str, data: List[Dict[str, Any]]):
        if not data: return
        query = """
            INSERT OR IGNORE INTO funding_rate (symbol, timestamp, fundingRate)
            VALUES (?, ?, ?)
        """
        rows = [
            (symbol, int(d["fundingTime"]), float(d["fundingRate"]))
            for d in data if "fundingTime" in d and "fundingRate" in d
        ]
        with self.get_connection() as conn:
            conn.executemany(query, rows)

    def get_latest_kline_timestamp(self, symbol: str, interval: str) -> Optional[int]:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT MAX(timestamp) FROM klines WHERE symbol=? AND interval=?", (symbol, interval))
            res = cur.fetchone()
            return res[0] if res and res[0] else None
