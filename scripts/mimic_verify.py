"""Pre-live verification: agent authorized? wallet state? Pariston board?
Reads only. Env: HL_AGENT_KEY, HL_WALLET_ADDRESS (+ MIRROR_* for sims).
Prints statuses and JSON facts, never secrets.
"""
import json
import os
import sys

sys.path.insert(0, ".")


def main() -> None:
    agent_key = os.getenv("HL_AGENT_KEY", "")
    wallet = os.getenv("HL_WALLET_ADDRESS", "")
    if not agent_key or not wallet:
        raise SystemExit("need HL_AGENT_KEY + HL_WALLET_ADDRESS")
    from eth_account import Account
    from mirror.hl import HlBridge

    derived = Account.from_key(agent_key).address
    print("agent address derived:", derived[:10] + "…")

    b = HlBridge(agent_key=agent_key, wallet_address=wallet)
    agents = b.public_post("/info", {"type": "extraAgents", "user": wallet})
    print("extraAgents:", json.dumps(agents)[:400])
    auth = any((a.get("address", "") or "").lower() == derived.lower()
               for a in (agents if isinstance(agents, list)
                         else agents.get("agents", [])))
    print("agent authorized:", auth)

    state = b.public_post("/info", {"type": "clearinghouseState", "user": wallet})
    ms = state.get("marginSummary", {})
    print("accountValue:", ms.get("accountValue"),
          "withdrawable:", state.get("withdrawable"))
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        if float(pos.get("szi", 0) or 0) != 0:
            print(f"OPEN {pos.get('coin')} {pos.get('szi')} entry={pos.get('entryPx')} "
                  f"uPnl={pos.get('unrealizedPnl')}")
    print("equity():", b.equity())


if __name__ == "__main__":
    main()
