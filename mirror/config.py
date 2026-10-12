"""Mirror config: all MIRROR_* env vars in one place.

The bot is detect-only: it needs an Invo login (to watch Pariston) and
nothing else. No destination portfolio, no trading keys — there is simply
no code path that could move money.
"""
import os
from dataclasses import dataclass

from mirror.feed import PARISTON_PORTFOLIO_ID as _DEFAULT_PARISTON


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass
class MirrorConfig:
    pariston_portfolio_id: str = _DEFAULT_PARISTON
    email: str = ""
    app_password: str = ""
    device_id: str = ""
    # Confirm-time sizing knobs for the operator flow (brain.decide_size).
    base_sim: float = 5.0
    max_open: int = 10
    max_risk_pct: float = 0.25
    poll_s: int = 60
    data_dir: str = "mirror_data"

    @classmethod
    def from_env(cls) -> "MirrorConfig":
        return cls(
            pariston_portfolio_id=os.getenv("PARISTON_PORTFOLIO_ID",
                                            _DEFAULT_PARISTON),
            email=os.getenv("MIRROR_INVO_EMAIL", ""),
            app_password=os.getenv("MIRROR_INVO_GMAIL_APP_PASSWORD", ""),
            device_id=os.getenv("MIRROR_INVO_DEVICE_ID", ""),
            base_sim=_f("MIRROR_INVO_SIM_SIZE", 5.0),
            max_open=_i("MIRROR_MAX_OPEN", 10),
            max_risk_pct=_f("MIRROR_MAX_RISK_PCT", 0.25),
            poll_s=max(15, _i("MIRROR_POLL_S", 60)),
            data_dir=os.getenv("MIRROR_DATA_DIR", "mirror_data"),
        )

    def missing_auth(self) -> list:
        """Login material required to watch (reads only, never trades)."""
        need = {
            "MIRROR_INVO_EMAIL": self.email,
            "MIRROR_INVO_GMAIL_APP_PASSWORD": self.app_password,
            "MIRROR_INVO_DEVICE_ID": self.device_id,
        }
        return [k for k, v in need.items() if not v]
