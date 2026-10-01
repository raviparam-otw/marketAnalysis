from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum


class Decision(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    symbol: str
    decision: Decision
    price: float
    vwap: float
    opening_high: float
    relative_volume: float
    market_aligned: bool
    reason: str
    timestamp: datetime
    setup: str = ""
    gap_pct: float = 0.0
    change_pct: float = 0.0
    stop_price: float = 0.0
    catalyst: bool = False
    catalyst_headline: str | None = None
    score: float = 0.0
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class PositionGuard:
    model: str
    symbol: str
    trade_id: str
    entry_price: float
    quantity: float
    high_watermark: float
    stop_price: float
    entered_at: datetime
    current_price: float = 0.0
    unrealized_pl: float = 0.0
    partial_taken: bool = False
    fill_risk_rebased: bool = False
    exit_pending: bool = False
    entry_order_id: str | None = None
    protective_stop_order_id: str | None = None
