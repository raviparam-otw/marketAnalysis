import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from intraday_lab.journal import TradeJournal


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 10, 15, tzinfo=EASTERN)


def order(order_id="o-1", status="accepted", **changes):
    values = {
        "id": order_id,
        "client_order_id": f"client-{order_id}",
        "status": status,
        "submitted_at": NOW,
        "filled_qty": None,
        "filled_avg_price": None,
        "filled_at": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_writes_every_order_immediately(tmp_path):
    journal = TradeJournal(tmp_path, now_fn=lambda: NOW)
    journal.ensure_today()
    journal.record_order(
        order(),
        side="BUY",
        symbol="AAPL",
        reason="opening_range_breakout",
        requested_notional=5000,
        signal_price=201.25,
    )
    journal.record_order(
        order("o-2"),
        side="SELL",
        symbol="AAPL",
        reason="take_profit",
        requested_qty=24.84,
        signal_price=211.31,
        approximate_pl=250.50,
    )

    path = tmp_path / "trades-2026-09-28.json"
    payload = json.loads(path.read_text())
    assert [trade["order_id"] for trade in payload["trades"]] == ["o-1", "o-2"]
    assert payload["summary"]["orders"] == 2
    assert payload["summary"]["buy_orders"] == 1
    assert payload["summary"]["sell_orders"] == 1
    assert payload["summary"]["estimated_realized_pl"] == 250.50


def test_fill_update_survives_restart(tmp_path):
    journal = TradeJournal(tmp_path, now_fn=lambda: NOW)
    journal.record_order(order(), side="BUY", symbol="MSFT", reason="entry")

    restarted = TradeJournal(tmp_path, now_fn=lambda: NOW)
    restarted.update_order(
        order(
            status="filled",
            filled_qty="10",
            filled_avg_price="415.25",
            filled_at=NOW,
        )
    )

    payload = json.loads((tmp_path / "trades-2026-09-28.json").read_text())
    trade = payload["trades"][0]
    assert trade["status"] == "filled"
    assert trade["filled_qty"] == 10.0
    assert trade["filled_avg_price"] == 415.25
    assert payload["summary"]["filled_orders"] == 1
    assert restarted.pending_order_ids() == []
