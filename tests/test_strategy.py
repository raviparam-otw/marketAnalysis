from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from intraday_lab.models import Decision
from intraday_lab.config import Settings
from intraday_lab.strategy import MomentumCatalystStrategy, OpeningRangeVwapStrategy


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


def test_model_b_action_day_can_qualify_without_news_when_momentum_score_is_strong():
    index = pd.date_range("2026-09-25 09:30", periods=30, freq="min", tz=EASTERN)
    close = np.linspace(10.0, 10.5, 30)
    bars = pd.DataFrame(
        {
            "open": close - 0.03,
            "high": close + 0.04,
            "low": close - 0.05,
            "close": close,
            "volume": [100] * 25 + [400, 450, 500, 550, 900],
        },
        index=index,
    )
    cfg = Settings(api_key="x", secret_key="y", paper=True)
    signal = MomentumCatalystStrategy(cfg).evaluate(
        "FAST", bars, True, datetime(2026, 9, 25, 9, 59, tzinfo=EASTERN), None
    )
    assert signal.score >= cfg.momentum_min_score
    assert signal.context["momentum_trigger"] is True


def test_model_a_rejects_one_bar_false_breakout_without_hold_confirmation():
    index = pd.date_range("2026-09-25 09:30", periods=25, freq="min", tz=EASTERN)
    close = np.array([100.0] * 24 + [101.0])
    bars = pd.DataFrame(
        {
            "open": np.array([100.0] * 24 + [100.2]),
            "high": close + 0.1,
            "low": close - 0.2,
            "close": close,
            "volume": [100] * 24 + [1000],
        },
        index=index,
    )
    signal = OpeningRangeVwapStrategy(1.5).evaluate(
        "FALSE", bars, True, datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN)
    )
    assert signal.decision == Decision.HOLD
    assert "breakout hold" in signal.context["failed_conditions"]


def test_model_a_rejects_extended_reentry_chase():
    index = pd.date_range("2026-09-25 09:30", periods=60, freq="min", tz=EASTERN)
    close = np.concatenate([
        np.full(15, 100.0),
        np.linspace(100.2, 108.0, 45),
    ])
    bars = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.1,
            "low": close - 0.2,
            "close": close,
            "volume": [100] * 55 + [400, 500, 600, 700, 1000],
        },
        index=index,
    )
    signal = OpeningRangeVwapStrategy(1.5).evaluate(
        "CHASE", bars, True, datetime(2026, 9, 25, 10, 29, tzinfo=EASTERN)
    )
    assert signal.decision == Decision.HOLD
    assert "not extended" in signal.context["failed_conditions"]
    assert signal.context["extension_from_or_pct"] > 6.0
