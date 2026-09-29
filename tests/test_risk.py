from intraday_lab.config import Settings
from intraday_lab.risk import RiskManager


def config(**changes):
    values = dict(api_key="x", secret_key="y", paper=True)
    values.update(changes)
    return Settings(**values)


def test_sizes_position_by_dollars_at_risk_and_notional_cap():
    risk = RiskManager(config(risk_per_trade=250, max_position_notional=12_500))
    result = risk.entry_check(10.0, 9.5, False)
    assert result.allowed
    assert result.quantity == 500
    assert result.notional == 5_000
    assert result.dollars_at_risk == 250


def test_notional_cap_limits_position():
    risk = RiskManager(config(risk_per_trade=1000, max_position_notional=5_000))
    result = risk.entry_check(10.0, 9.5, False)
    assert result.allowed
    assert result.quantity == 500
    assert result.notional == 5_000


def test_consecutive_loss_limit_blocks_entry():
    risk = RiskManager(config(max_consecutive_losses=2))
    risk.record_closed_trade(-100)
    risk.record_closed_trade(-100)
    result = risk.entry_check(10, 9.5, False)
    assert not result.allowed
    assert "Consecutive" in result.reason


def test_exit_rules_use_technical_stop():
    risk = RiskManager(config())
    assert risk.exit_reason(100, 98, 100, 98) == "technical_stop"
    assert risk.exit_reason(100, 105, 105, 98) == "take_profit"
    assert risk.exit_reason(100, 102.4, 104, 98) == "trailing_stop"
    assert risk.exit_reason(100, 101, 101, 98) is None
