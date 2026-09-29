from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AlpacaNewsService:
    """Small cached adapter around Alpaca's news REST endpoint (Benzinga source)."""

    def __init__(self, api_key: str, secret_key: str, lookback_minutes: int = 180) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.lookback_minutes = lookback_minutes
        self.cache: dict[str, dict] = {}
        self.last_refresh: datetime | None = None

    def refresh(self, symbols: list[str]) -> None:
        if not symbols:
            return
        now = datetime.now(timezone.utc)
        if self.last_refresh and (now - self.last_refresh).total_seconds() < 60:
            return
        start = now - timedelta(minutes=self.lookback_minutes)
        query = urlencode({"symbols": ",".join(symbols[:100]), "start": start.isoformat(), "limit": 50, "sort": "desc"})
        req = Request(
            f"https://data.alpaca.markets/v1beta1/news?{query}",
            headers={"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.secret_key},
        )
        try:
            with urlopen(req, timeout=8) as response:
                payload = json.load(response)
        except Exception:
            self.last_refresh = now
            return
        for item in payload.get("news", []):
            for symbol in item.get("symbols", []):
                self.cache[symbol.upper()] = {
                    "headline": item.get("headline"),
                    "summary": item.get("summary"),
                    "created_at": item.get("created_at"),
                    "source": item.get("source"),
                    "id": item.get("id"),
                }
        self.last_refresh = now

    def catalyst_for(self, symbol: str) -> dict | None:
        return self.cache.get(symbol.upper())
