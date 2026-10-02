from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from intraday_lab.config import Settings
from intraday_lab.finance_stack import FinanceIntelligenceStack
from intraday_lab.model_c import ModelCAdvisor
from intraday_lab.models import Decision
from intraday_lab.risk import RiskManager
from intraday_lab.strategy import OpeningRangeVwapStrategy


ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "sample-data"


def load_sample_bars(path: Path = SAMPLE_DIR / "market-bars.csv") -> dict[str, pd.DataFrame]:
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Sample bars missing columns: {sorted(missing)}")

    result: dict[str, pd.DataFrame] = {}
    for symbol, rows in frame.groupby("symbol", sort=False):
        symbol_frame = rows.drop(columns=["symbol"]).set_index("timestamp").sort_index()
        result[str(symbol).upper()] = symbol_frame
    return result


def load_json(name: str) -> dict:
    with (SAMPLE_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def market_alignment(bars: dict[str, pd.DataFrame]) -> bool:
    for benchmark in ("SPY", "QQQ"):
        frame = bars.get(benchmark)
        if frame is not None and len(frame) >= 2:
            if float(frame.iloc[-1]["close"]) > float(frame.iloc[-2]["close"]):
                return True
    return False


def size_signal(config: Settings, model: str, signal, capital: float) -> dict:
    risk = RiskManager(config, model=model, capital=capital)
    decision = risk.entry_check(signal.price, signal.stop_price, False)
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "qty": round(decision.quantity, 4),
        "notional": round(decision.notional, 2),
        "dollars_at_risk": round(decision.dollars_at_risk, 2),
        "model_capital": round(capital, 2),
    }


def validate_live_model_c(
    *,
    decision: dict,
    shortlist: list[dict],
    signal,
    risk: dict | None,
    min_confidence: float,
) -> dict[str, bool]:
    """Validate live inference semantics without requiring Fin-R1 to choose BUY."""
    decision_value = str(decision.get("decision", "")).upper()
    allowed_symbols = {str(item["symbol"]).upper() for item in shortlist}
    selected_symbol = str(decision.get("symbol") or "").upper()

    try:
        confidence = float(decision["confidence"])
        confidence_valid = 0.0 <= confidence <= 1.0
    except (KeyError, TypeError, ValueError):
        confidence = 0.0
        confidence_valid = False

    checks = {
        "C decision is BUY or HOLD": decision_value in {"BUY", "HOLD"},
        "C confidence is numeric and bounded": confidence_valid,
    }
    if decision_value == "HOLD":
        checks["C HOLD creates no signal"] = signal is None
        checks["C HOLD requires no risk sizing"] = risk is None
        return checks

    if decision_value != "BUY":
        return checks

    def positive_number(name: str) -> bool:
        try:
            return float(decision[name]) > 0.0
        except (KeyError, TypeError, ValueError):
            return False

    eligible = confidence_valid and confidence >= min_confidence
    checks.update(
        {
            "C BUY symbol is in deterministic shortlist": selected_symbol in allowed_symbols,
            "C BUY stop percentage is valid": positive_number("stop_pct"),
            "C BUY take-profit percentage is valid": positive_number("take_profit_pct"),
            "C confidence threshold gates signal": (signal is not None) == eligible,
        }
    )
    if eligible:
        checks["C eligible BUY signal matches selected symbol"] = (
            signal is not None and signal.symbol == selected_symbol
        )
        checks["C eligible BUY has a valid protective stop"] = (
            signal is not None and 0 < signal.stop_price < signal.price
        )
        checks["C eligible BUY passes risk sizing"] = bool(risk and risk.get("allowed"))
    else:
        checks["C ineligible BUY creates no signal or sizing"] = signal is None and risk is None
    return checks


