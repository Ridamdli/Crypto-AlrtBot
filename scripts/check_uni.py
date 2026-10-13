"""Read-only UNI check: Pariston's live TP/SL vs our resting triggers.

Prints ONLY coin levels and ledger state — never tokens or secrets.
Run via: railway run --service pariston-mimic python scripts/check_uni.py
"""
import os
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror.auto import PARISTON_PORTFOLIO_ID
from mirror.config import MirrorConfig
from mirror.feed import fetch_source_levels
from mirror.store import MirrorStore
from src.invo.client import InvoClient


def main() -> None:
    mc = MirrorConfig.from_env()
    client = InvoClient(email=mc.email, storage_dir=mc.data_dir)
    client.ensure_auth(email=mc.email, app_password=mc.app_password,
                       device_id=mc.device_id)
    token = client.access_token or ""

    snap = client.list_paper_investments(token, PARISTON_PORTFOLIO_ID)
    invs = snap.get("investments") if isinstance(snap, dict) else []
    uni_rows = [i for i in invs if isinstance(i, dict)
                 and str(i.get("ticker") or "").upper() in ("UNI", "UNIUSDT")]
    print(f"pariston_board_UNI_open={len(uni_rows)}")
    for i in uni_rows:
        print(f"board ticker={i.get('ticker')} side="
              f"{'LONG' if i.get('directionLong') else 'SHORT'} "
              f"lev={i.get('leverage')} entry={i.get('entryPrice')} "
              f"baseId={i.get('baseId')}")

    store = MirrorStore(os.getenv("MIRROR_DATA_DIR", mc.data_dir))
    ours = {}
    for e in store.recent_audit(5000):
        d = e.get("detail") or {}
        if d.get("coin") != "UNI":
            continue
        if e.get("event") == "mimicked":
            ours = {"margin": d.get("margin_usd"), "source": d.get("source"),
                    "side": d.get("side"), "avg_px": d.get("avg_px")}
        elif e.get("event") == "tpsl_placed":
            ours.update({"tp": d.get("tp"), "sl": d.get("sl")})
        elif e.get("event") == "mimic_closed":
            ours = {"closed": d.get("note", True)}
    print(f"our_UNI_ledger={ours}")

    for i in uni_rows:
        lv = fetch_source_levels(client, token, str(i.get("baseId") or ""))
        print(f"source_levels ticker={lv.get('ticker')} side={lv.get('side')} "
              f"tp={lv.get('tp')} sl={lv.get('stop_loss')} "
              f"entry={lv.get('entry')} is_open={lv.get('is_open')}")


if __name__ == "__main__":
    main()
