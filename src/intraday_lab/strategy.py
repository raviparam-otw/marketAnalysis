from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd
from .models import Decision, Signal

EASTERN = ZoneInfo("America/New_York")


def _session_frame(bars: pd.DataFrame, now: datetime) -> pd.DataFrame:
    frame = bars.copy().sort_index()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame.index = index.tz_convert(EASTERN)
    return frame[frame.index.date == now.date()]


def _enrich(session: pd.DataFrame) -> pd.DataFrame:
    frame = session.copy()
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    cumvol = frame["volume"].cumsum().replace(0, pd.NA)
    frame["vwap"] = (typical * frame["volume"]).cumsum() / cumvol
    frame["avg_volume_20"] = frame["volume"].rolling(20, min_periods=5).mean().shift(1)
    frame["relative_volume"] = frame["volume"] / frame["avg_volume_20"]
    return frame


class OpeningRangeVwapStrategy:
    """Model A: existing opening-range/VWAP strategy, preserved for A/B comparison."""

    def __init__(self, relative_volume_min: float = 1.5) -> None:
        self.relative_volume_min = relative_volume_min

    def evaluate(self, symbol: str, bars: pd.DataFrame, market_aligned: bool, now: datetime | None = None, catalyst: dict | None = None) -> Signal:
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        if bars.empty or len(bars) < 20:
            return Signal(symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned, "Insufficient minute bars", now)
        session = _enrich(_session_frame(bars, now))
        opening = session.between_time("09:30", "09:44")
        if opening.empty:
            return Signal(symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned, "Opening range not available", now)
        last = session.iloc[-1]
        price = float(last["close"]); vwap = float(last["vwap"]); opening_high = float(opening["high"].max())
        rvol = float(last["relative_volume"]) if pd.notna(last["relative_volume"]) else 0.0
        conditions = {
            "entry window": time(9,45) <= now.time() <= time(14,30),
            "above opening range": price > opening_high,
            "above VWAP": price > vwap,
            "relative volume": rvol >= self.relative_volume_min,
            "market alignment": market_aligned,
        }
        failed = [k for k,v in conditions.items() if not v]
        stop = max(vwap, opening_high) * 0.997 if price > max(vwap, opening_high) else price * 0.975
        decision = Decision.BUY if not failed else Decision.HOLD
        return Signal(symbol, decision, price, vwap, opening_high, rvol, market_aligned,
                      "All breakout confirmations passed" if not failed else "Waiting for: " + ", ".join(failed),
                      now, setup="opening_range_breakout", stop_price=stop, score=rvol)


class MomentumCatalystStrategy:
    """Model B: public momentum concepts - gap/change, RVOL, catalyst and pullback/HOD continuation."""

    def __init__(self, config) -> None:
        self.config = config

    def evaluate(self, symbol: str, bars: pd.DataFrame, market_aligned: bool, now: datetime | None = None, catalyst: dict | None = None) -> Signal:
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        if bars.empty or len(bars) < 20:
            return Signal(symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned, "Insufficient minute bars", now, setup="momentum")
        session = _enrich(_session_frame(bars, now))
        if len(session) < 6:
            return Signal(symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned, "Waiting for session structure", now, setup="momentum")
        last = session.iloc[-1]
        price = float(last["close"]); vwap = float(last["vwap"])
        day_open = float(session.iloc[0]["open"])
        previous_close = float(bars.iloc[-len(session)-1]["close"]) if len(bars) > len(session) else day_open
        gap_pct = ((day_open / previous_close) - 1) * 100 if previous_close else 0.0
        change_pct = ((price / previous_close) - 1) * 100 if previous_close else 0.0
        rvol = float(last["relative_volume"]) if pd.notna(last["relative_volume"]) else 0.0
        hod = float(session["high"].max())
        prior5 = session.iloc[-6:-1]
        pullback_low = float(prior5["low"].min())
        recent_high = float(prior5["high"].max())
        near_hod = price >= hod * 0.985
        continuation = price > recent_high and float(last["close"]) > float(last["open"])
        controlled_pullback = pullback_low > vwap * 0.985
        catalyst_ok = bool(catalyst) or not self.config.momentum_require_news
        conditions = {
            "price range": self.config.min_price <= price <= self.config.momentum_max_price,
            "gap/change": gap_pct >= self.config.momentum_gap_min_pct or change_pct >= self.config.momentum_change_min_pct,
            "relative volume": rvol >= self.config.momentum_rvol_min,
            "catalyst": catalyst_ok,
            "above VWAP": price > vwap,
            "near HOD": near_hod,
            "pullback continuation": controlled_pullback and continuation,
            "entry window": time(9,35) <= now.time() <= time(14,30),
        }

        # Action-day Model B is intentionally aggressive: retain hard safety gates
        # (tradable price and clock), but use a weighted momentum score for the rest.
        score = (
            max(0.0, change_pct) * 1.25
            + max(0.0, gap_pct) * 0.75
            + min(max(rvol, 0.0), 10.0) * 2.0
            + (4.0 if price > vwap else 0.0)
            + (3.0 if near_hod else 0.0)
            + (3.0 if controlled_pullback and continuation else 0.0)
            + (4.0 if catalyst else 0.0)
        )
        hard_failed = [
            name for name in ("price range", "entry window")
            if not conditions[name]
        ]
        if (not self.config.action_day_mode) and self.config.momentum_require_news and not conditions["catalyst"]:
            hard_failed.append("catalyst")
        momentum_trigger = (
            conditions["gap/change"]
            or conditions["relative volume"]
            or (price > vwap and near_hod)
            or (controlled_pullback and continuation)
        )
        qualified = (
            not hard_failed
            and momentum_trigger
            and score >= self.config.momentum_min_score
        )

        failed = [k for k,v in conditions.items() if not v]
        stop = min(pullback_low, vwap) if min(pullback_low, vwap) < price else price * 0.98
        context = {
            "hod": hod,
            "pullback_low": pullback_low,
            "recent_high": recent_high,
            "conditions": conditions,
            "failed_conditions": failed,
            "hard_failed": hard_failed,
            "momentum_trigger": momentum_trigger,
            "min_score": self.config.momentum_min_score,
        }
        reason = (
            f"Aggressive momentum score {score:.2f}"
            if qualified
            else f"Rejected score={score:.2f}; waiting for: " + ", ".join(failed or ["momentum score"])
        )
        return Signal(symbol, Decision.BUY if qualified else Decision.HOLD, price, vwap, hod, rvol, market_aligned,
                      reason, now, setup="momentum_action_day", gap_pct=gap_pct, change_pct=change_pct,
                      stop_price=stop, catalyst=bool(catalyst), catalyst_headline=(catalyst or {}).get("headline"),
                      score=score, context=context)
