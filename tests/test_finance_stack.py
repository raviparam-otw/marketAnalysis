import pandas as pd

from intraday_lab.finance_stack import FinanceIntelligenceStack, KronosForecaster


class FakeSentiment:
    enabled = True
    ready = True
    model_name = "fake-finbert"

    def analyze(self, text):
        return {
            "label": "positive",
            "positive": 0.9,
            "negative": 0.02,
            "neutral": 0.08,
            "signed_score": 0.88,
            "source": self.model_name,
        }


class FakeForecaster:
    enabled = True
    ready = True
    model_name = "fake-kronos"
    tokenizer_name = "fake-tokenizer"

    def forecast(self, bars):
        return {
            "direction": "BULLISH",
            "current_close": 10.0,
            "predicted_close": 10.2,
            "forecast_return_pct": 2.0,
            "forecast_max_upside_pct": 2.5,
            "forecast_max_downside_pct": -0.5,
            "pred_len": 5,
            "source": self.model_name,
        }


def test_stack_enriches_shortlist_with_sentiment_and_forecast():
    stack = FinanceIntelligenceStack(
        sentiment=FakeSentiment(),
        forecaster=FakeForecaster(),
    )
    shortlist = [
        {
            "symbol": "TEST",
            "price": 10.0,
            "catalyst_headline": "Company reports strong demand",
        }
    ]
    bars = {
        "TEST": pd.DataFrame(
            {
                "open": [9.9],
                "high": [10.1],
                "low": [9.8],
                "close": [10.0],
                "volume": [1000],
            }
        )
    }
    enriched = stack.enrich(
        shortlist,
        bars_by_symbol=bars,
        catalysts={"TEST": {"headline": "Company reports strong demand"}},
    )
    assert enriched[0]["finbert"]["label"] == "positive"
    assert enriched[0]["kronos"]["direction"] == "BULLISH"


def test_kronos_prediction_summary():
    prediction = pd.DataFrame(
        {
            "open": [100.1, 100.4],
            "high": [100.8, 101.2],
            "low": [99.8, 100.2],
            "close": [100.5, 101.0],
            "volume": [1000, 1200],
            "amount": [100000, 121200],
        }
    )
    result = KronosForecaster.summarize_prediction(100.0, prediction)
    assert result["direction"] == "BULLISH"
    assert result["forecast_return_pct"] == 1.0
    assert result["pred_len"] == 2
