import pytest

from intraday_lab.config import Settings


def test_live_mode_is_rejected():
    config = Settings(api_key="x", secret_key="y", paper=False)
    with pytest.raises(RuntimeError, match="SAFETY LOCK"):
        config.validate()


def test_valid_paper_configuration():
    Settings(api_key="x", secret_key="y", paper=True).validate()


def test_invalid_scaled_risk_is_rejected():
    config = Settings(
        api_key="x",
        secret_key="y",
        paper=True,
        risk_per_trade_pct=0.03,
        daily_loss_pct=0.02,
    )
    with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
        config.validate()


def test_aggressive_profile_defaults_are_canonical(monkeypatch):
    keys = [
        "RISK_PER_TRADE_PCT",
        "MAX_POSITION_PCT",
        "DAILY_LOSS_PCT",
        "MAX_ACCOUNT_EXPOSURE_PCT",
        "MAX_TRADES_PER_DAY",
        "MAX_CONSECUTIVE_LOSSES",
        "MODEL_A_MAX_OPEN_POSITIONS",
        "MODEL_A_MAX_EXPOSURE_PCT",
        "MODEL_A_BREAKOUT_BUFFER_PCT",
        "MODEL_A_CONFIRMATION_BARS",
        "MODEL_A_MAX_EXTENSION_FROM_OR_PCT",
        "MODEL_A_MAX_EXTENSION_FROM_VWAP_PCT",
        "MODEL_A_REENTRY_COOLDOWN_MINUTES",
        "MODEL_A_MAX_ENTRIES_PER_SYMBOL",
        "RELATIVE_VOLUME_MIN",
        "MODEL_C_RISK_PER_TRADE_PCT",
        "MODEL_C_MAX_POSITION_PCT",
        "MODEL_C_DAILY_LOSS_PCT",
        "MODEL_C_MAX_TRADES_PER_DAY",
        "MODEL_C_MAX_CONSECUTIVE_LOSSES",
        "MODEL_C_MAX_OPEN_POSITIONS",
        "MODEL_C_MAX_EXPOSURE_PCT",
        "MODEL_C_DECISION_INTERVAL_SECONDS",
        "MODEL_C_SHORTLIST_SIZE",
        "MODEL_C_MIN_CONFIDENCE",
        "MODEL_C_MIN_RVOL",
        "MODEL_C_REQUIRE_POSITIVE_1M",
        "MODEL_C_REQUIRE_VWAP_OR_POSITIVE_5M",
        "MODEL_C_MAX_DISTANCE_FROM_HOD_PCT",
        "MODEL_C_MAX_EXTENSION_FROM_VWAP_PCT",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    config = Settings(api_key="x", secret_key="y", paper=True)

    assert config.model_profile("A") == {
        "risk_per_trade_pct": 0.0075,
        "max_position_pct": 0.30,
        "daily_loss_pct": 0.03,
        "max_trades_per_day": 10,
        "max_consecutive_losses": 3,
        "max_open_positions": 4,
        "max_exposure_pct": 0.80,
        "max_entries_per_cycle": 2,
    }
    assert config.relative_volume_min == 1.25
    assert config.model_a_confirmation_bars == 1
    assert config.model_a_breakout_buffer_pct == 0.0008
    assert config.model_a_reentry_cooldown_minutes == 8
    assert config.model_a_max_entries_per_symbol == 3

    assert config.model_profile("C") == {
        "risk_per_trade_pct": 0.0075,
        "max_position_pct": 0.30,
        "daily_loss_pct": 0.035,
        "max_trades_per_day": 12,
        "max_consecutive_losses": 3,
        "max_open_positions": 4,
        "max_exposure_pct": 0.80,
        "max_entries_per_cycle": 1,
    }
    assert config.model_c_decision_interval_seconds == 30
    assert config.model_c_shortlist_size == 8
    assert config.model_c_min_confidence == 0.70
    assert config.model_c_min_rvol == 1.25
    assert config.model_c_require_positive_1m is True
    assert config.model_c_require_vwap_or_positive_5m is True
    assert config.model_c_max_distance_from_hod_pct == 5.0
    assert config.model_c_max_extension_from_vwap_pct == 0.08
