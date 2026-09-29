from intraday_lab.config import Settings
from intraday_lab.risk import RiskManager


def config(**changes):
    values = dict(api_key="x", secret_key="y", paper=True)
    values.update(changes)
    return Settings(**values)


def test_risk_scales_from_session_allocation():
    risk = RiskManager(
        config(risk_per_trade_pct=0.005, max_position_pct=0.25),
        capital=40_000,
    )
    result = risk.entry_check(10.0, 9.5, False)
    assert result.allowed
    assert result.quantity == 400
    assert result.notional == 4_000
    assert result.dollars_at_risk == 200
    assert risk.risk_budget == 200
    assert risk.max_position_notional == 10_000


def test_notional_cap_limits_position():
    risk = RiskManager(
        config(risk_per_trade_pct=0.05, max_position_pct=0.10),
        capital=50_000,
    )
    result = risk.entry_check(10.0, 9.5, False)
    assert result.allowed
    assert result.quantity == 500
    assert result.notional == 5_000


def test_available_cash_and_global_room_limit_position():
    risk = RiskManager(config(), capital=50_000)
    result = risk.entry_check(
        10.0,
        9.5,
        False,
        available_cash=1_500,
        global_room=1_000,
    )
    assert result.allowed
    assert result.notional == 1_000
    assert result.quantity == 100


def test_consecutive_loss_limit_blocks_entry():
    risk = RiskManager(config(max_consecutive_losses=2), capital=50_000)
    risk.record_closed_trade(-100)
    risk.record_closed_trade(-100)
    result = risk.entry_check(10, 9.5, False)
    assert not result.allowed
    assert "Consecutive" in result.reason


def test_daily_loss_limit_scales_with_capital():
    risk = RiskManager(config(daily_loss_pct=0.02), capital=30_000)
    assert risk.daily_loss_limit == 600
    risk.load_performance(
        realized_pl=-600,
        closed_trades=3,
        wins=0,
        losses=3,
        consecutive_losses=1,
    )
    result = risk.entry_check(10, 9.5, False)
    assert not result.allowed
    assert "Daily model loss" in result.reason


def test_exit_rules_use_technical_stop():
    risk = RiskManager(config(), capital=50_000)
    assert risk.exit_reason(100, 98, 100, 98) == "technical_stop"
    assert risk.exit_reason(100, 105, 105, 98) == "take_profit"
    assert risk.exit_reason(100, 102.4, 104, 98) == "trailing_stop"
    assert risk.exit_reason(100, 101, 101, 98) is None
