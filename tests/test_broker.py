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


def test_model_a_protected_limit_order_floors_fractional_qty_to_whole_shares():
    broker = object.__new__(PaperBroker)
    broker.trading = FakeTrading()

    broker.buy_protected_limit_qty(
        "STM",
        264.157,
        56.34,
        55.20,
        "model-a-fractional-test",
    )

    order = broker.trading.submitted
    assert float(order.qty) == 264
    assert order.order_class == OrderClass.OTO
    assert float(order.stop_loss.stop_price) == 55.20


def test_model_a_protected_limit_order_rejects_sub_one_share_qty():
    broker = object.__new__(PaperBroker)
    broker.trading = FakeTrading()

    try:
        broker.buy_protected_limit_qty(
            "EXPENSIVE",
            0.75,
            1000.00,
            990.00,
            "model-a-sub-one-test",
        )
    except ValueError as exc:
        assert "requires at least 1 whole share" in str(exc)
    else:
        raise AssertionError("Expected ValueError for protected qty below one whole share")
