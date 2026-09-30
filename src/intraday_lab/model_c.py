from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import request
from zoneinfo import ZoneInfo

import pandas as pd

from .models import Decision, Signal


EASTERN = ZoneInfo("America/New_York")


def _clean_json_text(text: str) -> str:
    """Extract a JSON object even when a reasoning model emits think/answer wrappers."""
    value = text.strip()

    answer = re.search(r"<answer>\s*(.*?)\s*</answer>", value, flags=re.I | re.S)
    if answer:
        value = answer.group(1).strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, flags=re.I | re.S)
    if fenced:
        value = fenced.group(1).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, consumed = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return value[index : index + consumed]

    return value


@dataclass(frozen=True)
class ModelCAdvisor:
    """Model C: Fin-R1 reasoner + Kronos K-line forecast + FinBERT sentiment.

    The deterministic scanner creates a small shortlist. FinBERT scores supplied
    finance headlines, Kronos forecasts short-horizon K-lines, and Fin-R1 performs
    the analyst/risk/portfolio reasoning. The broker remains outside this class;
    only TradingEngine can submit PAPER orders.
    """

    base_url: str
    model: str
    api_key: str = ""
    intelligence: Any | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip())

    def status(self, execution_enabled: bool = False) -> dict:
        active = self.configured and execution_enabled
        stack = self.intelligence.status() if self.intelligence is not None else {}
        return {
            "name": "C",
            "role": "ADAPTIVE",
            "label": "Fin-R1 + Kronos + FinBERT",
            "configured": self.configured,
            "execution_enabled": active,
            "model": self.model or None,
            "base_url": self.base_url or None,
            "reasoner": "Fin-R1",
            "stack": stack,
            "architecture": "FinBERT sentiment + Kronos forecast + Fin-R1 analyst/risk/portfolio manager",
            "message": (
                "Fin-R1 stack configured and eligible for PAPER execution."
                if active
                else (
                    "Fin-R1 connected; PAPER execution disabled by configuration."
                    if self.configured
                    else "Start Fin-R1 and configure MODEL_C_LLM_BASE_URL."
                )
            ),
        }

    def _chat_json(
        self,
        *,
        system: str,
        payload: dict,
        timeout_seconds: int,
        max_tokens: int = 2200,
    ) -> dict:
        if not self.configured:
            raise RuntimeError("Model C LLM endpoint is not configured.")

        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "temperature": 0.2,
            "top_p": 0.8,
            "repetition_penalty": 1.05,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, separators=(",", ":"), default=str),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"]
        parsed = json.loads(_clean_json_text(text))
        if not isinstance(parsed, dict):
            raise ValueError("Model C response must be a JSON object.")
        return parsed

    @staticmethod
    def build_candidate(
        symbol: str,
        bars: pd.DataFrame,
        *,
        now: datetime,
        catalyst: dict | None,
        market_aligned: bool,
    ) -> dict | None:
        if bars is None or bars.empty or len(bars) < 20:
            return None

        frame = bars.copy().sort_index()
        index = pd.DatetimeIndex(frame.index)
        if index.tz is None:
            index = index.tz_localize("UTC")
        frame.index = index.tz_convert(EASTERN)

        session = frame[frame.index.date == now.date()].copy()
        if len(session) < 10:
            return None

        typical = (session["high"] + session["low"] + session["close"]) / 3
        cumvol = session["volume"].cumsum().replace(0, pd.NA)
        session["vwap"] = (typical * session["volume"]).cumsum() / cumvol
        session["avg_volume_20"] = session["volume"].rolling(20, min_periods=5).mean().shift(1)
        session["rvol"] = session["volume"] / session["avg_volume_20"]

        last = session.iloc[-1]
        price = float(last["close"])
        if price <= 0:
            return None

        prior = frame[frame.index.date < now.date()]
        previous_close = float(prior.iloc[-1]["close"]) if not prior.empty else float(session.iloc[0]["open"])
        day_open = float(session.iloc[0]["open"])
        vwap = float(last["vwap"]) if pd.notna(last["vwap"]) else price
        rvol = float(last["rvol"]) if pd.notna(last["rvol"]) else 0.0
        gap_pct = ((day_open / previous_close) - 1) * 100 if previous_close else 0.0
        change_pct = ((price / previous_close) - 1) * 100 if previous_close else 0.0
        hod = float(session["high"].max())
        lod = float(session["low"].min())

        def ret(minutes: int) -> float:
            if len(session) <= minutes:
                return 0.0
            base = float(session.iloc[-minutes - 1]["close"])
            return ((price / base) - 1) * 100 if base else 0.0

        ret_1m = ret(1)
        ret_5m = ret(5)
        ret_15m = ret(15)
        distance_hod_pct = ((price / hod) - 1) * 100 if hod else 0.0
        range_pct = ((hod - lod) / price) * 100 if price else 0.0
        above_vwap = price > vwap

        # Deterministic pre-filter keeps LLM traffic bounded. Model C still makes
        # the final decision; this only prevents spending inference on dead symbols.
        pre_score = (
            max(change_pct, 0.0) * 1.5
            + max(gap_pct, 0.0) * 0.5
            + min(max(rvol, 0.0), 8.0) * 1.5
            + max(ret_5m, 0.0) * 2.0
            + (2.0 if above_vwap else 0.0)
            + (1.5 if distance_hod_pct >= -1.5 else 0.0)
            + (2.0 if catalyst else 0.0)
            + (0.5 if market_aligned else 0.0)
        )
        active = (
            change_pct > 0
            and (
                rvol >= 1.10
                or ret_5m >= 0.50
                or change_pct >= 1.25
                or bool(catalyst)
            )
        )
        if not active:
            return None

        return {
            "symbol": symbol,
            "price": round(price, 6),
            "vwap": round(vwap, 6),
            "above_vwap": above_vwap,
            "gap_pct": round(gap_pct, 4),
            "change_pct": round(change_pct, 4),
            "relative_volume": round(rvol, 4),
            "hod": round(hod, 6),
            "lod": round(lod, 6),
            "distance_hod_pct": round(distance_hod_pct, 4),
            "range_pct": round(range_pct, 4),
            "return_1m_pct": round(ret_1m, 4),
            "return_5m_pct": round(ret_5m, 4),
            "return_15m_pct": round(ret_15m, 4),
            "market_aligned": bool(market_aligned),
            "catalyst": bool(catalyst),
            "catalyst_headline": (catalyst or {}).get("headline"),
            "pre_score": round(pre_score, 4),
        }

    def shortlist(
        self,
        symbols: list[str],
        bars_by_symbol: dict,
        *,
        now: datetime,
        catalysts: dict[str, dict | None],
        market_aligned: bool,
        locked: set[str],
        limit: int,
    ) -> list[dict]:
        candidates = []
        for symbol in symbols:
            if symbol in locked:
                continue
            candidate = self.build_candidate(
                symbol,
                bars_by_symbol.get(symbol),
                now=now,
                catalyst=catalysts.get(symbol),
                market_aligned=market_aligned,
            )
            if candidate:
                candidates.append(candidate)
        candidates.sort(key=lambda item: item["pre_score"], reverse=True)
        return candidates[: max(1, limit)]

    def enrich_shortlist(
        self,
        shortlist: list[dict],
        *,
        bars_by_symbol: dict[str, pd.DataFrame],
        catalysts: dict[str, dict | None],
    ) -> list[dict]:
        if self.intelligence is None:
            return shortlist
        return self.intelligence.enrich(
            shortlist,
            bars_by_symbol=bars_by_symbol,
            catalysts=catalysts,
        )

    def decide(
        self,
        shortlist: list[dict],
        *,
        market_context: dict,
        timeout_seconds: int,
    ) -> dict:
        if not shortlist:
            return {
                "decision": "HOLD",
                "confidence": 0.0,
                "rationale": "No symbols passed deterministic activity pre-filter.",
            }

        analyst_system = (
            "You are Fin-R1 acting as the analyst committee of an intraday trading desk. "
            "Act as four concise specialists: technical analyst, financial-news/sentiment analyst, "
            "bull researcher, and bear researcher. The input may include deterministic market features, "
            "ProsusAI FinBERT sentiment probabilities, and a Kronos short-horizon K-line forecast. "
            "Treat FinBERT and Kronos as evidence, not certainty. Evaluate only supplied evidence; never "
            "invent fundamentals, prices, headlines, forecasts, or events. This is long-only PAPER trading. "
            "Return JSON only with key 'candidates', an array. Each item must contain symbol, technical_view, "
            "sentiment_view, forecast_view, bull_case, bear_case, risk_flags, and desk_score from 0 to 100. "
            "Keep each text field under 40 words."
        )
        analyst_report = self._chat_json(
            system=analyst_system,
            payload={"market": market_context, "shortlist": shortlist},
            timeout_seconds=timeout_seconds,
        )

        manager_system = (
            "You are Fin-R1 acting as the portfolio manager and risk manager of an intraday PAPER-trading desk. "
            "Use the raw shortlist, FinBERT sentiment, Kronos forecast, and analyst committee report. "
            "Choose at most ONE long entry. Prefer HOLD when evidence conflicts, when the Kronos downside "
            "meaningfully outweighs upside, or when sentiment is strongly negative without offsetting evidence. "
            "Never select a symbol outside the shortlist. Return JSON only with: decision ('BUY' or 'HOLD'), "
            "symbol (string or null), confidence (0 to 1), rationale (max 60 words), risks (array of short strings), "
            "invalidation (max 30 words), stop_pct (decimal, normally 0.005 to 0.03), "
            "take_profit_pct (decimal, normally 0.01 to 0.08)."
        )
        final = self._chat_json(
            system=manager_system,
            payload={
                "market": market_context,
                "shortlist": shortlist,
                "analyst_committee": analyst_report,
            },
            timeout_seconds=timeout_seconds,
        )
        final["desk_report"] = analyst_report
        return final

    @staticmethod
    def signal_from_decision(
        shortlist: list[dict],
        decision: dict,
        *,
        now: datetime,
        market_aligned: bool,
        min_confidence: float,
    ) -> Signal | None:
        if str(decision.get("decision", "HOLD")).upper() != "BUY":
            return None

        symbol = str(decision.get("symbol") or "").upper()
        candidate = next((item for item in shortlist if item["symbol"].upper() == symbol), None)
        if not candidate:
            return None

        try:
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            return None

        try:
            stop_pct = float(decision.get("stop_pct", 0.015))
        except (TypeError, ValueError):
            stop_pct = 0.015
        stop_pct = min(0.03, max(0.005, stop_pct))

        price = float(candidate["price"])
        vwap = float(candidate["vwap"])
        stop = price * (1 - stop_pct)
        if 0 < vwap < price:
            stop = max(stop, vwap * 0.997)
        stop = min(stop, price * 0.998)

        rationale = str(decision.get("rationale") or "LLM portfolio manager selected entry.")
        context = {
            "llm_confidence": round(confidence, 4),
            "llm_rationale": rationale,
            "llm_risks": decision.get("risks") or [],
            "llm_invalidation": decision.get("invalidation"),
            "llm_stop_pct": stop_pct,
            "llm_take_profit_pct": decision.get("take_profit_pct"),
            "llm_desk_report": decision.get("desk_report") or {},
            "candidate": candidate,
        }
        return Signal(
            symbol=symbol,
            decision=Decision.BUY,
            price=price,
            vwap=vwap,
            opening_high=float(candidate["hod"]),
            relative_volume=float(candidate["relative_volume"]),
            market_aligned=market_aligned,
            reason=f"LLM desk BUY {confidence:.0%}: {rationale}",
            timestamp=now,
            setup="llm_multi_agent",
            gap_pct=float(candidate["gap_pct"]),
            change_pct=float(candidate["change_pct"]),
            stop_price=stop,
            catalyst=bool(candidate["catalyst"]),
            catalyst_headline=candidate.get("catalyst_headline"),
            score=confidence * 100 + float(candidate["pre_score"]),
            context=context,
        )