def run_sample(*, live_llm: bool = False, capital_per_model: float = 33_333.33) -> dict:
    bars = load_sample_bars()
    news = load_json("news.json")
    fixture = load_json("model-c-responses.json")
    intelligence_fixture = load_json("model-c-intelligence.json")
    now = bars["SPY"].index.max().to_pydatetime()
    aligned = market_alignment(bars)

    config = Settings(
        api_key="sample",
        secret_key="sample",
        paper=True,
        action_day_mode=True,
    )

    # Model A — controlled opening-range/VWAP breakout.
    signal_a = OpeningRangeVwapStrategy(config.relative_volume_min).evaluate(
        "ALPHA",
        bars["ALPHA"],
        aligned,
        now,
    )

    # Model C — Fin-R1 reasoner + Kronos K-line forecast + FinBERT sentiment.
    # Offline mode uses checked-in deterministic evidence/LLM fixtures. --live-llm
    # runs the real local FinBERT/Kronos stack and calls the configured Fin-R1 endpoint.
    intelligence = FinanceIntelligenceStack.from_settings(config) if live_llm else None
    advisor = ModelCAdvisor(
        config.model_c_llm_base_url,
        config.model_c_llm_model,
        config.model_c_llm_api_key,
        intelligence=intelligence,
    )
    universe = ["ALPHA", "BETA", "GAMMA"]
    shortlist = advisor.shortlist(
        universe,
        bars,
        now=now,
        catalysts={symbol: news.get(symbol) for symbol in universe},
        market_aligned=aligned,
        locked=set(),
        limit=config.model_c_shortlist_size,
    )

    if live_llm:
        shortlist = advisor.enrich_shortlist(
            shortlist,
            bars_by_symbol=bars,
            catalysts={symbol: news.get(symbol) for symbol in universe},
        )
    else:
        shortlist = [
            dict(item) | intelligence_fixture.get(item["symbol"], {})
            for item in shortlist
        ]

    market_context = {
        "timestamp": now.isoformat(),
        "market_aligned": aligned,
        "benchmarks": {
            symbol: {
                "price": float(bars[symbol].iloc[-1]["close"]),
                "previous_bar": float(bars[symbol].iloc[-2]["close"]),
            }
            for symbol in ("SPY", "QQQ")
        },
        "paper_only": True,
        "source": "sample-data",
    }

    if live_llm:
        if not advisor.configured:
            raise RuntimeError(
                "--live-llm requires MODEL_C_LLM_BASE_URL and MODEL_C_LLM_MODEL in .env"
            )
        decision_c = advisor.decide(
            shortlist,
            market_context=market_context,
            timeout_seconds=config.model_c_llm_timeout_seconds,
        )
    else:
        decision_c = dict(fixture["portfolio_manager"])
        decision_c["desk_report"] = fixture["analyst_committee"]

    signal_c, c_rejections = advisor.signal_from_decision(
        shortlist,
        decision_c,
        now=now,
        market_aligned=aligned,
        min_confidence=config.model_c_min_confidence,
        min_rvol=config.model_c_min_rvol,
        require_positive_1m=config.model_c_require_positive_1m,
        require_vwap_or_positive_5m=config.model_c_require_vwap_or_positive_5m,
        max_distance_from_hod_pct=config.model_c_max_distance_from_hod_pct,
        max_extension_from_vwap_pct=config.model_c_max_extension_from_vwap_pct,
    )

    results = {
        "sample_timestamp": now.isoformat(),
        "market_aligned": aligned,
        "source": "synthetic local fixture; no Alpaca orders are submitted",
        "models": {
            "A": {
                "symbol": "ALPHA",
                "decision": signal_a.decision.value,
                "reason": signal_a.reason,
                "price": round(signal_a.price, 4),
                "rvol": round(signal_a.relative_volume, 4),
                "risk": size_signal(config, "A", signal_a, capital_per_model)
                if signal_a.decision == Decision.BUY
                else None,
            },
            "C": {
                "symbol": signal_c.symbol if signal_c else decision_c.get("symbol"),
                "decision": signal_c.decision.value if signal_c else "HOLD",
                "confidence": float(decision_c.get("confidence", 0.0) or 0.0),
                "reason": signal_c.reason if signal_c else decision_c.get("rationale"),
                "execution_rejections": c_rejections,
                "shortlist": [item["symbol"] for item in shortlist],
                "llm_mode": "FIN-R1 LIVE" if live_llm else "FIXTURE",
                "stack": {
                    item["symbol"]: {
                        "finbert": item.get("finbert"),
                        "kronos": item.get("kronos"),
                    }
                    for item in shortlist
                },
                "risk": size_signal(config, "C", signal_c, capital_per_model)
                if signal_c is not None
                else None,
            },
        },
    }

    expected = {"A": results["models"]["A"]["decision"] == "BUY"}
    if live_llm:
        expected.update(
            validate_live_model_c(
                decision=decision_c,
                shortlist=shortlist,
                signal=signal_c,
                risk=results["models"]["C"]["risk"],
                min_confidence=config.model_c_min_confidence,
            )
        )
        results["live_model_c_result"] = {
            "decision": str(decision_c.get("decision", "")).upper(),
            "selected_symbol": decision_c.get("symbol"),
            "confidence": decision_c.get("confidence"),
            "rationale": decision_c.get("rationale"),
            "shortlist": results["models"]["C"]["shortlist"],
            "finbert": {
                symbol: evidence["finbert"]
                for symbol, evidence in results["models"]["C"]["stack"].items()
            },
            "kronos": {
                symbol: evidence["kronos"]
                for symbol, evidence in results["models"]["C"]["stack"].items()
            },
        }
    else:
        expected["C"] = results["models"]["C"]["decision"] == "BUY"
    results["passed"] = all(expected.values())
    results["checks"] = expected
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Models A and C against checked-in synthetic sample data."
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Run real FinBERT + Kronos + Fin-R1 instead of deterministic Model C fixtures.",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=50_000.0,
        help="Virtual capital per model used only for sample risk sizing.",
    )
    args = parser.parse_args()

    result = run_sample(live_llm=args.live_llm, capital_per_model=args.capital)
    if args.live_llm:
        print("Normalized live Model C result")
        print(json.dumps(result["live_model_c_result"], indent=2, default=str))
        print()
    print(json.dumps(result, indent=2, default=str))
    print()
    for model, passed in result["checks"].items():
        print(f"[{'PASS' if passed else 'FAIL'}] Model {model}")
    print("RESULT:", "READY" if result["passed"] else "NOT READY")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
