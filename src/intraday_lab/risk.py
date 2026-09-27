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
    notional: float = 0.0


class RiskManager:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.session_start_equity: float | None = None
        self.losses_today = 0
        self.session_date = None

    def reset_session_if_needed(self, equity: float, now: datetime | None = None) -> None:
        today = (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
        if self.session_date != today:
            self.session_date = today
            self.session_start_equity = equity
            self.losses_today = 0

    def entry_check(self, equity: float, cash: float, has_position: bool) -> RiskDecision:
        self.reset_session_if_needed(equity)
        if equity <= self.config.floor_equity:
            return RiskDecision(False, "Experiment floor reached")
        if equity >= self.config.target_equity:
            return RiskDecision(False, "Experiment target reached")
        if has_position:
            return RiskDecision(False, "Only one open position is allowed")
        if self.losses_today >= 1:
            return RiskDecision(False, "One losing trade already recorded today")
        start = self.session_start_equity or equity
        if start - equity >= self.config.daily_loss_limit:
            return RiskDecision(False, "Daily loss limit reached")
        notional = min(cash, equity, self.config.max_exposure)
        if notional < 1:
            return RiskDecision(False, "Insufficient cash")
        return RiskDecision(True, "Risk checks passed", round(notional, 2))

    def record_closed_trade(self, realized_pl: float) -> None:
        if realized_pl < 0:
            self.losses_today += 1

    def exit_reason(self, entry: float, current: float, high: float) -> str | None:
        pnl_pct = (current - entry) / entry
        if pnl_pct <= -self.config.stop_loss_pct:
            return "stop_loss"
        if pnl_pct >= self.config.take_profit_pct:
            return "take_profit"
        if (high - entry) / entry >= self.config.trail_trigger_pct:
            trail_price = high * (1 - self.config.trail_distance_pct)
            if current <= trail_price:
                return "trailing_stop"
        return None
