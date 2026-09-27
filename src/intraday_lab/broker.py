from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from .config import Settings


class PaperBroker:
    def __init__(self, config: Settings) -> None:
        config.validate()
        # Literal True is intentional: live mode cannot be selected through configuration.
        self.trading = TradingClient(config.api_key, config.secret_key, paper=True)
        self.data = StockHistoricalDataClient(config.api_key, config.secret_key)

    def account_snapshot(self) -> dict:
        account = self.trading.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "status": str(account.status),
            "trading_blocked": bool(account.trading_blocked),
        }

    def positions(self) -> list:
        return list(self.trading.get_all_positions())

    def minute_bars(self, symbols: list[str], lookback_hours: int = 30) -> dict[str, pd.DataFrame]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=lookback_hours)
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars = self.data.get_stock_bars(request).df
        result: dict[str, pd.DataFrame] = {}
        if bars.empty:
            return result
        if isinstance(bars.index, pd.MultiIndex):
            for symbol in symbols:
                if symbol in bars.index.get_level_values(0):
                    result[symbol] = bars.xs(symbol, level=0).copy()
        else:
            result[symbols[0]] = bars.copy()
        return result

    def buy_notional(self, symbol: str, notional: float):
        order = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(order_data=order)

    def close_position(self, symbol: str):
        return self.trading.close_position(symbol)

    def close_all(self):
        return self.trading.close_all_positions(cancel_orders=True)
