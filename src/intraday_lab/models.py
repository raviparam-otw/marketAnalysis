from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class PositionGuard:
    symbol: str
    entry_price: float
    high_watermark: float
    entered_at: datetime
