from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from .models import Decision, Signal


EASTERN = ZoneInfo("America/New_York")


class OpeningRangeVwapStrategy:
    """Long-only opening-range breakout confirmed by VWAP and relative volume."""

    def __init__(self, relative_volume_min: float = 1.5) -> None:
        self.relative_volume_min = relative_volume_min

    @staticmethod
    def enrich(bars: pd.DataFrame) -> pd.DataFrame:
        frame = bars.copy().sort_index()
        typical = (frame["high"] + frame["low"] + frame["close"]) / 3
        cumulative_volume = frame["volume"].cumsum().replace(0, pd.NA)
        frame["vwap"] = (typical * frame["volume"]).cumsum() / cumulative_volume
        frame["avg_volume_20"] = frame["volume"].rolling(20, min_periods=5).mean().shift(1)
        frame["relative_volume"] = frame["volume"] / frame["avg_volume_20"]
        return frame

    def evaluate(
        self,
        symbol: str,
        bars: pd.DataFrame,
        market_aligned: bool,
        now: datetime | None = None,
    ) -> Signal:
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        if bars.empty or len(bars) < 20:
            return self._hold(symbol, now, "Insufficient minute bars")

        frame = self.enrich(bars)
        local_index = pd.DatetimeIndex(frame.index)
        if local_index.tz is None:
            local_index = local_index.tz_localize("UTC")
        frame.index = local_index.tz_convert(EASTERN)

        session = frame[frame.index.date == now.date()]
        opening = session.between_time("09:30", "09:44")
        if opening.empty:
            return self._hold(symbol, now, "Opening range not available")

        last = session.iloc[-1]
        price = float(last["close"])
        vwap = float(last["vwap"])
        opening_high = float(opening["high"].max())
        relative_volume = float(last["relative_volume"]) if pd.notna(last["relative_volume"]) else 0.0

        within_entry_window = time(9, 45) <= now.time() <= time(14, 30)
        conditions = {
            "entry window": within_entry_window,
            "above opening range": price > opening_high,
            "above VWAP": price > vwap,
            "relative volume": relative_volume >= self.relative_volume_min,
            "market alignment": market_aligned,
        }
        failed = [name for name, passed in conditions.items() if not passed]
        decision = Decision.BUY if not failed else Decision.HOLD
        reason = "All breakout confirmations passed" if not failed else "Waiting for: " + ", ".join(failed)
        return Signal(
            symbol=symbol,
            decision=decision,
            price=price,
            vwap=vwap,
            opening_high=opening_high,
            relative_volume=relative_volume,
            market_aligned=market_aligned,
            reason=reason,
            timestamp=now,
        )

    @staticmethod
    def _hold(symbol: str, now: datetime, reason: str) -> Signal:
        return Signal(symbol, Decision.HOLD, 0, 0, 0, 0, False, reason, now)
