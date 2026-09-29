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
    paper: bool = field(default_factory=lambda: _bool("ALPACA_PAPER", False))

    # Historical/backtest baseline. The live A/B engine does NOT assume this balance.
    starting_balance: float = field(default_factory=lambda: _float("STARTING_BALANCE", 100_000))

    # Legacy fallback only. Live sessions freeze 50/50 allocations from Alpaca equity at Start.
    model_capital: float = field(default_factory=lambda: _float("MODEL_CAPITAL", 50_000))

    # Risk scales with each model's frozen session allocation.
    risk_per_trade_pct: float = field(default_factory=lambda: _float("RISK_PER_TRADE_PCT", 0.005))
    max_position_pct: float = field(default_factory=lambda: _float("MAX_POSITION_PCT", 0.25))
    daily_loss_pct: float = field(default_factory=lambda: _float("DAILY_LOSS_PCT", 0.02))
    max_account_exposure_pct: float = field(default_factory=lambda: _float("MAX_ACCOUNT_EXPOSURE_PCT", 0.50))
    max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MAX_TRADES_PER_DAY", "6")))
    max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2")))

    # Legacy absolute values remain available for the historical backtester/compatibility,
    # but the live engine derives its dollar limits from the percentages above.
    target_equity: float = field(default_factory=lambda: _float("TARGET_EQUITY", 150_000))
    floor_equity: float = field(default_factory=lambda: _float("FLOOR_EQUITY", 50_000))
    max_exposure: float = field(default_factory=lambda: _float("MAX_EXPOSURE", 50_000))
    risk_per_trade: float = field(default_factory=lambda: _float("RISK_PER_TRADE", 250))
    max_position_notional: float = field(default_factory=lambda: _float("MAX_POSITION_NOTIONAL", 12_500))
    daily_loss_limit: float = field(default_factory=lambda: _float("DAILY_LOSS_LIMIT", 1_000))

    stop_loss_pct: float = field(default_factory=lambda: _float("STOP_LOSS_PCT", 0.025))
    take_profit_pct: float = field(default_factory=lambda: _float("TAKE_PROFIT_PCT", 0.05))
    trail_trigger_pct: float = field(default_factory=lambda: _float("TRAIL_TRIGGER_PCT", 0.03))
    trail_distance_pct: float = field(default_factory=lambda: _float("TRAIL_DISTANCE_PCT", 0.015))

    relative_volume_min: float = field(default_factory=lambda: _float("RELATIVE_VOLUME_MIN", 1.5))
    momentum_rvol_min: float = field(default_factory=lambda: _float("MOMENTUM_RVOL_MIN", 2.0))
    momentum_gap_min_pct: float = field(default_factory=lambda: _float("MOMENTUM_GAP_MIN_PCT", 4.0))
    momentum_change_min_pct: float = field(default_factory=lambda: _float("MOMENTUM_CHANGE_MIN_PCT", 5.0))
    momentum_max_price: float = field(default_factory=lambda: _float("MOMENTUM_MAX_PRICE", 30.0))
    momentum_require_news: bool = field(default_factory=lambda: _bool("MOMENTUM_REQUIRE_NEWS", True))

    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "10")))
    trade_log_dir: str = field(default_factory=lambda: os.getenv("TRADE_LOG_DIR", str(ROOT / "trade-data")))
    dynamic_universe: bool = field(default_factory=lambda: _bool("DYNAMIC_UNIVERSE", True))
    universe_refresh_minutes: int = field(default_factory=lambda: int(os.getenv("UNIVERSE_REFRESH_MINUTES", "10")))
    universe_size: int = field(default_factory=lambda: int(os.getenv("UNIVERSE_SIZE", "150")))
    snapshot_batch_size: int = field(default_factory=lambda: int(os.getenv("SNAPSHOT_BATCH_SIZE", "200")))
    bar_batch_size: int = field(default_factory=lambda: int(os.getenv("BAR_BATCH_SIZE", "20")))
    min_price: float = field(default_factory=lambda: _float("MIN_PRICE", 1.0))
    min_dollar_volume: float = field(default_factory=lambda: _float("MIN_DOLLAR_VOLUME", 1_000_000))
    max_spread_pct: float = field(default_factory=lambda: _float("MAX_SPREAD_PCT", 0.01))
    excluded_symbols: tuple[str, ...] = field(default_factory=lambda: _symbols("EXCLUDED_SYMBOLS"))
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

        pct_fields = {
            "RISK_PER_TRADE_PCT": self.risk_per_trade_pct,
            "MAX_POSITION_PCT": self.max_position_pct,
            "DAILY_LOSS_PCT": self.daily_loss_pct,
            "MAX_ACCOUNT_EXPOSURE_PCT": self.max_account_exposure_pct,
        }
        for name, value in pct_fields.items():
            if not (0 < value <= 1):
                raise ValueError(f"{name} must be greater than 0 and no more than 1.")
        if self.risk_per_trade_pct >= self.daily_loss_pct:
            raise ValueError("RISK_PER_TRADE_PCT must be below DAILY_LOSS_PCT.")
        if self.max_trades_per_day <= 0 or self.max_consecutive_losses <= 0:
            raise ValueError("Daily trade-count controls must be positive.")

        if not (0 < self.stop_loss_pct < self.take_profit_pct < 1):
            raise ValueError("Expected 0 < stop loss < take profit < 1.")
        if self.starting_balance <= 0:
            raise ValueError("STARTING_BALANCE must be positive.")
        if not (self.floor_equity < self.starting_balance < self.target_equity):
            raise ValueError("Expected floor < starting balance < target for backtest defaults.")
        if self.max_exposure <= 0:
            raise ValueError("MAX_EXPOSURE must be positive.")
        if self.universe_size <= 0 or self.snapshot_batch_size <= 0 or self.bar_batch_size <= 0:
            raise ValueError("Universe and data batch sizes must be positive.")
        if self.universe_refresh_minutes <= 0:
            raise ValueError("Universe refresh interval must be positive.")
        if self.min_price <= 0 or self.min_dollar_volume < 0:
            raise ValueError("Universe price and liquidity limits are invalid.")
        if not (0 < self.max_spread_pct < 1):
            raise ValueError("MAX_SPREAD_PCT must be between 0 and 1.")


settings = Settings()
