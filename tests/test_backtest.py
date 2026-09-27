import pandas as pd

from intraday_lab.backtest import Backtester
from intraday_lab.config import Settings


def make_tester():
    instance = object.__new__(Backtester)
    instance.config = Settings(api_key="x", secret_key="y", paper=True)
    return instance


def test_simulated_stop_is_conservative():
    index = pd.date_range("2026-09-25 10:01", periods=2, freq="min", tz="America/New_York")
    future = pd.DataFrame({"open": [100, 100], "high": [106, 101], "low": [97, 99],
                           "close": [101, 100]}, index=index)
    price, _, reason = make_tester()._simulate_exit(100, future)
    assert price == 97.5
    assert reason == "stop_loss"


def test_simulated_end_of_day_exit():
    index = pd.date_range("2026-09-25 15:49", periods=2, freq="min", tz="America/New_York")
    future = pd.DataFrame({"open": [100, 101], "high": [102, 102], "low": [99, 100],
                           "close": [101, 101.5]}, index=index)
    price, timestamp, reason = make_tester()._simulate_exit(100, future)
    assert price == 101.5
    assert timestamp == index[-1]
    assert reason == "end_of_day"
