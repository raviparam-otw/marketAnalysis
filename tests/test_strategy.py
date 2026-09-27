from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from intraday_lab.models import Decision
from intraday_lab.strategy import OpeningRangeVwapStrategy


EASTERN = ZoneInfo("America/New_York")


def test_breakout_signal_when_all_confirmations_pass():
    index = pd.date_range("2026-09-25 09:30", periods=25, freq="min", tz=EASTERN)
    close = np.concatenate([np.full(20, 100.0), np.array([100.2, 100.4, 100.7, 101.0, 102.0])])
    bars = pd.DataFrame(
        {"open": close - .1, "high": close + .1, "low": close - .2, "close": close, "volume": [100]*24+[1000]},
        index=index,
    )
    signal = OpeningRangeVwapStrategy(1.5).evaluate(
        "TEST", bars, True, datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN)
    )
    assert signal.decision == Decision.BUY
    assert signal.relative_volume > 1.5


def test_no_trade_without_market_alignment():
    index = pd.date_range("2026-09-25 09:30", periods=25, freq="min", tz=EASTERN)
    bars = pd.DataFrame(
        {"open": 100, "high": 101, "low": 99, "close": 102, "volume": [100]*24+[1000]}, index=index
    )
    signal = OpeningRangeVwapStrategy().evaluate(
        "TEST", bars, False, datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN)
    )
    assert signal.decision == Decision.HOLD
    assert "market alignment" in signal.reason
