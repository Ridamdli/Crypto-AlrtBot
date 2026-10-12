"""Mirror config: all MIRROR_* env vars in one place."""
import os
from dataclasses import dataclass

PARISTON_PORTFOLIO_ID = "17cc19aa-d854-4857-9cca-7f05f21ec2da"


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
    pariston_portfolio_id: str = PARISTON_PORTFOLIO_ID
    email: str = ""
    app_password: str = ""
    portfolio_id: str = ""
    device_id: str = ""
    base_sim: float = 5.0
    max_open: int = 10
    max_risk_pct: float = 0.25
    poll_s: int = 60
    data_dir: str = "mirror_data"
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "MirrorConfig":
        return cls(
            pariston_portfolio_id=os.getenv("PARISTON_PORTFOLIO_ID",
                                            PARISTON_PORTFOLIO_ID),
            email=os.getenv("MIRROR_INVO_EMAIL", ""),
            app_password=os.getenv("MIRROR_INVO_GMAIL_APP_PASSWORD", ""),
            portfolio_id=os.getenv("MIRROR_INVO_PORTFOLIO_ID", ""),
            device_id=os.getenv("MIRROR_INVO_DEVICE_ID", ""),
            base_sim=_f("MIRROR_INVO_SIM_SIZE", 5.0),
            max_open=_i("MIRROR_MAX_OPEN", 10),
            max_risk_pct=_f("MIRROR_MAX_RISK_PCT", 0.25),
            poll_s=max(15, _i("MIRROR_POLL_S", 60)),
            data_dir=os.getenv("MIRROR_DATA_DIR", "mirror_data"),
            dry_run=os.getenv("MIRROR_DRY_RUN", "true").lower() != "false",
        )

    def missing_live(self) -> list:
        """Secrets/IDs required for a LIVE (non-dry) run."""
        need = {
            "MIRROR_INVO_EMAIL": self.email,
            "MIRROR_INVO_GMAIL_APP_PASSWORD": self.app_password,
            "MIRROR_INVO_PORTFOLIO_ID": self.portfolio_id,
            "MIRROR_INVO_DEVICE_ID": self.device_id,
        }
        return [k for k, v in need.items() if not v]
