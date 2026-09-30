from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings


EASTERN = ZoneInfo("America/New_York")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float = 0.0
    notional: float = 0.0
    dollars_at_risk: float = 0.0


class RiskManager:
    """Per-model risk ledger whose dollar limits scale from frozen session capital."""

    def __init__(self, config: Settings, model: str = "A", capital: float | None = None) -> None:
        self.config = config
        self.model = model
        self.session_capital = float(capital or 0.0)
        self.session_date = None
        self.realized_pl = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0

    def configure_session_capital(self, capital: float) -> None:
        if capital <= 0:
            raise ValueError("Session capital must be positive.")
        self.session_capital = float(capital)

    @property
    def capital(self) -> float:
        if self.session_capital > 0:
            return self.session_capital
        # Compatibility fallback for tests/backtest helpers. Live TradingEngine always configures this.
        return float(self.config.model_capital or (self.config.starting_balance / 2.0))

    @property
    def profile(self) -> dict:
        return self.config.model_profile(self.model)

    @property
    def risk_budget(self) -> float:
        return round(self.capital * float(self.profile["risk_per_trade_pct"]), 2)

    @property
    def max_position_notional(self) -> float:
        return round(self.capital * float(self.profile["max_position_pct"]), 2)

    @property
    def daily_loss_limit(self) -> float:
        return round(self.capital * float(self.profile["daily_loss_pct"]), 2)

    @property
    def max_trades_per_day(self) -> int:
        return int(self.profile["max_trades_per_day"])

    @property
    def max_consecutive_losses(self) -> int:
        return int(self.profile["max_consecutive_losses"])

    @property
    def max_open_positions(self) -> int:
        return int(self.profile["max_open_positions"])

    @property
    def max_exposure(self) -> float:
        return round(self.capital * float(self.profile["max_exposure_pct"]), 2)

    @property
    def max_entries_per_cycle(self) -> int:
        return int(self.profile["max_entries_per_cycle"])

    @property
    def model_equity(self) -> float:
        return max(0.0, self.capital + self.realized_pl)

    @property
    def remaining_daily_loss(self) -> float:
        return max(0.0, self.daily_loss_limit + min(0.0, self.realized_pl))

    def reset_session_if_needed(self, now: datetime | None = None) -> None:
        today = (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
        if self.session_date != today:
            self.session_date = today
            self.realized_pl = 0.0
            self.trades_today = 0
            self.wins_today = 0
            self.losses_today = 0
            self.consecutive_losses = 0

    def load_performance(
        self,
        *,
        realized_pl: float,
        closed_trades: int,
        wins: int,
        losses: int,
        consecutive_losses: int,
        now: datetime | None = None,
    ) -> None:
        self.reset_session_if_needed(now)
        self.realized_pl = float(realized_pl)
        self.trades_today = int(closed_trades)
        self.wins_today = int(wins)
        self.losses_today = int(losses)
        self.consecutive_losses = int(consecutive_losses)

    def entry_check(
        self,
        entry_price: float,
        stop_price: float,
        has_position: bool,
        now: datetime | None = None,
        *,
        available_cash: float | None = None,
        global_room: float | None = None,
        model_room: float | None = None,
    ) -> RiskDecision:
        self.reset_session_if_needed(now)
        if has_position:
            return RiskDecision(False, "Model already has an open position")
        if self.trades_today >= self.max_trades_per_day:
            return RiskDecision(False, "Maximum trades per day reached")
        if self.consecutive_losses >= self.max_consecutive_losses:
            return RiskDecision(False, "Consecutive-loss limit reached")
        if self.realized_pl <= -self.daily_loss_limit:
            return RiskDecision(False, "Daily model loss limit reached")

        risk_per_share = entry_price - stop_price
        if entry_price <= 0 or risk_per_share <= 0:
            return RiskDecision(False, "Invalid entry/stop relationship")

        by_risk = self.risk_budget / risk_per_share
        notional_room = min(self.max_position_notional, self.model_equity)
        if available_cash is not None:
            notional_room = min(notional_room, max(0.0, float(available_cash)))
        if global_room is not None:
            notional_room = min(notional_room, max(0.0, float(global_room)))
        if model_room is not None:
            notional_room = min(notional_room, max(0.0, float(model_room)))
        by_notional = notional_room / entry_price

        qty = max(0.0, min(by_risk, by_notional))
        if qty < 0.001:
            return RiskDecision(False, "No portfolio or global exposure room available")

        notional = qty * entry_price
        return RiskDecision(
            True,
            "Risk checks passed",
            quantity=qty,
            notional=notional,
            dollars_at_risk=qty * risk_per_share,
        )

    def record_closed_trade(self, realized_pl: float, now: datetime | None = None) -> None:
        self.reset_session_if_needed(now)
        self.trades_today += 1
        self.realized_pl += realized_pl
        if realized_pl < 0:
            self.losses_today += 1
            self.consecutive_losses += 1
        else:
            if realized_pl > 0:
                self.wins_today += 1
            self.consecutive_losses = 0

    def exit_reason(
        self,
        entry: float,
        current: float,
        high: float,
        stop_price: float,
        *,
        entered_at: datetime | None = None,
        now: datetime | None = None,
    ) -> str | None:
        if current <= stop_price:
            return "technical_stop"

        pnl_pct = (current - entry) / entry
        high_pct = (high - entry) / entry

        # Model A is the breakout control. Today's paper behavior showed that a
        # breakout that immediately loses momentum should be cut before waiting
        # for the wider shared stop. This does not affect Models B or C.
        if self.model.upper() == "A":
            current_time = now or datetime.now(EASTERN)
            elapsed_minutes = (
                (current_time - entered_at).total_seconds() / 60
                if entered_at is not None
                else 0.0
            )
            weakness_price = entry * (1 - self.config.model_a_weakness_exit_pct)
            if current <= weakness_price + 1e-9:
                return "model_a_weakness_exit"
            if (
                entered_at is not None
                and elapsed_minutes >= self.config.model_a_failed_breakout_minutes
                and high_pct < self.config.model_a_failed_breakout_max_gain_pct
                and current
                <= entry * (1 - self.config.model_a_failed_breakout_exit_pct) + 1e-9
            ):
                return "model_a_failed_breakout"

        if pnl_pct >= self.config.take_profit_pct:
            return "take_profit"

        # Protect a trade that has already proved itself instead of allowing a full round-trip.
        if high_pct >= self.config.break_even_trigger_pct:
            protected = entry * (1 + self.config.break_even_lock_pct)
            if current <= protected:
                return "profit_protection"

        # Start trailing much earlier than the old +3% trigger. This is intentionally
        # responsive for intraday PAPER testing, while the original wider trail remains
        # available after a stronger move.
        if high_pct >= self.config.early_trail_trigger_pct:
            trailing = high * (1 - self.config.early_trail_distance_pct)
            if current <= trailing:
                return "early_trailing_stop"

        if high_pct >= self.config.trail_trigger_pct:
            trailing = high * (1 - self.config.trail_distance_pct)
            if current <= trailing:
                return "trailing_stop"

        if entered_at is not None:
            current_time = now or datetime.now(EASTERN)
            elapsed_minutes = (current_time - entered_at).total_seconds() / 60
            if elapsed_minutes >= self.config.stagnation_minutes and high_pct < self.config.stagnation_min_gain_pct:
                return "stagnation_exit"

        return None
