import pytest

from intraday_lab.config import Settings


def test_live_mode_is_rejected():
    config = Settings(api_key="x", secret_key="y", paper=False)
    with pytest.raises(RuntimeError, match="SAFETY LOCK"):
        config.validate()


def test_valid_paper_configuration():
    Settings(api_key="x", secret_key="y", paper=True).validate()
