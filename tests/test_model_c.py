from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from intraday_lab.model_c import ModelCAdvisor, _clean_json_text


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=EASTERN)


def bars():
    prior = pd.date_range("2026-09-24 15:59", periods=1, freq="min", tz=EASTERN)
    session = pd.date_range("2026-09-25 09:30", periods=31, freq="min", tz=EASTERN)
    index = prior.append(session)
    close = np.array([10.0] + list(np.linspace(10.20, 10.90, 31)))
    volume = [100] + [120] * 25 + [250, 300, 350, 450, 600, 900]
    return pd.DataFrame(
        {
            "open": close - 0.03,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def test_candidate_prefilter_builds_financial_features():
    advisor = ModelCAdvisor("", "")
    candidate = advisor.build_candidate(
        "TEST",
        bars(),
        now=NOW,
        catalyst={"headline": "Company announces material contract"},
        market_aligned=True,
    )
    assert candidate is not None
    assert candidate["symbol"] == "TEST"
    assert candidate["price"] > candidate["vwap"]
    assert candidate["relative_volume"] > 1
    assert candidate["pre_score"] > 0


def test_signal_requires_confidence_and_valid_shortlist_symbol():
    advisor = ModelCAdvisor("", "")
    candidate = advisor.build_candidate(
        "TEST",
        bars(),
        now=NOW,
        catalyst=None,
        market_aligned=True,
    )
    shortlist = [candidate]

    low, low_rejections = advisor.signal_from_decision(
        shortlist,
        {"decision": "BUY", "symbol": "TEST", "confidence": 0.40, "stop_pct": 0.01},
        now=NOW,
        market_aligned=True,
        min_confidence=0.68,
    )
    assert low is None
    assert low_rejections

    signal, rejections = advisor.signal_from_decision(
        shortlist,
        {
            "decision": "BUY",
            "symbol": "TEST",
            "confidence": 0.82,
            "stop_pct": 0.01,
            "rationale": "Momentum, volume and price structure agree.",
        },
        now=NOW,
        market_aligned=True,
        min_confidence=0.68,
    )
    assert signal is not None
    assert signal.symbol == "TEST"
    assert signal.setup == "llm_multi_agent"
    assert signal.stop_price < signal.price
    assert rejections == []


def test_invalid_symbol_is_never_executed():
    advisor = ModelCAdvisor("", "")
    candidate = advisor.build_candidate("TEST", bars(), now=NOW, catalyst=None, market_aligned=True)
    signal, rejections = advisor.signal_from_decision(
        [candidate],
        {"decision": "BUY", "symbol": "MADEUP", "confidence": 0.99},
        now=NOW,
        market_aligned=True,
        min_confidence=0.68,
    )
    assert signal is None
    assert "symbol not in shortlist" in rejections


def test_fin_r1_think_answer_wrapper_json_is_extracted():
    raw = '<think>financial reasoning here</think><answer>{"decision":"HOLD","confidence":0.73}</answer>'
    parsed = __import__("json").loads(_clean_json_text(raw))
    assert parsed["decision"] == "HOLD"
    assert parsed["confidence"] == 0.73


def test_validator_rejects_llm_buy_when_raw_momentum_is_weak():
    advisor = ModelCAdvisor("", "")
    candidate = {
        "symbol": "WEAK",
        "price": 99.0,
        "vwap": 100.0,
        "above_vwap": False,
        "gap_pct": 4.0,
        "change_pct": 8.0,
        "relative_volume": 0.70,
        "hod": 102.0,
        "lod": 96.0,
        "distance_hod_pct": -2.94,
        "range_pct": 6.06,
        "return_1m_pct": 0.25,
        "return_5m_pct": -0.80,
        "return_15m_pct": -1.20,
        "market_aligned": False,
        "catalyst": False,
        "catalyst_headline": None,
        "pre_score": 20.0,
    }
    signal, rejections = advisor.signal_from_decision(
        [candidate],
        {
            "decision": "BUY",
            "symbol": "WEAK",
            "confidence": 0.90,
            "stop_pct": 0.01,
            "rationale": "Model claims strong momentum.",
        },
        now=NOW,
        market_aligned=False,
        min_confidence=0.70,
        min_rvol=1.25,
        require_positive_1m=True,
        require_vwap_or_positive_5m=True,
    )
    assert signal is None
    assert any("RVOL" in reason for reason in rejections)
    assert any("below VWAP" in reason for reason in rejections)
