from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus, OrderSide, TimeInForce
from alpaca.trading.requests import GetAssetsRequest, MarketOrderRequest

from .config import Settings
from .universe import asset_is_eligible, rank_candidates, snapshot_candidate


class PaperBroker:
    def __init__(self, config: Settings) -> None:
        config.validate()
        self.config = config
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

    def discover_universe(self) -> tuple[list[str], dict[str, int]]:
        """Screen every active Alpaca US equity, then return the liquid shortlist."""
        request = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        assets = self.trading.get_all_assets(request)
        excluded = set(self.config.excluded_symbols)
        eligible = [asset for asset in assets if asset_is_eligible(asset, excluded)]

        candidates = []
        for offset in range(0, len(eligible), self.config.snapshot_batch_size):
            symbols = [asset.symbol for asset in eligible[offset : offset + self.config.snapshot_batch_size]]
            snapshots = self.data.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
            )
            for symbol, snapshot in snapshots.items():
                candidate = snapshot_candidate(
                    symbol,
                    snapshot,
                    min_price=self.config.min_price,
                    min_dollar_volume=self.config.min_dollar_volume,
                    max_spread_pct=self.config.max_spread_pct,
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
        # Alpaca bar limits apply to the combined multi-symbol response. Small
        # batches prevent highly active alphabetically-first symbols from
        # consuming the response before later symbols receive any bars.
        for offset in range(0, len(symbols), self.config.bar_batch_size):
            batch = symbols[offset : offset + self.config.bar_batch_size]
            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                feed=DataFeed.IEX,
                limit=10_000,
            )
            bars = self.data.get_stock_bars(request).df
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
