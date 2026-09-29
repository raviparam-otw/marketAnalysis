from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus, OrderSide, TimeInForce
from alpaca.trading.requests import GetAssetsRequest, MarketOrderRequest

from .config import Settings
from .universe import asset_is_eligible, rank_candidates, snapshot_candidate


class PaperBroker:
    def __init__(self, config: Settings) -> None:
        config.validate()
        self.config = config
        # Literal True is intentional. Live trading cannot be enabled through configuration.
        self.trading = TradingClient(config.api_key, config.secret_key, paper=True)
        self.data = StockHistoricalDataClient(config.api_key, config.secret_key)

    def account_snapshot(self) -> dict:
        account = self.trading.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(getattr(account, "portfolio_value", account.equity)),
            "last_equity": float(getattr(account, "last_equity", account.equity)),
            "daytrade_count": int(getattr(account, "daytrade_count", 0) or 0),
            "status": str(account.status),
            "trading_blocked": bool(account.trading_blocked),
        }

    def positions(self) -> list:
        return list(self.trading.get_all_positions())

    def open_orders(self) -> list:
        return list(self.trading.get_orders())

    def discover_universe(self) -> tuple[list[str], dict[str, int]]:
        request = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        assets = self.trading.get_all_assets(request)
        eligible = [
            asset
            for asset in assets
            if asset_is_eligible(asset, set(self.config.excluded_symbols))
        ]

        candidates = []
        for offset in range(0, len(eligible), self.config.snapshot_batch_size):
            symbols = [
                asset.symbol
                for asset in eligible[offset : offset + self.config.snapshot_batch_size]
            ]
            snapshots = self.data.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
            )
            for symbol, snapshot in snapshots.items():
                candidate = snapshot_candidate(
                    symbol,
                    snapshot,
                    self.config.min_price,
                    self.config.min_dollar_volume,
                    self.config.max_spread_pct,
                )
                if candidate:
                    candidates.append(candidate)

        selected = rank_candidates(candidates, self.config.universe_size)
        return selected, {
            "assets_received": len(assets),
            "metadata_eligible": len(eligible),
            "market_eligible": len(candidates),
            "selected": len(selected),
        }

    def minute_bars(self, symbols: list[str], lookback_hours: int = 30) -> dict[str, pd.DataFrame]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=lookback_hours)
        result: dict[str, pd.DataFrame] = {}

        for offset in range(0, len(symbols), self.config.bar_batch_size):
            batch = symbols[offset : offset + self.config.bar_batch_size]
            bars = self.data.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Minute,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX,
                    limit=10_000,
                )
            ).df
            if bars.empty:
                continue
            if isinstance(bars.index, pd.MultiIndex):
                available = bars.index.get_level_values(0)
                for symbol in batch:
                    if symbol in available:
                        result[symbol] = bars.xs(symbol, level=0).copy()
            elif len(batch) == 1:
                result[batch[0]] = bars.copy()
        return result

    def buy_qty(self, symbol: str, qty: float, client_order_id: str):
        return self.trading.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                qty=round(qty, 6),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
        )

    def sell_qty(self, symbol: str, qty: float, client_order_id: str):
        return self.trading.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                qty=round(qty, 6),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
        )

    def close_position(self, symbol: str):
        return self.trading.close_position(symbol)

    def order(self, order_id: str):
        return self.trading.get_order_by_id(UUID(str(order_id)))

    def cancel_open_orders(self):
        return self.trading.cancel_orders()

    def close_all(self):
        return self.trading.close_all_positions(cancel_orders=True)
