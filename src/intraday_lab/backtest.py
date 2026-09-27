from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from .config import Settings
from .models import Decision
from .risk import RiskManager
from .strategy import OpeningRangeVwapStrategy


EASTERN = ZoneInfo("America/New_York")


@dataclass
class BacktestTrade:
    session: str
    symbol: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    reason: str
    profit_loss: float
    return_pct: float


class Backtester:
    """Conservative one-position intraday simulator using IEX minute bars."""

    def __init__(self, config: Settings) -> None:
        config.validate()
        self.config = config
        self.strategy = OpeningRangeVwapStrategy(config.relative_volume_min)
        self.risk = RiskManager(config)
        self.data = StockHistoricalDataClient(config.api_key, config.secret_key)

    def fetch(self, start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
        request = StockBarsRequest(
            symbol_or_symbols=list(self.config.watchlist), timeframe=TimeFrame.Minute,
            start=start, end=end, feed=DataFeed.IEX,
        )
        bars = self.data.get_stock_bars(request).df
        result: dict[str, pd.DataFrame] = {}
        if bars.empty:
            return result
        for symbol in self.config.watchlist:
            if isinstance(bars.index, pd.MultiIndex) and symbol in bars.index.get_level_values(0):
                frame = bars.xs(symbol, level=0).copy()
                index = pd.DatetimeIndex(frame.index)
                if index.tz is None:
                    index = index.tz_localize("UTC")
                frame.index = index.tz_convert(EASTERN)
                result[symbol] = frame
        return result

    def run(self, start: datetime, end: datetime) -> dict:
        bars = self.fetch(start, end)
        if not bars:
            return {"error": "No bars returned", "trades": []}
        sessions = sorted({stamp.date() for frame in bars.values() for stamp in frame.index})
        equity = self.config.starting_balance
        peak = equity
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        for session_date in sessions:
            if equity <= self.config.floor_equity or equity >= self.config.target_equity:
                break
            session_bars = {symbol: frame[frame.index.date == session_date] for symbol, frame in bars.items()}
            trade = self._trade_session(session_date, session_bars, equity)
            if trade:
                trades.append(trade)
                equity += trade.profit_loss
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0)
        wins = sum(t.profit_loss > 0 for t in trades)
        losses = sum(t.profit_loss < 0 for t in trades)
        return {
            "start": start.isoformat(), "end": end.isoformat(),
            "starting_equity": round(self.config.starting_balance, 2),
            "ending_equity": round(equity, 2),
            "total_return_pct": round((equity / self.config.starting_balance - 1) * 100, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "trade_count": len(trades), "wins": wins, "losses": losses,
            "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0,
            "trades": [asdict(t) for t in trades],
            "assumptions": ["IEX minute bars", "one long position", "no commissions",
                            "0.05% adverse slippage each way", "stop precedes target within one bar"],
        }

    def _trade_session(self, session_date: date, bars: dict[str, pd.DataFrame], equity: float) -> BacktestTrade | None:
        timeline = sorted({stamp for frame in bars.values() for stamp in frame.index
                           if time(9, 45) <= stamp.time() <= time(14, 30)})
        for timestamp in timeline:
            market_aligned = self._market_alignment_at(bars, timestamp)
            candidates = []
            for symbol, frame in bars.items():
                signal = self.strategy.evaluate(symbol, frame[frame.index <= timestamp], market_aligned,
                                                timestamp.to_pydatetime())
                if signal.decision == Decision.BUY:
                    candidates.append(signal)
            if not candidates:
                continue
            signal = max(candidates, key=lambda item: item.relative_volume)
            entry = signal.price * 1.0005
            quantity = min(equity, self.config.max_exposure) / entry
            future = bars[signal.symbol][bars[signal.symbol].index > timestamp]
            exit_price, exit_time, reason = self._simulate_exit(entry, future)
            exit_price *= 0.9995
            profit_loss = quantity * (exit_price - entry)
            return BacktestTrade(str(session_date), signal.symbol, timestamp.isoformat(), round(entry, 4),
                                 exit_time.isoformat(), round(exit_price, 4), reason,
                                 round(profit_loss, 2), round((exit_price / entry - 1) * 100, 3))
        return None

    def _simulate_exit(self, entry: float, future: pd.DataFrame) -> tuple[float, pd.Timestamp, str]:
        stop = entry * (1 - self.config.stop_loss_pct)
        target = entry * (1 + self.config.take_profit_pct)
        high_watermark = entry
        eligible = future[future.index.time <= time(15, 50)]
        if eligible.empty:
            raise RuntimeError("No bars available after entry")
        for timestamp, bar in eligible.iterrows():
            high_watermark = max(high_watermark, float(bar["high"]))
            if float(bar["low"]) <= stop:
                return stop, timestamp, "stop_loss"
            if float(bar["high"]) >= target:
                return target, timestamp, "take_profit"
            if (high_watermark - entry) / entry >= self.config.trail_trigger_pct:
                trailing = high_watermark * (1 - self.config.trail_distance_pct)
                if float(bar["low"]) <= trailing:
                    return trailing, timestamp, "trailing_stop"
        last_time = eligible.index[-1]
        return float(eligible.iloc[-1]["close"]), last_time, "end_of_day"

    @staticmethod
    def _market_alignment_at(bars: dict[str, pd.DataFrame], timestamp: pd.Timestamp) -> bool:
        for benchmark in ("SPY", "QQQ"):
            frame = bars.get(benchmark)
            if frame is not None:
                visible = frame[frame.index <= timestamp]
                if len(visible) >= 2 and float(visible.iloc[-1]["close"]) > float(visible.iloc[-2]["close"]):
                    return True
        return False
