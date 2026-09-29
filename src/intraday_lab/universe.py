from __future__ import annotations
from dataclasses import dataclass

EXCLUDED_NAME_FRAGMENTS=(" WARRANT "," WARRANTS "," UNIT "," UNITS "," RIGHT "," RIGHTS "," PREFERRED ")
LEVERAGED_OR_INVERSE_FRAGMENTS=(" 2X "," 3X "," -1X "," -2X "," -3X "," ULTRA "," ULTRAPRO"," DAILY BULL"," DAILY BEAR"," INVERSE")

@dataclass(frozen=True)
class UniverseCandidate:
    symbol:str
    price:float
    dollar_volume:float
    spread_pct:float
    gap_pct:float=0.0
    change_pct:float=0.0


def enum_value(value:object)->str:
    return str(getattr(value,"value",value)).upper()


def asset_is_eligible(asset:object,excluded_symbols:set[str])->bool:
    symbol=str(getattr(asset,"symbol","")).upper(); name=f" {str(getattr(asset,'name','')).upper()} "
    exchange=enum_value(getattr(asset,"exchange","")); status=enum_value(getattr(asset,"status",""))
    if not symbol or symbol in excluded_symbols: return False
    if status!="ACTIVE" or exchange=="OTC" or not bool(getattr(asset,"tradable",False)): return False
    if any(f in name for f in EXCLUDED_NAME_FRAGMENTS): return False
    if any(f in name for f in LEVERAGED_OR_INVERSE_FRAGMENTS): return False
    return True


def snapshot_candidate(symbol:str,snapshot:object,min_price:float,min_dollar_volume:float,max_spread_pct:float)->UniverseCandidate|None:
    trade=getattr(snapshot,"latest_trade",None); quote=getattr(snapshot,"latest_quote",None); daily=getattr(snapshot,"daily_bar",None); previous=getattr(snapshot,"previous_daily_bar",None)
    price=float(getattr(trade,"price",0) or getattr(daily,"close",0) or 0); bid=float(getattr(quote,"bid_price",0) or 0); ask=float(getattr(quote,"ask_price",0) or 0)
    if price<min_price or bid<=0 or ask<=bid: return None
    spread=(ask-bid)/((ask+bid)/2)
    if spread>max_spread_pct: return None
    volume=float(getattr(daily,"volume",0) or 0); prev_volume=float(getattr(previous,"volume",0) or 0)
    dollar_volume=price*max(volume,prev_volume)
    if dollar_volume<min_dollar_volume: return None
    prev_close=float(getattr(previous,"close",0) or 0); day_open=float(getattr(daily,"open",0) or price)
    gap=((day_open/prev_close)-1)*100 if prev_close else 0.0; change=((price/prev_close)-1)*100 if prev_close else 0.0
    return UniverseCandidate(symbol.upper(),price,dollar_volume,spread,gap,change)


def rank_candidates(candidates:list[UniverseCandidate],limit:int)->list[str]:
    # Favor active movers but retain liquidity as a tie-breaker.
    ranked=sorted(candidates,key=lambda x:(max(abs(x.change_pct),abs(x.gap_pct)),x.dollar_volume),reverse=True)
    return [c.symbol for c in ranked[:limit]]
