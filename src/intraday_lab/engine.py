from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from .broker import PaperBroker
from .config import Settings
from .journal import TradeJournal
from .models import Decision, PositionGuard
from .news import AlpacaNewsService
from .risk import RiskManager
from .strategy import MomentumCatalystStrategy, OpeningRangeVwapStrategy

EASTERN=ZoneInfo("America/New_York")


class ModelRuntime:
    def __init__(self,name:str,label:str,strategy,risk:RiskManager) -> None:
        self.name=name; self.label=label; self.strategy=strategy; self.risk=risk
        self.logs=deque(maxlen=250); self.latest_signals={}; self.guards:dict[str,PositionGuard]={}
        self.realized_pl=0.0; self.last_trade_pl=0.0

    def log(self,level:str,message:str,**details)->None:
        self.logs.appendleft({"time":datetime.now(EASTERN).isoformat(timespec="seconds"),"level":level,"message":message,"details":details})


class TradingEngine:
    """Single paper-only orchestrator running Model A and Model B against the same market session."""
    def __init__(self,config:Settings,broker:PaperBroker)->None:
        self.config=config; self.broker=broker; self.journal=TradeJournal(config.trade_log_dir)
        self.news=AlpacaNewsService(config.api_key,config.secret_key)
        self.models={
            "A":ModelRuntime("A","Opening Range / VWAP",OpeningRangeVwapStrategy(config.relative_volume_min),RiskManager(config,"A")),
            "B":ModelRuntime("B","Momentum Catalyst",MomentumCatalystStrategy(config),RiskManager(config,"B")),
        }
        self.running=False; self.task=None; self.last_account={}; self.universe=list(config.watchlist); self.universe_stats={}; self.universe_refreshed_at=None

    async def start(self)->None:
        if self.running:return
        self.config.validate(); path=self.journal.ensure_today(); self.running=True; self.task=asyncio.create_task(self._loop())
        for m in self.models.values():m.log("INFO","Model started",capital=self.config.model_capital,trade_log=str(path))

    async def stop(self)->None:
        self.running=False; task=self.task; self.task=None
        if task and task is not asyncio.current_task():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        await self._sync_journal_orders()
        for m in self.models.values():m.log("INFO","Model stopped")

    async def kill_switch(self)->None:
        await self.stop()
        try: await asyncio.to_thread(self.broker.cancel_open_orders)
        except Exception as exc:
            for m in self.models.values():m.log("ERROR","Could not cancel open orders",error=str(exc))
        try: await asyncio.to_thread(self.broker.close_all)
        except Exception as exc:
            for m in self.models.values():m.log("ERROR","Could not close all paper positions",error=str(exc))
        for m in self.models.values(): m.guards.clear(); m.log("WARN","Kill switch executed")

    async def _loop(self)->None:
        while self.running:
            try: await self.run_cycle()
            except Exception as exc:
                for m in self.models.values():m.log("ERROR","Cycle failed",error=str(exc))
            await asyncio.sleep(self.config.poll_seconds)

    async def run_cycle(self)->None:
        now=datetime.now(EASTERN); await self._sync_journal_orders(); self.last_account=await asyncio.to_thread(self.broker.account_snapshot)
        positions=await asyncio.to_thread(self.broker.positions); position_map={p.symbol:p for p in positions}
        for model in self.models.values(): await self._manage_model_position(model,position_map,now)
        if not self._entry_session(now): return
        await self._refresh_universe_if_needed(now)
        symbols=list(dict.fromkeys([*self.universe,"SPY","QQQ"])); bars=await asyncio.to_thread(self.broker.minute_bars,symbols)
        market_aligned=self._market_alignment(bars)
        await asyncio.to_thread(self.news.refresh,self.universe)
        locked={s for model in self.models.values() for s in model.guards}
        for model in self.models.values():
            if model.guards: continue
            candidates=[]
            for symbol in self.universe:
                if symbol in locked: continue
                catalyst=self.news.catalyst_for(symbol) if model.name=="B" else None
                signal=model.strategy.evaluate(symbol,bars.get(symbol,self._empty_frame()),market_aligned,now,catalyst)
                model.latest_signals[symbol]=signal.to_dict()
                if signal.decision==Decision.BUY:candidates.append(signal)
            if not candidates:
                model.log("SCAN","No qualified entry",scanned=len(self.universe)); continue
            best=max(candidates,key=lambda s:s.score or s.relative_volume)
            check=model.risk.entry_check(best.price,best.stop_price,False,now)
            if not check.allowed:
                model.log("INFO","Entry rejected",reason=check.reason,symbol=best.symbol); continue
            cid=f"model-{model.name.lower()}-{uuid4().hex[:20]}"
            order=await asyncio.to_thread(self.broker.buy_qty,best.symbol,check.quantity,cid)
            context=best.to_dict()|{"risk_dollars":check.dollars_at_risk,"planned_notional":check.notional}
            self.journal.record_order(order,model=model.name,side="BUY",symbol=best.symbol,reason=best.reason,requested_qty=check.quantity,signal_price=best.price,signal_context=context)
            model.guards[best.symbol]=PositionGuard(model.name,best.symbol,best.price,check.quantity,best.price,best.stop_price,now)
            locked.add(best.symbol); model.log("TRADE","Paper buy submitted",symbol=best.symbol,qty=round(check.quantity,4),risk=round(check.dollars_at_risk,2),setup=best.setup)

    async def _manage_model_position(self,model:ModelRuntime,position_map:dict,now:datetime)->None:
        for symbol,guard in list(model.guards.items()):
            position=position_map.get(symbol)
            if position is None: continue
            current=float(position.current_price); entry=float(position.avg_entry_price); guard.entry_price=entry; guard.high_watermark=max(guard.high_watermark,current)
            reason=model.risk.exit_reason(entry,current,guard.high_watermark,guard.stop_price)
            if now.time()>=time(15,50):reason="end_of_day"
            if not reason: continue
            qty=min(abs(float(position.qty)),guard.quantity)
            cid=f"model-{model.name.lower()}-exit-{uuid4().hex[:16]}"
            order=await asyncio.to_thread(self.broker.sell_qty,symbol,qty,cid)
            self.journal.record_order(order,model=model.name,side="SELL",symbol=symbol,reason=reason,requested_qty=qty,signal_price=current,signal_context={"entry":entry,"stop":guard.stop_price,"high_watermark":guard.high_watermark})
            model.guards.pop(symbol,None); model.log("TRADE","Paper exit submitted",symbol=symbol,reason=reason,qty=round(qty,4))

    async def _sync_journal_orders(self)->None:
        for oid in self.journal.pending_order_ids():
            try: self.journal.update_order(await asyncio.to_thread(self.broker.order,oid))
            except Exception: pass
        status=self.journal.status(); summary=status.get("summary",{}).get("models",{})
        for name,model in self.models.items():
            model.realized_pl=float(summary.get(name,{}).get("realized_pl",0) or 0); model.risk.realized_pl=model.realized_pl

    async def _refresh_universe_if_needed(self,now:datetime)->None:
        if not self.config.dynamic_universe:self.universe=list(self.config.watchlist);return
        if self.universe_refreshed_at and now-self.universe_refreshed_at<timedelta(minutes=self.config.universe_refresh_minutes):return
        selected,stats=await asyncio.to_thread(self.broker.discover_universe)
        if selected:
            self.universe=selected; self.universe_stats=stats; self.universe_refreshed_at=now
            for m in self.models.values():m.log("INFO","Universe refreshed",**stats)

    @staticmethod
    def _entry_session(now): return now.weekday()<5 and time(9,35)<=now.time()<=time(14,30)
    @staticmethod
    def _market_alignment(bars):
        for benchmark in ("SPY","QQQ"):
            f=bars.get(benchmark)
            if f is not None and len(f)>=2 and float(f.iloc[-1]["close"])>float(f.iloc[-2]["close"]):return True
        return False
    @staticmethod
    def _empty_frame():
        import pandas as pd
        return pd.DataFrame()

    def status(self)->dict:
        journal=self.journal.status()
        return {"mode":"PAPER ONLY","running":self.running,"account":self.last_account,"model_capital":self.config.model_capital,
                "models":{name:{"name":name,"label":m.label,"capital":self.config.model_capital,"realized_pl":m.realized_pl,"equity":self.config.model_capital+m.realized_pl,
                                       "open_positions":[{"symbol":g.symbol,"entry":g.entry_price,"qty":g.quantity,"stop":g.stop_price,"high":g.high_watermark} for g in m.guards.values()],
                                       "signals":list(m.latest_signals.values()),"logs":list(m.logs)} for name,m in self.models.items()},
                "universe":{"symbols":self.universe,"stats":self.universe_stats,"refreshed_at":self.universe_refreshed_at.isoformat() if self.universe_refreshed_at else None},
                "trades":journal.get("trades",[]),"trade_summary":journal.get("summary",{})}
