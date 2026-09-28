from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() == "true"


def _symbols(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        symbol.strip().upper()
        for symbol in os.getenv(name, default).split(",")
        if symbol.strip()
    )


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
    trade_log_dir: str = field(
        default_factory=lambda: os.getenv("TRADE_LOG_DIR", str(ROOT / "trade-data"))
    )
    dynamic_universe: bool = field(default_factory=lambda: _bool("DYNAMIC_UNIVERSE", True))
    universe_refresh_minutes: int = field(
        default_factory=lambda: int(os.getenv("UNIVERSE_REFRESH_MINUTES", "30"))
    )
    universe_size: int = field(default_factory=lambda: int(os.getenv("UNIVERSE_SIZE", "100")))
    snapshot_batch_size: int = field(
        default_factory=lambda: int(os.getenv("SNAPSHOT_BATCH_SIZE", "200"))
    )
    bar_batch_size: int = field(default_factory=lambda: int(os.getenv("BAR_BATCH_SIZE", "20")))
    min_price: float = field(default_factory=lambda: _float("MIN_PRICE", 5.0))
    min_dollar_volume: float = field(
        default_factory=lambda: _float("MIN_DOLLAR_VOLUME", 1_000_000)
    )
    max_spread_pct: float = field(default_factory=lambda: _float("MAX_SPREAD_PCT", 0.005))
    excluded_symbols: tuple[str, ...] = field(
        default_factory=lambda: _symbols("EXCLUDED_SYMBOLS")
    )
    watchlist: tuple[str, ...] = field(
        default_factory=lambda: _symbols(
            "WATCHLIST", "SPY,QQQ,NVDA,TSLA,AMD,PLTR,AAPL,META,AMZN,MSFT"
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
        if self.universe_size <= 0 or self.snapshot_batch_size <= 0 or self.bar_batch_size <= 0:
            raise ValueError("Universe size and data batch sizes must be positive.")
        if self.universe_refresh_minutes <= 0:
            raise ValueError("Universe refresh interval must be positive.")
        if self.min_price <= 0 or self.min_dollar_volume < 0:
            raise ValueError("Universe price and liquidity limits are invalid.")
        if not (0 < self.max_spread_pct < 1):
            raise ValueError("MAX_SPREAD_PCT must be between 0 and 1.")


settings = Settings()
