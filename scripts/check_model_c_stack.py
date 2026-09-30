from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from intraday_lab.config import settings
from intraday_lab.finance_stack import FinanceIntelligenceStack
from intraday_lab.model_c import ModelCAdvisor


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample-data"


def load_bars() -> dict[str, pd.DataFrame]:
    frame = pd.read_csv(SAMPLE_DIR / "market-bars.csv", parse_dates=["timestamp"])
    result = {}
    for symbol, rows in frame.groupby("symbol", sort=False):
        result[str(symbol).upper()] = (
            rows.drop(columns=["symbol"]).set_index("timestamp").sort_index()
        )
    return result


def main() -> int:
    bars = load_bars()
    with (SAMPLE_DIR / "news.json").open("r", encoding="utf-8") as handle:
        news = json.load(handle)

    stack = FinanceIntelligenceStack.from_settings(settings)
    advisor = ModelCAdvisor(
        settings.model_c_llm_base_url,
        settings.model_c_llm_model,
        settings.model_c_llm_api_key,
        intelligence=stack,
    )

    print("Model C stack status")
    print(json.dumps(advisor.status(execution_enabled=False), indent=2))

    if not advisor.configured:
        print("\n[FAIL] Fin-R1 endpoint is not configured.")
        print("Set MODEL_C_LLM_BASE_URL and MODEL_C_LLM_MODEL in .env.")
        return 1

    now = bars["SPY"].index.max().to_pydatetime()
    shortlist = advisor.shortlist(
        ["ALPHA", "BETA", "GAMMA"],
        bars,
        now=now,
        catalysts={symbol: news.get(symbol) for symbol in ("ALPHA", "BETA", "GAMMA")},
        market_aligned=True,
        locked=set(),
        limit=3,
    )

    enriched = advisor.enrich_shortlist(
        shortlist,
        bars_by_symbol=bars,
        catalysts={symbol: news.get(symbol) for symbol in ("ALPHA", "BETA", "GAMMA")},
    )

    finbert_ok = any(
        item.get("finbert") and not item["finbert"].get("error")
        for item in enriched
        if item.get("catalyst_headline")
    )
    kronos_ok = bool(enriched) and all(
        item.get("kronos") and not item["kronos"].get("error")
        for item in enriched
    )

    print("\nEnriched shortlist")
    print(json.dumps(enriched, indent=2, default=str))

    if not finbert_ok:
        print("\n[FAIL] FinBERT did not produce a sentiment result.")
        return 1
    print("\n[PASS] FinBERT sentiment")

    if not kronos_ok:
        errors = {
            item.get("symbol"): item.get("kronos", {}).get("error")
            for item in enriched
            if item.get("kronos", {}).get("error")
        }
        print("[FAIL] Kronos did not produce forecasts for the shortlist.")
        print("Kronos device:", settings.model_c_kronos_device)
        print("Kronos errors:", json.dumps(errors, indent=2))
        return 1
    print("[PASS] Kronos forecasts")

    decision = advisor.decide(
        enriched,
        market_context={
            "timestamp": now.isoformat(),
            "market_aligned": True,
            "benchmarks": {
                "SPY": {"price": float(bars["SPY"].iloc[-1]["close"])},
                "QQQ": {"price": float(bars["QQQ"].iloc[-1]["close"])},
            },
            "paper_only": True,
            "source": "sample-data-stack-check",
        },
        timeout_seconds=settings.model_c_llm_timeout_seconds,
    )

    print("\nFin-R1 decision")
    print(json.dumps(decision, indent=2, default=str))
    valid = str(decision.get("decision", "")).upper() in {"BUY", "HOLD"}
    if not valid:
        print("\n[FAIL] Fin-R1 returned an invalid decision.")
        return 1

    print("\n[PASS] Fin-R1 reasoning endpoint")
    print("RESULT: MODEL C FULL STACK READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
