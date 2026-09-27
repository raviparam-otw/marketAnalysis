from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .broker import PaperBroker
from .config import Settings
from .models import Decision, PositionGuard
from .risk import RiskManager
from .strategy import OpeningRangeVwapStrategy


EASTERN = ZoneInfo("America/New_York")


class TradingEngine:
    def __init__(self, config: Settings, broker: PaperBroker) -> None:
        self.config = config
        self.broker = broker
        self.strategy = OpeningRangeVwapStrategy(config.relative_volume_min)
        self.risk = RiskManager(config)
        self.running = False
        self.task: asyncio.Task | None = None
        self.logs: deque[dict] = deque(maxlen=250)
        self.latest_signals: dict[str, dict] = {}
        self.guards: dict[str, PositionGuard] = {}
        self.last_account: dict = {}

    def log(self, level: str, message: str, **details) -> None:
        self.logs.appendleft(
            {
                "time": datetime.now(EASTERN).isoformat(timespec="seconds"),
                "level": level,
                "message": message,
                "details": details,
            }
        )

    async def start(self) -> None:
        if self.running:
            return
        self.config.validate()
        self.running = True
        self.task = asyncio.create_task(self._loop())
        self.log("INFO", "Paper engine started")

    async def stop(self) -> None:
        self.running = False
        task = self.task
        self.task = None
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.log("INFO", "Engine stopped")

    async def kill_switch(self) -> None:
        await self.stop()
        await asyncio.to_thread(self.broker.close_all)
        self.guards.clear()
        self.log("WARN", "Kill switch executed; orders canceled and positions closing")

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.run_cycle()
            except Exception as exc:  # keep dashboard alive and surface broker/data errors
                self.log("ERROR", "Cycle failed", error=str(exc))
            await asyncio.sleep(self.config.poll_seconds)

    async def run_cycle(self) -> None:
        now = datetime.now(EASTERN)
        account = await asyncio.to_thread(self.broker.account_snapshot)
        positions = await asyncio.to_thread(self.broker.positions)
        self.last_account = account
        self.risk.reset_session_if_needed(account["equity"], now)

        if account["equity"] <= self.config.floor_equity or account["equity"] >= self.config.target_equity:
            await self.kill_switch()
            return

        await self._manage_positions(positions, now)
        if positions or not self._entry_session(now):
            return

        bars = await asyncio.to_thread(self.broker.minute_bars, list(self.config.watchlist))
        market_aligned = self._market_alignment(bars)
        candidates = []
        for symbol in self.config.watchlist:
            signal = self.strategy.evaluate(symbol, bars.get(symbol, self._empty_frame()), market_aligned, now)
            self.latest_signals[symbol] = signal.to_dict()
            if signal.decision == Decision.BUY:
                candidates.append(signal)

        if not candidates:
            return
        best = max(candidates, key=lambda item: item.relative_volume)
        check = self.risk.entry_check(account["equity"], account["cash"], False)
        if not check.allowed:
            self.log("INFO", "Entry rejected", reason=check.reason)
            return
        order = await asyncio.to_thread(self.broker.buy_notional, best.symbol, check.notional)
        self.guards[best.symbol] = PositionGuard(best.symbol, best.price, best.price, now)
        self.log("TRADE", "Paper buy submitted", symbol=best.symbol, notional=check.notional, order_id=str(order.id))

    async def _manage_positions(self, positions: list, now: datetime) -> None:
        for position in positions:
            symbol = position.symbol
            current = float(position.current_price)
            entry = float(position.avg_entry_price)
            guard = self.guards.setdefault(symbol, PositionGuard(symbol, entry, current, now))
            guard.high_watermark = max(guard.high_watermark, current)
            reason = self.risk.exit_reason(entry, current, guard.high_watermark)
            if now.time() >= time(15, 50):
                reason = "end_of_day"
            if reason:
                await asyncio.to_thread(self.broker.close_position, symbol)
                unrealized = float(position.unrealized_pl)
                self.risk.record_closed_trade(unrealized)
                self.guards.pop(symbol, None)
                self.log("TRADE", "Paper exit submitted", symbol=symbol, reason=reason, approximate_pl=unrealized)

    @staticmethod
    def _entry_session(now: datetime) -> bool:
        return now.weekday() < 5 and time(9, 45) <= now.time() <= time(14, 30)

    @staticmethod
    def _market_alignment(bars: dict) -> bool:
        for benchmark in ("SPY", "QQQ"):
            frame = bars.get(benchmark)
            if frame is not None and len(frame) >= 2 and float(frame.iloc[-1]["close"]) > float(frame.iloc[-2]["close"]):
                return True
        return False

    @staticmethod
    def _empty_frame():
        import pandas as pd

        return pd.DataFrame()

    def status(self) -> dict:
        return {
            "mode": "PAPER ONLY",
            "running": self.running,
            "account": self.last_account,
            "limits": {
                "starting_balance": self.config.starting_balance,
                "target_equity": self.config.target_equity,
                "floor_equity": self.config.floor_equity,
                "max_exposure": self.config.max_exposure,
                "daily_loss_limit": self.config.daily_loss_limit,
            },
            "signals": list(self.latest_signals.values()),
            "logs": list(self.logs),
        }
