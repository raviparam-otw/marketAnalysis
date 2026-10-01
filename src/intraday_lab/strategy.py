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


def _available_time_of_day_rvol(bars: pd.DataFrame, now: datetime, fallback: float) -> tuple[float, str]:
    frame = bars.copy().sort_index()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame.index = index.tz_convert(EASTERN)
    regular = frame.between_time("09:30", "16:00")
    current = regular[regular.index.date == now.date()]
    if current.empty:
        return fallback, "1m_vs_trailing_20m"

    cutoff = now.replace(second=0, microsecond=0)
    current_cutoff = current[current.index < cutoff]
    current_volume = float(current_cutoff["volume"].sum())
    prior_dates = sorted({day for day in regular.index.date if day < now.date()}, reverse=True)
    baselines = []
    for day in prior_dates[:5]:
        prior = regular[regular.index.date == day]
        prior_cutoff = prior[prior.index.time < cutoff.time()]
        if not prior_cutoff.empty:
            baselines.append(float(prior_cutoff["volume"].sum()))
    if baselines:
        baseline = float(pd.Series(baselines).median())
        if baseline > 0:
            return current_volume / baseline, "cumulative_same_time"
    return fallback, "1m_vs_trailing_20m"


def _enrich(session: pd.DataFrame) -> pd.DataFrame:
    frame = session.copy()
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    cumvol = frame["volume"].cumsum().replace(0, pd.NA)
    frame["vwap"] = (typical * frame["volume"]).cumsum() / cumvol
    frame["avg_volume_20"] = frame["volume"].rolling(20, min_periods=5).mean().shift(1)
    frame["relative_volume"] = frame["volume"] / frame["avg_volume_20"]
    return frame


