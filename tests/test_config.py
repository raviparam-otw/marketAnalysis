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
