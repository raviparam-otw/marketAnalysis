from types import SimpleNamespace

from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

from intraday_lab.broker import PaperBroker


class FakeTrading:
    def __init__(self):
        self.submitted = None

    def submit_order(self, order_data):
        self.submitted = order_data
        return SimpleNamespace(id="entry-1")


def test_model_a_protected_limit_order_builds_oto_stop():
    broker = object.__new__(PaperBroker)
    broker.trading = FakeTrading()

    result = broker.buy_protected_limit_qty(
        "AAPL",
        10,
        101.25,
        99.75,
        "model-a-test",
    )

    order = broker.trading.submitted
    assert result.id == "entry-1"
    assert order.symbol == "AAPL"
    assert float(order.qty) == 10
    assert float(order.limit_price) == 101.25
    assert order.side == OrderSide.BUY
    assert order.time_in_force == TimeInForce.DAY
    assert order.order_class == OrderClass.OTO
    assert float(order.stop_loss.stop_price) == 99.75
