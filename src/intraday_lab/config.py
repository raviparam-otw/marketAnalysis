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
    return tuple(s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip())


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    paper: bool = field(default_factory=lambda: _bool("ALPACA_PAPER", False))

    starting_balance: float = field(default_factory=lambda: _float("STARTING_BALANCE", 100_000))
    model_capital: float = field(default_factory=lambda: _float("MODEL_CAPITAL", 50_000))
    # Legacy experiment fields retained for the historical backtester.
    target_equity: float = field(default_factory=lambda: _float("TARGET_EQUITY", 150_000))
    floor_equity: float = field(default_factory=lambda: _float("FLOOR_EQUITY", 50_000))
    max_exposure: float = field(default_factory=lambda: _float("MAX_EXPOSURE", 50_000))
    risk_per_trade: float = field(default_factory=lambda: _float("RISK_PER_TRADE", 250))
    max_position_notional: float = field(default_factory=lambda: _float("MAX_POSITION_NOTIONAL", 12_500))
    daily_loss_limit: float = field(default_factory=lambda: _float("DAILY_LOSS_LIMIT", 1_000))
    max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MAX_TRADES_PER_DAY", "6")))
    max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2")))
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
    watchlist: tuple[str, ...] = field(default_factory=lambda: _symbols("WATCHLIST", "SPY,QQQ,NVDA,TSLA,AMD,PLTR,AAPL,META,AMZN,MSFT"))

    def validate(self, require_credentials: bool = True) -> None:
        if not self.paper:
            raise RuntimeError("SAFETY LOCK: ALPACA_PAPER must be exactly 'true'.")
        if require_credentials and (not self.api_key or not self.secret_key):
            raise RuntimeError("Missing paper API credentials in .env.")
        if self.model_capital <= 0 or self.model_capital * 2 > self.starting_balance:
            raise ValueError("MODEL_CAPITAL must be positive and two models must fit within STARTING_BALANCE.")
        if self.risk_per_trade <= 0 or self.risk_per_trade > self.model_capital:
            raise ValueError("RISK_PER_TRADE is invalid.")
        if self.max_position_notional <= 0 or self.max_position_notional > self.model_capital:
            raise ValueError("MAX_POSITION_NOTIONAL must fit within MODEL_CAPITAL.")
        if self.daily_loss_limit <= 0 or self.max_trades_per_day <= 0 or self.max_consecutive_losses <= 0:
            raise ValueError("Daily risk controls must be positive.")
        if not (0 < self.stop_loss_pct < self.take_profit_pct < 1):
            raise ValueError("Expected 0 < stop loss < take profit < 1.")
        if self.universe_size <= 0 or self.snapshot_batch_size <= 0 or self.bar_batch_size <= 0:
            raise ValueError("Universe and batch sizes must be positive.")
        if self.min_price <= 0 or self.min_dollar_volume < 0 or not (0 < self.max_spread_pct < 1):
            raise ValueError("Universe filters are invalid.")


settings = Settings()
