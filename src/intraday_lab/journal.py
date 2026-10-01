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


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _float(value):
    return None if value in (None, "") else float(value)


class TradeJournal:
    """Crash-safe daily order journal with model/session attribution and fill-based P&L."""

    def __init__(self, directory: str | Path, now_fn: Callable[[], datetime] | None = None) -> None:
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
    def _model_summary(rows: list[dict]) -> dict:
        completed = [
            row for row in rows
            if row.get("side") == "SELL"
            and row.get("status") == "filled"
            and row.get("realized_pl") is not None
        ]
        pnls = [float(row.get("realized_pl") or 0.0) for row in completed]
        wins = sum(p > 0 for p in pnls)
        losses = sum(p < 0 for p in pnls)
        breakeven = sum(p == 0 for p in pnls)
        realized = sum(pnls)
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))

        consecutive_losses = 0
        for pnl in reversed(pnls):
            if pnl < 0:
                consecutive_losses += 1
            elif pnl > 0:
                break
            # Breakeven does not reset a losing streak.

        return {
            "orders": len(rows),
            "closed_trades": len(completed),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate_pct": round((wins / len(completed)) * 100, 2) if completed else 0.0,
            "realized_pl": round(realized, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
            "avg_trade": round(realized / len(completed), 2) if completed else 0.0,
            "best_trade": round(max(pnls), 2) if pnls else 0.0,
            "worst_trade": round(min(pnls), 2) if pnls else 0.0,
            "consecutive_losses": consecutive_losses,
        }

    @classmethod
    def _summary(cls, trades: list[dict]) -> dict:
        filled = [trade for trade in trades if trade.get("status") == "filled"]
        by_model = {}
        for model in ("A", "C"):
            by_model[model] = cls._model_summary(
                [trade for trade in trades if trade.get("model") == model]
            )
        return {
            "orders": len(trades),
            "filled_orders": len(filled),
            "realized_pl": round(sum(item["realized_pl"] for item in by_model.values()), 2),
            "models": by_model,
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
        model: str,
        side: str,
        symbol: str,
        reason: str,
        trade_id: str | None = None,
        session_id: str | None = None,
        requested_qty: float | None = None,
        requested_notional: float | None = None,
        signal_price: float | None = None,
        signal_context: dict | None = None,
    ) -> dict:
        now = self._now()
        path, payload = self._load(now)
        order_id = str(order.id)
        record = {
            "order_id": order_id,
            "client_order_id": str(getattr(order, "client_order_id", "") or "") or None,
            "session_id": session_id,
            "trade_id": trade_id,
            "model": model,
            "symbol": symbol,
            "side": side.upper(),
            "reason": reason,
            "status": str(_value(getattr(order, "status", "submitted"))).lower(),
            "recorded_at": now.isoformat(timespec="seconds"),
            "submitted_at": _iso(getattr(order, "submitted_at", None)) or now.isoformat(timespec="seconds"),
            "requested_notional": _float(requested_notional),
            "requested_qty": _float(requested_qty),
            "signal_price": _float(signal_price),
            "filled_qty": _float(getattr(order, "filled_qty", None)),
            "filled_avg_price": _float(getattr(order, "filled_avg_price", None)),
            "filled_at": _iso(getattr(order, "filled_at", None)),
            "realized_pl": None,
            "signal_context": signal_context or {},
        }
        trades = payload.setdefault("trades", [])
        existing = next((trade for trade in trades if trade.get("order_id") == order_id), None)
        if existing:
            existing.update(record)
            record = existing
        else:
            trades.append(record)
        self._recalculate(trades)
        self._write(path, payload, now)
        return record

    @staticmethod
    def _matching_buy(sell: dict, trades: list[dict]) -> dict | None:
        trade_id = sell.get("trade_id")
        if trade_id:
            return next(
                (
                    trade for trade in trades
                    if trade.get("trade_id") == trade_id
                    and trade.get("side") == "BUY"
                    and trade.get("status") == "filled"
                    and trade.get("filled_avg_price") is not None
                ),
                None,
            )

        # Compatibility fallback for records created before trade_id existed.
        buys = [
            trade for trade in trades
            if trade.get("model") == sell.get("model")
            and trade.get("symbol") == sell.get("symbol")
            and trade.get("side") == "BUY"
            and trade.get("status") == "filled"
            and trade.get("filled_avg_price") is not None
        ]
        return buys[-1] if buys else None

    @classmethod
    def _recalculate(cls, trades: list[dict]) -> None:
        sells = [
            trade for trade in trades
            if trade.get("side") == "SELL"
            and trade.get("status") == "filled"
            and trade.get("filled_avg_price") is not None
        ]
        for sell in sells:
            buy = cls._matching_buy(sell, trades)
            if not buy:
                continue
            qty = min(
                float(sell.get("filled_qty") or 0.0),
                float(buy.get("filled_qty") or 0.0),
            )
            sell["realized_pl"] = round(
                (float(sell["filled_avg_price"]) - float(buy["filled_avg_price"])) * qty,
                4,
            )

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
        self._recalculate(payload.get("trades", []))
        self._write(path, payload, now)
        return True

    def pending_order_ids(self) -> list[str]:
        _, payload = self._load()
        return [
            trade["order_id"]
            for trade in payload.get("trades", [])
            if trade.get("status") not in TERMINAL_STATUSES
        ]

    def open_trades(self) -> list[dict]:
        _, payload = self._load()
        trades = payload.get("trades", [])
        buys = [
            trade for trade in trades
            if trade.get("side") == "BUY"
            and trade.get("status") == "filled"
            and trade.get("model") in {"A", "C"}
        ]
        open_rows = []
        for buy in buys:
            trade_id = buy.get("trade_id")
            if trade_id:
                has_exit = any(
                    row.get("trade_id") == trade_id
                    and row.get("side") == "SELL"
                    and row.get("status") == "filled"
                    for row in trades
                )
            else:
                has_exit = any(
                    row.get("model") == buy.get("model")
                    and row.get("symbol") == buy.get("symbol")
                    and row.get("side") == "SELL"
                    and row.get("status") == "filled"
                    and str(row.get("recorded_at", "")) >= str(buy.get("recorded_at", ""))
                    for row in trades
                )
            if not has_exit:
                open_rows.append(buy)
        return open_rows

    def status(self) -> dict:
        _, payload = self._load()
        trades = payload.get("trades", [])
        return {
            "summary": self._summary(trades),
            "trades": trades,
        }
