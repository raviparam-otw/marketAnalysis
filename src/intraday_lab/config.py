from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    paper: bool = field(
        default_factory=lambda: os.getenv("ALPACA_PAPER", "").strip().lower() == "true"
    )
    starting_balance: float = field(default_factory=lambda: _float("STARTING_BALANCE", 100_000))
    target_equity: float = field(default_factory=lambda: _float("TARGET_EQUITY", 150_000))
    floor_equity: float = field(default_factory=lambda: _float("FLOOR_EQUITY", 50_000))
    max_exposure: float = field(default_factory=lambda: _float("MAX_EXPOSURE", 100_000))
    daily_loss_limit: float = field(default_factory=lambda: _float("DAILY_LOSS_LIMIT", 5_000))
    stop_loss_pct: float = field(default_factory=lambda: _float("STOP_LOSS_PCT", 0.025))
    take_profit_pct: float = field(default_factory=lambda: _float("TAKE_PROFIT_PCT", 0.05))
    trail_trigger_pct: float = field(default_factory=lambda: _float("TRAIL_TRIGGER_PCT", 0.03))
    trail_distance_pct: float = field(default_factory=lambda: _float("TRAIL_DISTANCE_PCT", 0.015))
    relative_volume_min: float = field(default_factory=lambda: _float("RELATIVE_VOLUME_MIN", 1.5))
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "60")))
    watchlist: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip().upper()
            for s in os.getenv(
                "WATCHLIST", "SPY,QQQ,NVDA,TSLA,AMD,PLTR,AAPL,META,AMZN,MSFT"
            ).split(",")
            if s.strip()
        )
    )

    def validate(self, require_credentials: bool = True) -> None:
        if not self.paper:
            raise RuntimeError("SAFETY LOCK: ALPACA_PAPER must be exactly 'true'.")
        if require_credentials and (not self.api_key or not self.secret_key):
            raise RuntimeError("Missing paper API credentials in .env.")
        if not (0 < self.stop_loss_pct < self.take_profit_pct < 1):
            raise ValueError("Expected 0 < stop loss < take profit < 1.")
        if not (self.floor_equity < self.starting_balance < self.target_equity):
            raise ValueError("Expected floor < starting balance < target.")
        if self.max_exposure <= 0 or self.daily_loss_limit <= 0:
            raise ValueError("Exposure and loss limits must be positive.")


settings = Settings()
