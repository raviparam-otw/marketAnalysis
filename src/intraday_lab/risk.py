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
    def __init__(self, config: Settings, model: str = "A") -> None:
        self.config = config
        self.model = model
        self.session_date = None
        self.realized_pl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0

    def reset_session_if_needed(self, now: datetime | None = None) -> None:
        today = (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
        if self.session_date != today:
            self.session_date = today
            self.realized_pl = 0.0
            self.trades_today = 0
            self.consecutive_losses = 0

    def entry_check(self, entry_price: float, stop_price: float, has_position: bool, now: datetime | None = None) -> RiskDecision:
        self.reset_session_if_needed(now)
        if has_position:
            return RiskDecision(False, "Model already has an open position")
        if self.trades_today >= self.config.max_trades_per_day:
            return RiskDecision(False, "Maximum trades per day reached")
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            return RiskDecision(False, "Consecutive-loss limit reached")
        if self.realized_pl <= -self.config.daily_loss_limit:
            return RiskDecision(False, "Daily loss limit reached")
        risk_per_share = entry_price - stop_price
        if entry_price <= 0 or risk_per_share <= 0:
            return RiskDecision(False, "Invalid entry/stop relationship")
        by_risk = self.config.risk_per_trade / risk_per_share
        by_notional = self.config.max_position_notional / entry_price
        by_capital = self.config.model_capital / entry_price
        qty = max(0.0, min(by_risk, by_notional, by_capital))
        if qty < 0.001:
            return RiskDecision(False, "Calculated position is too small")
        notional = qty * entry_price
        return RiskDecision(True, "Risk checks passed", qty, notional, qty * risk_per_share)

    def record_closed_trade(self, realized_pl: float, now: datetime | None = None) -> None:
        # A close can be the first risk event seen after a process restart or
        # during isolated/unit validation. Initialize the session before
        # mutating counters so the next entry_check does not wipe them.
        self.reset_session_if_needed(now)
        self.trades_today += 1
        self.realized_pl += realized_pl
        self.consecutive_losses = self.consecutive_losses + 1 if realized_pl < 0 else 0

    def exit_reason(self, entry: float, current: float, high: float, stop_price: float) -> str | None:
        if current <= stop_price:
            return "technical_stop"
        pnl_pct = (current - entry) / entry
        if pnl_pct >= self.config.take_profit_pct:
            return "take_profit"
        if (high - entry) / entry >= self.config.trail_trigger_pct:
            if current <= high * (1 - self.config.trail_distance_pct):
                return "trailing_stop"
        return None
