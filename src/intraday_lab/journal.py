from __future__ import annotations

import json, os
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}


def _value(value):
    return None if value is None else getattr(value, "value", value)

def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value is not None else None)

def _float(value):
    return None if value in (None, "") else float(value)


class TradeJournal:
    def __init__(self, directory: str | Path, now_fn: Callable[[], datetime] | None = None) -> None:
        self.directory = Path(directory); self.now_fn = now_fn or (lambda: datetime.now(EASTERN))

    def _now(self):
        now = self.now_fn(); return now if now.tzinfo else now.replace(tzinfo=EASTERN)
    def _path(self, now=None):
        current = now or self._now(); return self.directory / f"trades-{current.date().isoformat()}.json"
    def _new_payload(self, now):
        return {"date": now.date().isoformat(), "timezone":"America/New_York", "updated_at":now.isoformat(timespec="seconds"), "summary":{}, "trades":[]}
    def _load(self, now=None):
        current = now or self._now(); path=self._path(current)
        if not path.exists(): return path, self._new_payload(current)
        with path.open("r",encoding="utf-8") as h: return path, json.load(h)

    @staticmethod
    def _summary(trades):
        filled=[t for t in trades if t.get("status")=="filled"]
        realized=sum(float(t.get("realized_pl") or 0) for t in trades if t.get("side")=="SELL")
        by_model={}
        for model in ("A","B"):
            rows=[t for t in trades if t.get("model")==model]
            by_model[model]={"orders":len(rows),"realized_pl":round(sum(float(t.get("realized_pl") or 0) for t in rows if t.get("side")=="SELL"),2)}
        return {"orders":len(trades),"filled_orders":len(filled),"realized_pl":round(realized,2),"models":by_model}

    def _write(self,path,payload,now):
        self.directory.mkdir(parents=True,exist_ok=True); payload["updated_at"]=now.isoformat(timespec="seconds"); payload["summary"]=self._summary(payload.get("trades",[]))
        tmp=path.with_suffix(path.suffix+".tmp")
        with tmp.open("w",encoding="utf-8") as h:
            json.dump(payload,h,indent=2); h.write("\n"); h.flush(); os.fsync(h.fileno())
        os.replace(tmp,path)

    def ensure_today(self):
        now=self._now(); path,payload=self._load(now)
        if not path.exists(): self._write(path,payload,now)
        return path

    def record_order(self, order, *, model: str = "A", side: str, symbol: str, reason: str, requested_qty=None, requested_notional=None, signal_price=None, signal_context=None):
        now=self._now(); path,payload=self._load(now); oid=str(order.id)
        rec={"order_id":oid,"client_order_id":str(getattr(order,"client_order_id","") or "") or None,"model":model,"symbol":symbol,"side":side.upper(),"reason":reason,
             "status":str(_value(getattr(order,"status","submitted"))).lower(),"recorded_at":now.isoformat(timespec="seconds"),"submitted_at":_iso(getattr(order,"submitted_at",None)) or now.isoformat(timespec="seconds"),
             "requested_notional":_float(requested_notional),"requested_qty":_float(requested_qty),"signal_price":_float(signal_price),"filled_qty":_float(getattr(order,"filled_qty",None)),
             "filled_avg_price":_float(getattr(order,"filled_avg_price",None)),"filled_at":_iso(getattr(order,"filled_at",None)),"realized_pl":None,"signal_context":signal_context or {}}
        trades=payload.setdefault("trades",[]); existing=next((t for t in trades if t.get("order_id")==oid),None)
        if existing: existing.update(rec); rec=existing
        else: trades.append(rec)
        self._recalculate(trades); self._write(path,payload,now); return rec

    def _recalculate(self,trades):
        for sell in [t for t in trades if t.get("side")=="SELL" and t.get("status")=="filled" and t.get("filled_avg_price")]:
            buys=[t for t in trades if t.get("model")==sell.get("model") and t.get("symbol")==sell.get("symbol") and t.get("side")=="BUY" and t.get("status")=="filled" and t.get("filled_avg_price")]
            if not buys: continue
            buy=buys[-1]; qty=min(float(sell.get("filled_qty") or 0),float(buy.get("filled_qty") or 0))
            sell["realized_pl"]=round((float(sell["filled_avg_price"])-float(buy["filled_avg_price"]))*qty,4)

    def update_order(self,order):
        now=self._now(); path,payload=self._load(now); oid=str(order.id)
        rec=next((t for t in payload.get("trades",[]) if t.get("order_id")==oid),None)
        if rec is None: return False
        rec.update({"status":str(_value(getattr(order,"status",rec.get("status")))).lower(),"filled_qty":_float(getattr(order,"filled_qty",None)),"filled_avg_price":_float(getattr(order,"filled_avg_price",None)),"filled_at":_iso(getattr(order,"filled_at",None))})
        self._recalculate(payload.get("trades",[])); self._write(path,payload,now); return True

    def pending_order_ids(self):
        _,p=self._load(); return [t["order_id"] for t in p.get("trades",[]) if t.get("status") not in TERMINAL_STATUSES]
    def status(self):
        _,p=self._load(); return {"summary":self._summary(p.get("trades",[])),"trades":p.get("trades",[])}
