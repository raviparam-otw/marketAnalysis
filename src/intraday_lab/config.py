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
    action_day_mode: bool = field(default_factory=lambda: _bool("ACTION_DAY_MODE", True))

    # Historical/backtest baseline. The live A/B engine does NOT assume this balance.
    starting_balance: float = field(default_factory=lambda: _float("STARTING_BALANCE", 100_000))

    # Legacy fallback only. Live sessions freeze equal A/B/C allocations from Alpaca equity at Start.
    model_capital: float = field(default_factory=lambda: _float("MODEL_CAPITAL", 50_000))

    # Risk scales with each model's frozen session allocation.
    risk_per_trade_pct: float = field(default_factory=lambda: _float("RISK_PER_TRADE_PCT", 0.005))
    max_position_pct: float = field(default_factory=lambda: _float("MAX_POSITION_PCT", 0.25))
    daily_loss_pct: float = field(default_factory=lambda: _float("DAILY_LOSS_PCT", 0.02))
    # PAPER action-day profile: keep a global account cap, then enforce per-model caps below.
    max_account_exposure_pct: float = field(default_factory=lambda: _float("MAX_ACCOUNT_EXPOSURE_PCT", 0.80))
    max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MAX_TRADES_PER_DAY", "6")))
    max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2")))

    # Model A remains the control, but can deploy several positions and cut failed
    # breakouts faster than the shared generic stop logic.
    model_a_max_open_positions: int = field(default_factory=lambda: int(os.getenv("MODEL_A_MAX_OPEN_POSITIONS", "3")))
    model_a_max_exposure_pct: float = field(default_factory=lambda: _float("MODEL_A_MAX_EXPOSURE_PCT", 0.65))
    model_a_weakness_exit_pct: float = field(default_factory=lambda: _float("MODEL_A_WEAKNESS_EXIT_PCT", 0.006))
    model_a_failed_breakout_minutes: int = field(default_factory=lambda: int(os.getenv("MODEL_A_FAILED_BREAKOUT_MINUTES", "5")))
    model_a_failed_breakout_max_gain_pct: float = field(default_factory=lambda: _float("MODEL_A_FAILED_BREAKOUT_MAX_GAIN_PCT", 0.003))
    model_a_failed_breakout_exit_pct: float = field(default_factory=lambda: _float("MODEL_A_FAILED_BREAKOUT_EXIT_PCT", 0.002))
    model_a_breakout_buffer_pct: float = field(default_factory=lambda: _float("MODEL_A_BREAKOUT_BUFFER_PCT", 0.0015))
    model_a_confirmation_bars: int = field(default_factory=lambda: int(os.getenv("MODEL_A_CONFIRMATION_BARS", "2")))
    model_a_max_extension_from_or_pct: float = field(default_factory=lambda: _float("MODEL_A_MAX_EXTENSION_FROM_OR_PCT", 0.06))
    model_a_max_extension_from_vwap_pct: float = field(default_factory=lambda: _float("MODEL_A_MAX_EXTENSION_FROM_VWAP_PCT", 0.04))

    # Model B is intentionally aggressive for PAPER experimentation.
    model_b_risk_per_trade_pct: float = field(default_factory=lambda: _float("MODEL_B_RISK_PER_TRADE_PCT", 0.01))
    model_b_max_position_pct: float = field(default_factory=lambda: _float("MODEL_B_MAX_POSITION_PCT", 0.20))
    model_b_daily_loss_pct: float = field(default_factory=lambda: _float("MODEL_B_DAILY_LOSS_PCT", 0.05))
    model_b_max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MODEL_B_MAX_TRADES_PER_DAY", "20")))
    model_b_max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("MODEL_B_MAX_CONSECUTIVE_LOSSES", "5")))
    model_b_max_open_positions: int = field(default_factory=lambda: int(os.getenv("MODEL_B_MAX_OPEN_POSITIONS", "5")))
    model_b_max_exposure_pct: float = field(default_factory=lambda: _float("MODEL_B_MAX_EXPOSURE_PCT", 0.90))
    model_b_max_entries_per_cycle: int = field(default_factory=lambda: int(os.getenv("MODEL_B_MAX_ENTRIES_PER_CYCLE", "3")))

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
    break_even_trigger_pct: float = field(default_factory=lambda: _float("BREAK_EVEN_TRIGGER_PCT", 0.0075))
    break_even_lock_pct: float = field(default_factory=lambda: _float("BREAK_EVEN_LOCK_PCT", 0.001))
    early_trail_trigger_pct: float = field(default_factory=lambda: _float("EARLY_TRAIL_TRIGGER_PCT", 0.01))
    early_trail_distance_pct: float = field(default_factory=lambda: _float("EARLY_TRAIL_DISTANCE_PCT", 0.006))
    stagnation_minutes: int = field(default_factory=lambda: int(os.getenv("STAGNATION_MINUTES", "45")))
    stagnation_min_gain_pct: float = field(default_factory=lambda: _float("STAGNATION_MIN_GAIN_PCT", 0.005))

    relative_volume_min: float = field(default_factory=lambda: _float("RELATIVE_VOLUME_MIN", 1.5))
    momentum_rvol_min: float = field(default_factory=lambda: _float("MOMENTUM_RVOL_MIN", 1.25))
    momentum_gap_min_pct: float = field(default_factory=lambda: _float("MOMENTUM_GAP_MIN_PCT", 1.5))
    momentum_change_min_pct: float = field(default_factory=lambda: _float("MOMENTUM_CHANGE_MIN_PCT", 2.0))
    momentum_max_price: float = field(default_factory=lambda: _float("MOMENTUM_MAX_PRICE", 50.0))
    momentum_require_news: bool = field(default_factory=lambda: _bool("MOMENTUM_REQUIRE_NEWS", False))
    momentum_min_score: float = field(default_factory=lambda: _float("MOMENTUM_MIN_SCORE", 5.0))

    # Model C - finance-native adaptive stack.
    # Fin-R1 is the primary reasoning LLM. Kronos contributes K-line forecasts and
    # FinBERT contributes finance-specific headline sentiment.
    model_c_enabled: bool = field(default_factory=lambda: _bool("MODEL_C_ENABLED", True))
    model_c_execution_enabled: bool = field(default_factory=lambda: _bool("MODEL_C_EXECUTION_ENABLED", True))
    model_c_llm_base_url: str = field(default_factory=lambda: os.getenv("MODEL_C_LLM_BASE_URL", ""))
    model_c_llm_model: str = field(default_factory=lambda: os.getenv("MODEL_C_LLM_MODEL", "Fin-R1"))
    model_c_llm_api_key: str = field(default_factory=lambda: os.getenv("MODEL_C_LLM_API_KEY", ""))
    model_c_llm_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MODEL_C_LLM_TIMEOUT_SECONDS", "60")))
    model_c_decision_interval_seconds: int = field(default_factory=lambda: int(os.getenv("MODEL_C_DECISION_INTERVAL_SECONDS", "60")))
    model_c_shortlist_size: int = field(default_factory=lambda: int(os.getenv("MODEL_C_SHORTLIST_SIZE", "5")))
    model_c_min_confidence: float = field(default_factory=lambda: _float("MODEL_C_MIN_CONFIDENCE", 0.68))
    model_c_require_full_stack: bool = field(default_factory=lambda: _bool("MODEL_C_REQUIRE_FULL_STACK", True))

    model_c_finbert_enabled: bool = field(default_factory=lambda: _bool("MODEL_C_FINBERT_ENABLED", True))
    model_c_finbert_model: str = field(default_factory=lambda: os.getenv("MODEL_C_FINBERT_MODEL", "ProsusAI/finbert"))

    model_c_kronos_enabled: bool = field(default_factory=lambda: _bool("MODEL_C_KRONOS_ENABLED", True))
    model_c_kronos_repo_path: str = field(
        default_factory=lambda: os.getenv("MODEL_C_KRONOS_REPO_PATH", str(ROOT / ".models" / "Kronos"))
    )
    model_c_kronos_model: str = field(default_factory=lambda: os.getenv("MODEL_C_KRONOS_MODEL", "NeoQuasar/Kronos-small"))
    model_c_kronos_tokenizer: str = field(default_factory=lambda: os.getenv("MODEL_C_KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base"))
    model_c_kronos_device: str = field(default_factory=lambda: os.getenv("MODEL_C_KRONOS_DEVICE", "cpu"))
    model_c_kronos_lookback: int = field(default_factory=lambda: int(os.getenv("MODEL_C_KRONOS_LOOKBACK", "120")))
    model_c_kronos_pred_len: int = field(default_factory=lambda: int(os.getenv("MODEL_C_KRONOS_PRED_LEN", "5")))
    model_c_risk_per_trade_pct: float = field(default_factory=lambda: _float("MODEL_C_RISK_PER_TRADE_PCT", 0.0075))
    model_c_max_position_pct: float = field(default_factory=lambda: _float("MODEL_C_MAX_POSITION_PCT", 0.25))
    model_c_daily_loss_pct: float = field(default_factory=lambda: _float("MODEL_C_DAILY_LOSS_PCT", 0.03))
    model_c_max_trades_per_day: int = field(default_factory=lambda: int(os.getenv("MODEL_C_MAX_TRADES_PER_DAY", "10")))
    model_c_max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("MODEL_C_MAX_CONSECUTIVE_LOSSES", "3")))
    model_c_max_open_positions: int = field(default_factory=lambda: int(os.getenv("MODEL_C_MAX_OPEN_POSITIONS", "3")))
    model_c_max_exposure_pct: float = field(default_factory=lambda: _float("MODEL_C_MAX_EXPOSURE_PCT", 0.75))

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

    def model_profile(self, model: str) -> dict:
        model = model.upper()
        if model == "B":
            return {
                "risk_per_trade_pct": self.model_b_risk_per_trade_pct,
                "max_position_pct": self.model_b_max_position_pct,
                "daily_loss_pct": self.model_b_daily_loss_pct,
                "max_trades_per_day": self.model_b_max_trades_per_day,
                "max_consecutive_losses": self.model_b_max_consecutive_losses,
                "max_open_positions": self.model_b_max_open_positions,
                "max_exposure_pct": self.model_b_max_exposure_pct,
                "max_entries_per_cycle": self.model_b_max_entries_per_cycle,
            }
        if model == "C":
            return {
                "risk_per_trade_pct": self.model_c_risk_per_trade_pct,
                "max_position_pct": self.model_c_max_position_pct,
                "daily_loss_pct": self.model_c_daily_loss_pct,
                "max_trades_per_day": self.model_c_max_trades_per_day,
                "max_consecutive_losses": self.model_c_max_consecutive_losses,
                "max_open_positions": self.model_c_max_open_positions,
                "max_exposure_pct": self.model_c_max_exposure_pct,
                "max_entries_per_cycle": 1,
            }
        return {
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_position_pct": self.max_position_pct,
            "daily_loss_pct": self.daily_loss_pct,
            "max_trades_per_day": self.max_trades_per_day,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_open_positions": self.model_a_max_open_positions,
            "max_exposure_pct": self.model_a_max_exposure_pct,
            "max_entries_per_cycle": 2,
        }

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

        for model in ("A", "B", "C"):
            profile = self.model_profile(model)
            for key in ("risk_per_trade_pct", "max_position_pct", "daily_loss_pct", "max_exposure_pct"):
                if not (0 < float(profile[key]) <= 1):
                    raise ValueError(f"{model} {key} must be greater than 0 and no more than 1.")
            if profile["risk_per_trade_pct"] >= profile["daily_loss_pct"]:
                raise ValueError(f"{model} risk per trade must be below its daily loss limit.")
            for key in ("max_trades_per_day", "max_consecutive_losses", "max_open_positions", "max_entries_per_cycle"):
                if int(profile[key]) <= 0:
                    raise ValueError(f"{model} {key} must be positive.")

        if self.model_c_decision_interval_seconds <= 0 or self.model_c_shortlist_size <= 0:
            raise ValueError("Model C decision interval and shortlist size must be positive.")
        if not (0 <= self.model_c_min_confidence <= 1):
            raise ValueError("MODEL_C_MIN_CONFIDENCE must be between 0 and 1.")
        if self.model_c_llm_timeout_seconds <= 0:
            raise ValueError("MODEL_C_LLM_TIMEOUT_SECONDS must be positive.")
        if self.model_c_kronos_lookback <= 0 or self.model_c_kronos_pred_len <= 0:
            raise ValueError("Model C Kronos lookback and prediction length must be positive.")

        if not (0 < self.model_a_weakness_exit_pct < 1):
            raise ValueError("MODEL_A_WEAKNESS_EXIT_PCT must be between 0 and 1.")
        if self.model_a_failed_breakout_minutes <= 0:
            raise ValueError("MODEL_A_FAILED_BREAKOUT_MINUTES must be positive.")
        if not (0 <= self.model_a_failed_breakout_max_gain_pct < 1):
            raise ValueError("MODEL_A_FAILED_BREAKOUT_MAX_GAIN_PCT must be between 0 and 1.")
        if not (0 < self.model_a_failed_breakout_exit_pct < 1):
            raise ValueError("MODEL_A_FAILED_BREAKOUT_EXIT_PCT must be between 0 and 1.")
        if not (0 <= self.model_a_breakout_buffer_pct < 0.05):
            raise ValueError("MODEL_A_BREAKOUT_BUFFER_PCT must be between 0 and 0.05.")
        if self.model_a_confirmation_bars not in (1, 2, 3):
            raise ValueError("MODEL_A_CONFIRMATION_BARS must be 1, 2, or 3.")
        if not (0 < self.model_a_max_extension_from_or_pct < 1):
            raise ValueError("MODEL_A_MAX_EXTENSION_FROM_OR_PCT must be between 0 and 1.")
        if not (0 < self.model_a_max_extension_from_vwap_pct < 1):
            raise ValueError("MODEL_A_MAX_EXTENSION_FROM_VWAP_PCT must be between 0 and 1.")

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
