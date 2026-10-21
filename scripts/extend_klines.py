"""Extend the 15m kline cache for the symbols in the production ledger.

Usage: python scripts/extend_klines.py 2026-10-05 [SYMBOL ...]
Read-only w.r.t. production; writes only the local historical DB.
"""
import json
import sys
import time
from datetime import datetime, timezone

from src.data.binance_client import BinanceFuturesClient
from src.data.historical_store import HistoricalStore
from scripts.download_history import download_klines


def main() -> None:
    start = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    symbols = sys.argv[2:]
    if not symbols:
        with open("prod_evidence/pariston_outcomes.json", encoding="utf-8") as f:
            symbols = sorted({v["symbol"] for v in json.load(f).values()})
    end_ms = int(time.time() * 1000)

    client, store = BinanceFuturesClient(), HistoricalStore()
    for sym in symbols:
        before = store.get_latest_kline_timestamp(sym, "15m")
        download_klines(client, store, sym, "15m", start_ms, end_ms)
        after = store.get_latest_kline_timestamp(sym, "15m")
        print(f"{sym:14} {before} -> {after}")


if __name__ == "__main__":
    main()
