from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}


def _value(value):
    if value is None:
        return None
    return getattr(value, "value", value)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


class TradeJournal:
    """Crash-safe JSON journal for every paper order submitted by the engine."""

    def __init__(
        self,
        directory: str | Path,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.now_fn = now_fn or (lambda: datetime.now(EASTERN))

    def _now(self) -> datetime:
        now = self.now_fn()
        return now if now.tzinfo else now.replace(tzinfo=EASTERN)

    def _path(self, now: datetime | None = None) -> Path:
        current = now or self._now()
        return self.directory / f"trades-{current.date().isoformat()}.json"

    def _new_payload(self, now: datetime) -> dict:
        return {
            "date": now.date().isoformat(),
            "timezone": "America/New_York",
            "updated_at": now.isoformat(timespec="seconds"),
            "summary": {},
            "trades": [],
        }

    def _load(self, now: datetime | None = None) -> tuple[Path, dict]:
        current = now or self._now()
        path = self._path(current)
        if not path.exists():
            return path, self._new_payload(current)
        with path.open("r", encoding="utf-8") as handle:
            return path, json.load(handle)

    @staticmethod
    def _summary(trades: list[dict]) -> dict:
        buy_orders = [trade for trade in trades if trade.get("side") == "BUY"]
        sell_orders = [trade for trade in trades if trade.get("side") == "SELL"]
        filled = [trade for trade in trades if trade.get("status") == "filled"]
        estimated_pl = sum(
            float(trade.get("approximate_pl") or 0.0)
            for trade in sell_orders
        )
        return {
            "orders": len(trades),
            "buy_orders": len(buy_orders),
            "sell_orders": len(sell_orders),
            "filled_orders": len(filled),
            "estimated_realized_pl": round(estimated_pl, 2),
        }

    def _write(self, path: Path, payload: dict, now: datetime) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = now.isoformat(timespec="seconds")
        payload["summary"] = self._summary(payload.get("trades", []))
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    def ensure_today(self) -> Path:
        now = self._now()
        path, payload = self._load(now)
        if not path.exists():
            self._write(path, payload, now)
        return path

    def record_order(
        self,
        order,
        *,
        side: str,
        symbol: str,
        reason: str,
        requested_notional: float | None = None,
        requested_qty: float | None = None,
        signal_price: float | None = None,
        approximate_pl: float | None = None,
    ) -> dict:
        now = self._now()
        path, payload = self._load(now)
        order_id = str(order.id)
        status = str(_value(getattr(order, "status", "submitted"))).lower()
        record = {
            "order_id": order_id,
            "client_order_id": str(getattr(order, "client_order_id", "") or "") or None,
            "symbol": symbol,
            "side": side.upper(),
            "reason": reason,
            "status": status,
            "recorded_at": now.isoformat(timespec="seconds"),
            "submitted_at": _iso(getattr(order, "submitted_at", None)) or now.isoformat(timespec="seconds"),
            "requested_notional": _float(requested_notional),
            "requested_qty": _float(requested_qty),
            "signal_price": _float(signal_price),
            "filled_qty": _float(getattr(order, "filled_qty", None)),
            "filled_avg_price": _float(getattr(order, "filled_avg_price", None)),
            "filled_at": _iso(getattr(order, "filled_at", None)),
            "approximate_pl": _float(approximate_pl),
        }
        trades = payload.setdefault("trades", [])
        existing = next((trade for trade in trades if trade.get("order_id") == order_id), None)
        if existing:
            existing.update(record)
            record = existing
        else:
            trades.append(record)
        self._write(path, payload, now)
        return record

    def update_order(self, order) -> bool:
        now = self._now()
        path, payload = self._load(now)
        order_id = str(order.id)
        record = next(
            (trade for trade in payload.get("trades", []) if trade.get("order_id") == order_id),
            None,
        )
        if record is None:
            return False
        record.update(
            {
                "status": str(_value(getattr(order, "status", record.get("status")))).lower(),
                "filled_qty": _float(getattr(order, "filled_qty", None)),
                "filled_avg_price": _float(getattr(order, "filled_avg_price", None)),
                "filled_at": _iso(getattr(order, "filled_at", None)),
            }
        )
        self._write(path, payload, now)
        return True

    def pending_order_ids(self) -> list[str]:
        _, payload = self._load()
        return [
            trade["order_id"]
            for trade in payload.get("trades", [])
            if trade.get("status") not in TERMINAL_STATUSES
        ]

    def status(self) -> dict:
        now = self._now()
        path, payload = self._load(now)
        return {
            "file": str(path),
            "summary": self._summary(payload.get("trades", [])),
        }
