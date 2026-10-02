from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus, OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import GetAssetsRequest, GetOrderByIdRequest, LimitOrderRequest, MarketOrderRequest, StopLossRequest

from .config import Settings
from .universe import asset_is_eligible, rank_candidates, snapshot_candidate


class PaperBroker:
    def __init__(self, config: Settings) -> None:
        config.validate()
        self.config = config
        # Literal True is intentional. Live trading cannot be enabled through configuration.
        self.trading = TradingClient(config.api_key, config.secret_key, paper=True)
        self.data = StockHistoricalDataClient(config.api_key, config.secret_key)
        self.data_feed = DataFeed.SIP if config.market_data_feed == "sip" else DataFeed.IEX

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
                StockSnapshotRequest(symbol_or_symbols=symbols, feed=self.data_feed)
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
                    feed=self.data_feed,
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

    def latest_quote(self, symbol: str) -> dict:
        quotes = self.data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed=self.data_feed)
        )
        quote = quotes.get(symbol)
        if quote is None:
            return {}
        bid = float(getattr(quote, "bid_price", 0) or 0)
        ask = float(getattr(quote, "ask_price", 0) or 0)
        midpoint = ((bid + ask) / 2) if bid > 0 and ask > 0 else 0.0
        spread_pct = ((ask - bid) / midpoint) if midpoint > 0 and ask >= bid else None
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "bid_size": float(getattr(quote, "bid_size", 0) or 0),
            "ask_size": float(getattr(quote, "ask_size", 0) or 0),
            "timestamp": getattr(quote, "timestamp", None),
            "spread_pct": spread_pct,
            "feed": self.config.market_data_feed,
        }

    @staticmethod
    def _equity_price(price: float) -> float:
        return round(float(price), 2) if float(price) >= 1 else round(float(price), 4)

    def buy_protected_limit_qty(
        self,
        symbol: str,
        qty: float,
        limit_price: float,
        stop_price: float,
        client_order_id: str,
    ):
        entry_limit = self._equity_price(limit_price)
        protective_stop = self._equity_price(stop_price)
        if protective_stop >= entry_limit:
            tick = 0.01 if entry_limit >= 1 else 0.0001
            protective_stop = self._equity_price(max(tick, entry_limit - tick))

        whole_qty = int(float(qty))
        if whole_qty < 1:
            raise ValueError(
                f"Protected OTO order for {symbol} requires at least 1 whole share; "
                f"calculated qty={qty!r}."
            )

        return self.trading.submit_order(
            order_data=LimitOrderRequest(
                symbol=symbol,
                qty=whole_qty,
                limit_price=entry_limit,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
                order_class=OrderClass.OTO,
                stop_loss=StopLossRequest(stop_price=protective_stop),
            )
        )

    def order_nested(self, order_id: str):
        return self.trading.get_order_by_id(
            UUID(str(order_id)),
            GetOrderByIdRequest(nested=True),
        )

    def cancel_order(self, order_id: str) -> None:
        self.trading.cancel_order_by_id(UUID(str(order_id)))

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
