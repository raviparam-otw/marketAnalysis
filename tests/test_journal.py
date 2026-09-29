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


def test_writes_model_and_signal_context(tmp_path):
    journal = TradeJournal(tmp_path, now_fn=lambda: NOW)
    journal.record_order(order(), model="B", side="BUY", symbol="TEST", reason="momentum", requested_qty=100, signal_price=10, signal_context={"gap_pct": 12})
    payload = json.loads((tmp_path / "trades-2026-09-28.json").read_text())
    assert payload["trades"][0]["model"] == "B"
    assert payload["trades"][0]["signal_context"]["gap_pct"] == 12


def test_realized_pl_uses_actual_fills(tmp_path):
    journal = TradeJournal(tmp_path, now_fn=lambda: NOW)
    journal.record_order(order("b", status="filled", filled_qty="100", filled_avg_price="10.10", filled_at=NOW), model="A", side="BUY", symbol="XYZ", reason="entry", requested_qty=100)
    journal.record_order(order("s", status="filled", filled_qty="100", filled_avg_price="9.90", filled_at=NOW), model="A", side="SELL", symbol="XYZ", reason="exit", requested_qty=100)
    payload = json.loads((tmp_path / "trades-2026-09-28.json").read_text())
    sell = payload["trades"][1]
    assert sell["realized_pl"] == -20.0
    assert payload["summary"]["realized_pl"] == -20.0
    assert payload["summary"]["models"]["A"]["realized_pl"] == -20.0
