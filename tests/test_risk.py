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


def test_exit_rules_use_technical_stop_and_early_profit_protection():
    risk = RiskManager(config(), capital=50_000)
    assert risk.exit_reason(100, 98, 100, 98) == "technical_stop"
    assert risk.exit_reason(100, 105, 105, 98) == "take_profit"
    assert risk.exit_reason(100, 100.05, 101.0, 98) == "profit_protection"
    assert risk.exit_reason(100, 100.5, 102.0, 98) == "early_trailing_stop"
    assert risk.exit_reason(100, 100.4, 100.4, 98) is None



def test_model_room_caps_entry_notional():
    risk = RiskManager(config(), model="C", capital=50_000)
    result = risk.entry_check(10.0, 9.5, False, model_room=2_000)
    assert result.allowed
    assert result.notional == 2_000


def test_model_a_cuts_weakness_before_wider_stop():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    entered = datetime(2026, 9, 30, 10, 0, tzinfo=eastern)
    risk = RiskManager(config(), model="A", capital=50_000)

    # A quick -0.6% move is enough to cut a failed breakout even if the
    # original technical stop is much lower.
    assert (
        risk.exit_reason(
            100.0,
            99.4,
            100.1,
            97.5,
            entered_at=entered,
            now=entered + timedelta(minutes=2),
        )
        == "model_a_weakness_exit"
    )


def test_model_a_exits_stalled_failed_breakout_after_five_minutes():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    entered = datetime(2026, 9, 30, 10, 0, tzinfo=eastern)
    risk = RiskManager(config(), model="A", capital=50_000)

    assert (
        risk.exit_reason(
            100.0,
            99.8,
            100.2,
            97.5,
            entered_at=entered,
            now=entered + timedelta(minutes=6),
        )
        == "model_a_failed_breakout"
    )


def test_model_a_still_supports_multiple_open_positions():
    risk = RiskManager(config(), model="A", capital=50_000)
    assert risk.max_open_positions == 3
    assert risk.max_entries_per_cycle == 2


def test_strong_move_uses_wider_trailing_regime():
    risk = RiskManager(config(), capital=50_000)
    # At +5% high watermark, the main 1.5% trail replaces the earlier 0.6% trail.
    assert risk.exit_reason(100, 104.0, 105.0, 98.0) is None
    assert risk.exit_reason(100, 103.4, 105.0, 98.0) == "trailing_stop"


def test_breakeven_does_not_reset_loss_streak():
    risk = RiskManager(config(max_consecutive_losses=2), capital=50_000)
    risk.record_closed_trade(-100)
    risk.record_closed_trade(0)
    assert risk.consecutive_losses == 1
