"""One-shot mimic proof against an already-open Pariston position.

Reads only — posts nothing, signs nothing. Exercises the exact live
path (detect -> detail -> brain -> HL order preview -> record preview)
so the only unproven step left is the signature itself.
Env: MIRROR_INVO_EMAIL / MIRROR_INVO_GMAIL_APP_PASSWORD /
     MIRROR_INVO_DEVICE_ID / MIMIC_ACCOUNT_USD / MIMIC_MAX_USD /
     MIMIC_MAX_PCT / MIMIC_MAX_CONCURRENT / MIMIC_MIN_REMAINING_USD.
Usage: python scripts/mimic_proof.py [--coin HYPE]
"""
import json
import os
import sys

sys.path.insert(0, ".")


def main() -> None:
    want = ""
    if "--coin" in sys.argv:
        want = sys.argv[sys.argv.index("--coin") + 1].upper().replace("USDT", "")

    from src.invo.client import InvoClient
    from mirror.auto import MimicConfig, decide_live_size
    from mirror.feed import fetch_open_positions, fetch_setup_detail
    from mirror.hl import HlBridge

    email = os.getenv("MIRROR_INVO_EMAIL", "")
    pw = os.getenv("MIRROR_INVO_GMAIL_APP_PASSWORD", "")
    dev = os.getenv("MIRROR_INVO_DEVICE_ID", "")
    data_dir = os.getenv("MIRROR_DATA_DIR", "mirror_data")
    if not (email and pw):
        raise SystemExit("need MIRROR_INVO_EMAIL + MIRROR_INVO_GMAIL_APP_PASSWORD")

    client = InvoClient(email=email, storage_dir=data_dir)
    client.ensure_auth(email=email, app_password=pw, device_id=dev)
    token = client.access_token or ""
    pf = os.getenv("PARISTON_PORTFOLIO_ID",
                   "17cc19aa-d854-4857-9cca-7f05f21ec2da")
    positions = fetch_open_positions(client, token, pf)
    if want:
        positions = [p for p in positions if p.get("ticker") == want]
    if not positions:
        raise SystemExit("no matching open Pariston position")

    mg = MimicConfig.from_env()
    bridge = HlBridge()  # public reads only; no key needed
    out = []
    for pos in positions:
        detail = {}
        try:
            detail = fetch_setup_detail(client, token, pos["baseId"])
        except Exception as e:
            detail = {"_detail_error": str(e)[:120]}
        margin, reason = decide_live_size(mg.account_usd, 0.0, mg)
        preview = {"margin_usd": margin, "reason": reason}
        if margin is not None:
            coin = pos["ticker"]
            lev = int(pos.get("leverage") or 10)
            try:
                meta = bridge.meta().get(coin, {})
                mid = bridge.mid(coin)
                import math
                dec = int(meta.get("szDecimals", 2))
                size = math.floor(margin * lev / mid * 10 ** dec) / 10 ** dec
                preview.update({
                    "coin": coin, "side": pos.get("side"),
                    "leverage": min(lev, int(meta.get("maxLeverage", lev))),
                    "mid": mid, "notional_usd": round(margin * lev, 2),
                    "size": size, "assetIndex": meta.get("assetIndex"),
                    "min_notional_ok": margin * lev >= 10.0,
                })
            except Exception as e:
                preview["hl_error"] = str(e)[:160]
        out.append({"pariston": pos, "detail_tp_sl": {
            "tp": detail.get("tp1"), "sl": detail.get("stop_loss")},
            "brain": preview})
    print(json.dumps({"account_usd": mg.account_usd, "positions": out},
                     indent=1)[:3000])


if __name__ == "__main__":
    main()
