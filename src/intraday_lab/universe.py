from __future__ import annotations

from dataclasses import dataclass


EXCLUDED_NAME_FRAGMENTS = (
    " WARRANT ",
    " WARRANTS ",
    " UNIT ",
    " UNITS ",
    " RIGHT ",
    " RIGHTS ",
    " PREFERRED ",
)

LEVERAGED_OR_INVERSE_FRAGMENTS = (
    " 2X ",
    " 3X ",
    " -1X ",
    " -2X ",
    " -3X ",
    " ULTRA ",
    " ULTRAPRO",
    " DAILY BULL",
    " DAILY BEAR",
    " INVERSE",
)


@dataclass(frozen=True)
class UniverseCandidate:
    symbol: str
    price: float
    dollar_volume: float
    spread_pct: float


def enum_value(value: object) -> str:
    """Return a stable string for alpaca-py enums and plain test values."""
    return str(getattr(value, "value", value)).upper()


def asset_is_eligible(asset: object, excluded_symbols: set[str]) -> bool:
    symbol = str(getattr(asset, "symbol", "")).upper()
    name = f" {str(getattr(asset, 'name', '')).upper()} "
    exchange = enum_value(getattr(asset, "exchange", ""))
    status = enum_value(getattr(asset, "status", ""))

    if not symbol or symbol in excluded_symbols:
        return False
    if status != "ACTIVE" or exchange == "OTC":
        return False
    if not bool(getattr(asset, "tradable", False)):
        return False
    # Orders are submitted by notional, so the asset must support fractional shares.
    if not bool(getattr(asset, "fractionable", False)):
        return False
    if any(fragment in name for fragment in EXCLUDED_NAME_FRAGMENTS):
        return False
    if any(fragment in name for fragment in LEVERAGED_OR_INVERSE_FRAGMENTS):
        return False
    return True


def snapshot_candidate(
    symbol: str,
    snapshot: object,
    min_price: float,
    min_dollar_volume: float,
    max_spread_pct: float,
) -> UniverseCandidate | None:
    trade = getattr(snapshot, "latest_trade", None)
    quote = getattr(snapshot, "latest_quote", None)
    daily = getattr(snapshot, "daily_bar", None)
    previous = getattr(snapshot, "previous_daily_bar", None)

    price = float(getattr(trade, "price", 0) or getattr(daily, "close", 0) or 0)
    bid = float(getattr(quote, "bid_price", 0) or 0)
    ask = float(getattr(quote, "ask_price", 0) or 0)
    if price < min_price or bid <= 0 or ask <= bid:
        return None

    spread_pct = (ask - bid) / ((ask + bid) / 2)
    if spread_pct > max_spread_pct:
        return None

    current_volume = float(getattr(daily, "volume", 0) or 0)
    previous_volume = float(getattr(previous, "volume", 0) or 0)
    dollar_volume = price * max(current_volume, previous_volume)
    if dollar_volume < min_dollar_volume:
        return None

    return UniverseCandidate(symbol.upper(), price, dollar_volume, spread_pct)


def rank_candidates(candidates: list[UniverseCandidate], limit: int) -> list[str]:
    ranked = sorted(candidates, key=lambda item: item.dollar_volume, reverse=True)
    return [candidate.symbol for candidate in ranked[:limit]]