class OpeningRangeVwapStrategy:
    """Model A: opening-range/VWAP control with anti-chase and breakout-hold confirmation."""

    def __init__(
        self,
        relative_volume_min: float = 1.5,
        breakout_buffer_pct: float = 0.0015,
        confirmation_bars: int = 2,
        max_extension_from_or_pct: float = 0.06,
        max_extension_from_vwap_pct: float = 0.04,
        max_bar_age_seconds: int = 120,
    ) -> None:
        self.relative_volume_min = relative_volume_min
        self.breakout_buffer_pct = breakout_buffer_pct
        self.confirmation_bars = max(1, int(confirmation_bars))
        self.max_extension_from_or_pct = max_extension_from_or_pct
        self.max_extension_from_vwap_pct = max_extension_from_vwap_pct
        self.max_bar_age_seconds = max(1, int(max_bar_age_seconds))

    def evaluate(self, symbol: str, bars: pd.DataFrame, market_aligned: bool, now: datetime | None = None, catalyst: dict | None = None) -> Signal:
        now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        if bars.empty or len(bars) < 20:
            return Signal(
                symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned,
                "Insufficient minute bars", now,
                setup="opening_range_breakout",
                context={"failed_conditions": ["insufficient minute bars"]},
            )

        raw_session = _session_frame(bars, now)
        current_minute = now.replace(second=0, microsecond=0)
        # Alpaca minute-bar timestamps mark the start of the minute. Do not use
        # the current, still-forming candle as a breakout confirmation.
        completed_session = raw_session[raw_session.index < current_minute]
        session = _enrich(completed_session)

        opening = session.between_time("09:30", "09:44")
        expected_opening_minutes = {
            (datetime.combine(now.date(), time(9, 30), tzinfo=EASTERN) + pd.Timedelta(minutes=i)).time()
            for i in range(15)
        }
        observed_opening_minutes = {stamp.time().replace(tzinfo=None) for stamp in opening.index}
        if len(opening) < 15 or not expected_opening_minutes.issubset(observed_opening_minutes):
            return Signal(
                symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned,
                "Opening range incomplete", now,
                setup="opening_range_breakout",
                context={
                    "failed_conditions": ["opening range incomplete"],
                    "opening_bars": len(opening),
                    "required_opening_bars": 15,
                },
            )

        last = session.iloc[-1]
        last_timestamp = session.index[-1]
        bar_age_seconds = max(0.0, (now - last_timestamp.to_pydatetime()).total_seconds())
        if bar_age_seconds > self.max_bar_age_seconds:
            return Signal(
                symbol, Decision.HOLD, 0, 0, 0, 0, market_aligned,
                "Rejected: stale market bar", now,
                setup="opening_range_breakout",
                context={
                    "failed_conditions": ["stale market bar"],
                    "bar_age_seconds": bar_age_seconds,
                    "max_bar_age_seconds": self.max_bar_age_seconds,
                },
            )

        price = float(last["close"])
        vwap = float(last["vwap"])
        opening_high = float(opening["high"].max())
        volume_acceleration = float(last["relative_volume"]) if pd.notna(last["relative_volume"]) else 0.0
        rvol, rvol_mode = _available_time_of_day_rvol(bars, now, volume_acceleration)

        breakout_level = opening_high * (1 + self.breakout_buffer_pct)
        recent = session.iloc[-3:]
        closes_above_breakout = int((recent["close"] > breakout_level).sum())
        breakout_hold = closes_above_breakout >= self.confirmation_bars

        previous_close = float(session.iloc[-2]["close"]) if len(session) >= 2 else price
        latest_green = float(last["close"]) > float(last["open"])
        continuation = latest_green and price >= previous_close

        extension_from_or = ((price / opening_high) - 1) if opening_high > 0 else 999.0
        extension_from_vwap = ((price / vwap) - 1) if vwap > 0 else 999.0
        not_extended = (
            extension_from_or <= self.max_extension_from_or_pct
            and extension_from_vwap <= self.max_extension_from_vwap_pct
        )

        conditions = {
            "entry window": time(9,45) <= now.time() <= time(14,30),
            "above opening range": price > breakout_level,
            "breakout hold": breakout_hold,
            "continuation candle": continuation,
            "above VWAP": price > vwap,
            "relative volume": rvol >= self.relative_volume_min,
            "not extended": not_extended,
            "market alignment": market_aligned,
        }
        failed = [name for name, passed in conditions.items() if not passed]

        stop_reference = max(vwap, opening_high)
        stop = stop_reference * 0.997 if price > stop_reference else price * 0.975
        decision = Decision.BUY if not failed else Decision.HOLD

        context = {
            "conditions": conditions,
            "failed_conditions": failed,
            "breakout_level": breakout_level,
            "closes_above_breakout_last_3": closes_above_breakout,
            "required_confirmation_bars": self.confirmation_bars,
            "extension_from_or_pct": extension_from_or * 100,
            "extension_from_vwap_pct": extension_from_vwap * 100,
            "max_extension_from_or_pct": self.max_extension_from_or_pct * 100,
            "max_extension_from_vwap_pct": self.max_extension_from_vwap_pct * 100,
            "latest_green": latest_green,
            "previous_close": previous_close,
            "bar_age_seconds": bar_age_seconds,
            "rvol_mode": rvol_mode,
            "volume_acceleration_1m_vs_20m": volume_acceleration,
        }

        # Prefer persistent breakouts, not a one-bar poke above the opening range.
        score = rvol + (1.0 if breakout_hold else 0.0) + (0.5 if continuation else 0.0)
        reason = (
            "Confirmed ORB: held breakout, continuation, VWAP and RVOL passed"
            if not failed
            else "Rejected: " + ", ".join(failed)
        )
        return Signal(
            symbol,
            decision,
            price,
            vwap,
            opening_high,
            rvol,
            market_aligned,
            reason,
            now,
            setup="opening_range_breakout",
            stop_price=stop,
            score=score,
            context=context,
        )


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
        effective_max_price = (
            max(self.config.momentum_max_price, 100.0)
            if self.config.action_day_mode
            else self.config.momentum_max_price
        )
        conditions = {
            "price range": self.config.min_price <= price <= effective_max_price,
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
