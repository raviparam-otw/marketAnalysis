from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd


class FinBERTSentiment:
    """Lazy local ProsusAI/finbert sentiment provider.

    Heavy ML dependencies are imported only when sentiment is actually requested,
    keeping normal CI/tests and Models A/B lightweight.
    """

    def __init__(self, model_name: str, enabled: bool = True) -> None:
        self.model_name = model_name
        self.enabled = enabled
        self._pipeline = None

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and importlib.util.find_spec("transformers") is not None
            and importlib.util.find_spec("torch") is not None
        )

    def _load(self):
        if not self.enabled:
            raise RuntimeError("FinBERT is disabled.")
        if self._pipeline is None:
            try:
                from transformers import pipeline
            except ImportError as exc:
                raise RuntimeError(
                    "FinBERT dependencies are missing. Install requirements-model-c.txt."
                ) from exc
            import torch

            if torch.cuda.is_available():
                device = 0
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = -1

            self._pipeline = pipeline(
                "text-classification",
                model=self.model_name,
                tokenizer=self.model_name,
                device=device,
            )
        return self._pipeline

    def analyze(self, text: str | None) -> dict:
        if not text:
            return {
                "label": "neutral",
                "positive": 0.0,
                "negative": 0.0,
                "neutral": 1.0,
                "signed_score": 0.0,
                "source": self.model_name,
                "empty": True,
            }

        pipe = self._load()
        raw = pipe(text, top_k=None, truncation=True)
        if raw and isinstance(raw[0], list):
            raw = raw[0]

        scores = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
        for item in raw or []:
            label = str(item.get("label", "")).lower()
            if label in scores:
                scores[label] = float(item.get("score", 0.0) or 0.0)

        if not any(scores.values()) and raw:
            # Compatibility fallback for transformers versions returning only top-1.
            top = raw[0]
            label = str(top.get("label", "neutral")).lower()
            if label not in scores:
                label = "neutral"
            scores[label] = float(top.get("score", 0.0) or 0.0)

        label = max(scores, key=scores.get)
        return {
            "label": label,
            **{key: round(value, 6) for key, value in scores.items()},
            "signed_score": round(scores["positive"] - scores["negative"], 6),
            "source": self.model_name,
            "empty": False,
        }


class KronosForecaster:
    """Lazy local Kronos-small K-line forecast provider.

    The official Kronos repository is cloned outside Git into .models/Kronos.
    Model/tokenizer weights are resolved from Hugging Face on first load and then
    use the local Hugging Face cache.
    """

    def __init__(
        self,
        *,
        repo_path: str,
        model_name: str,
        tokenizer_name: str,
        lookback: int = 120,
        pred_len: int = 5,
        enabled: bool = True,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name
        self.lookback = lookback
        self.pred_len = pred_len
        self.enabled = enabled
        self._predictor = None

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and self.repo_path.exists()
            and importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("huggingface_hub") is not None
        )

    def _load(self):
        if not self.enabled:
            raise RuntimeError("Kronos is disabled.")
        if not self.repo_path.exists():
            raise RuntimeError(
                f"Kronos repository not found at {self.repo_path}. "
                "Run scripts/setup_model_c_macos.sh first."
            )
        if self._predictor is not None:
            return self._predictor

        repo = str(self.repo_path.resolve())
        if repo not in sys.path:
            sys.path.insert(0, repo)

        try:
            from model import Kronos, KronosPredictor, KronosTokenizer
        except ImportError as exc:
            raise RuntimeError(
                f"Could not import Kronos from {repo}. "
                "Run scripts/setup_model_c_macos.sh."
            ) from exc

        tokenizer = KronosTokenizer.from_pretrained(self.tokenizer_name)
        model = Kronos.from_pretrained(self.model_name)
        self._predictor = KronosPredictor(model, tokenizer, max_context=512)
        return self._predictor

    @staticmethod
    def _prepare_frame(bars: pd.DataFrame, lookback: int) -> pd.DataFrame:
        if bars is None:
            raise ValueError("Kronos requires non-empty OHLCV bars.")
        frame = bars.copy().sort_index()
        if frame.empty:
            raise ValueError("Kronos requires non-empty OHLCV bars.")

        required = ["open", "high", "low", "close"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"Kronos bars missing columns: {missing}")

        frame = frame.tail(max(20, lookback)).copy()
        if "volume" not in frame.columns:
            frame["volume"] = 0.0
        if "amount" not in frame.columns:
            frame["amount"] = frame["volume"] * frame[required].mean(axis=1)

        return frame[required + ["volume", "amount"]].astype(float)

    @staticmethod
    def summarize_prediction(current_close: float, prediction: pd.DataFrame) -> dict:
        if prediction is None or prediction.empty:
            raise ValueError("Kronos returned an empty prediction.")

        predicted_close = float(prediction.iloc[-1]["close"])
        predicted_high = float(prediction["high"].max())
        predicted_low = float(prediction["low"].min())
        return_pct = ((predicted_close / current_close) - 1) * 100 if current_close else 0.0
        max_upside_pct = ((predicted_high / current_close) - 1) * 100 if current_close else 0.0
        max_downside_pct = ((predicted_low / current_close) - 1) * 100 if current_close else 0.0

        if return_pct >= 0.25:
            direction = "BULLISH"
        elif return_pct <= -0.25:
            direction = "BEARISH"
        else:
            direction = "FLAT"

        return {
            "direction": direction,
            "current_close": round(current_close, 6),
            "predicted_close": round(predicted_close, 6),
            "forecast_return_pct": round(return_pct, 4),
            "forecast_max_upside_pct": round(max_upside_pct, 4),
            "forecast_max_downside_pct": round(max_downside_pct, 4),
            "pred_len": len(prediction),
        }

    def forecast(self, bars: pd.DataFrame) -> dict:
        predictor = self._load()
        frame = self._prepare_frame(bars, self.lookback)

        index = pd.DatetimeIndex(frame.index)
        if index.tz is None:
            index = index.tz_localize("UTC")
        frame.index = index

        last_time = frame.index[-1]
        future_index = pd.date_range(
            start=last_time + pd.Timedelta(minutes=1),
            periods=self.pred_len,
            freq="min",
            tz=last_time.tz,
        )

        prediction = predictor.predict(
            df=frame,
            x_timestamp=pd.Series(frame.index),
            y_timestamp=pd.Series(future_index),
            pred_len=self.pred_len,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )
        return {
            **self.summarize_prediction(float(frame.iloc[-1]["close"]), prediction),
            "source": self.model_name,
            "tokenizer": self.tokenizer_name,
        }


