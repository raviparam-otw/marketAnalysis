from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID
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
        config.validate(); self.config=config
        self.trading=TradingClient(config.api_key,config.secret_key,paper=True)
        self.data=StockHistoricalDataClient(config.api_key,config.secret_key)

    def account_snapshot(self):
        a=self.trading.get_account(); return {"equity":float(a.equity),"cash":float(a.cash),"buying_power":float(a.buying_power),"status":str(a.status),"trading_blocked":bool(a.trading_blocked)}
    def positions(self): return list(self.trading.get_all_positions())

    def discover_universe(self):
        assets=self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,asset_class=AssetClass.US_EQUITY))
        eligible=[a for a in assets if asset_is_eligible(a,set(self.config.excluded_symbols))]
        candidates=[]
        for off in range(0,len(eligible),self.config.snapshot_batch_size):
            symbols=[a.symbol for a in eligible[off:off+self.config.snapshot_batch_size]]
            snaps=self.data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=symbols,feed=DataFeed.IEX))
            for symbol,snap in snaps.items():
                c=snapshot_candidate(symbol,snap,self.config.min_price,self.config.min_dollar_volume,self.config.max_spread_pct)
                if c: candidates.append(c)
        selected=rank_candidates(candidates,self.config.universe_size)
        return selected,{"assets_received":len(assets),"metadata_eligible":len(eligible),"market_eligible":len(candidates),"selected":len(selected)}

    def minute_bars(self,symbols:list[str],lookback_hours:int=30):
        end=datetime.now(timezone.utc); start=end-timedelta(hours=lookback_hours); result={}
        for off in range(0,len(symbols),self.config.bar_batch_size):
            batch=symbols[off:off+self.config.bar_batch_size]
            bars=self.data.get_stock_bars(StockBarsRequest(symbol_or_symbols=batch,timeframe=TimeFrame.Minute,start=start,end=end,feed=DataFeed.IEX,limit=10_000)).df
            if bars.empty: continue
            if isinstance(bars.index,pd.MultiIndex):
                avail=bars.index.get_level_values(0)
                for s in batch:
                    if s in avail: result[s]=bars.xs(s,level=0).copy()
            elif len(batch)==1: result[batch[0]]=bars.copy()
        return result

    def buy_qty(self,symbol:str,qty:float,client_order_id:str):
        return self.trading.submit_order(order_data=MarketOrderRequest(symbol=symbol,qty=round(qty,6),side=OrderSide.BUY,time_in_force=TimeInForce.DAY,client_order_id=client_order_id))
    def sell_qty(self,symbol:str,qty:float,client_order_id:str):
        return self.trading.submit_order(order_data=MarketOrderRequest(symbol=symbol,qty=round(qty,6),side=OrderSide.SELL,time_in_force=TimeInForce.DAY,client_order_id=client_order_id))
    def order(self,order_id:str): return self.trading.get_order_by_id(UUID(str(order_id)))
    def cancel_open_orders(self): return self.trading.cancel_orders()
    def close_all(self): return self.trading.close_all_positions(cancel_orders=True)
