from intraday_lab.config import Settings
from intraday_lab.risk import RiskManager


def config(**changes):
    values = dict(api_key="x", secret_key="y", paper=True)
    values.update(changes)
    return Settings(**values)


def test_caps_order_at_max_exposure():
    result = RiskManager(config(max_exposure=100_000)).entry_check(100_000, 400_000, False)
    assert result.allowed
    assert result.notional == 100_000


def test_floor_stops_entries():
    result = RiskManager(config()).entry_check(49_999, 49_999, False)
    assert not result.allowed
    assert "floor" in result.reason.lower()


def test_one_loss_stops_entries_for_day():
    risk = RiskManager(config())
    risk.entry_check(100_000, 100_000, False)
    risk.record_closed_trade(-10)
    result = risk.entry_check(99_990, 99_990, False)
    assert not result.allowed
    assert "losing trade" in result.reason.lower()


def test_exit_rules():
    risk = RiskManager(config())
    assert risk.exit_reason(100, 97.5, 100) == "stop_loss"
    assert risk.exit_reason(100, 105, 105) == "take_profit"
    assert risk.exit_reason(100, 102.4, 104) == "trailing_stop"
    assert risk.exit_reason(100, 101, 101) is None