class FinanceIntelligenceStack:
    """FinBERT + Kronos enrichment consumed by the Fin-R1 portfolio reasoner."""

    def __init__(
        self,
        *,
        sentiment: Any | None = None,
        forecaster: Any | None = None,
    ) -> None:
        self.sentiment = sentiment
        self.forecaster = forecaster

    @classmethod
    def from_settings(cls, config):
        sentiment = FinBERTSentiment(
            config.model_c_finbert_model,
            enabled=config.model_c_finbert_enabled,
        )
        forecaster = KronosForecaster(
            repo_path=config.model_c_kronos_repo_path,
            model_name=config.model_c_kronos_model,
            tokenizer_name=config.model_c_kronos_tokenizer,
            lookback=config.model_c_kronos_lookback,
            pred_len=config.model_c_kronos_pred_len,
            enabled=config.model_c_kronos_enabled,
        )
        return cls(sentiment=sentiment, forecaster=forecaster)

    def status(self) -> dict:
        return {
            "finbert": {
                "enabled": bool(self.sentiment and getattr(self.sentiment, "enabled", True)),
                "ready": bool(self.sentiment and getattr(self.sentiment, "ready", True)),
                "model": getattr(self.sentiment, "model_name", None),
            },
            "kronos": {
                "enabled": bool(self.forecaster and getattr(self.forecaster, "enabled", True)),
                "ready": bool(self.forecaster and getattr(self.forecaster, "ready", True)),
                "model": getattr(self.forecaster, "model_name", None),
                "tokenizer": getattr(self.forecaster, "tokenizer_name", None),
            },
        }

    def enrich(
        self,
        shortlist: list[dict],
        *,
        bars_by_symbol: dict[str, pd.DataFrame],
        catalysts: dict[str, dict | None],
    ) -> list[dict]:
        enriched: list[dict] = []
        for raw in shortlist:
            item = dict(raw)
            symbol = str(item["symbol"]).upper()
            catalyst = catalysts.get(symbol) or {}
            headline = catalyst.get("headline") or item.get("catalyst_headline")

            if self.sentiment and getattr(self.sentiment, "enabled", True):
                try:
                    item["finbert"] = self.sentiment.analyze(headline)
                except Exception as exc:
                    item["finbert"] = {"error": str(exc), "available": False}

            if self.forecaster and getattr(self.forecaster, "enabled", True):
                try:
                    item["kronos"] = self.forecaster.forecast(bars_by_symbol.get(symbol))
                except Exception as exc:
                    item["kronos"] = {"error": str(exc), "available": False}

            enriched.append(item)
        return enriched
