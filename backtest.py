from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

from intraday_lab.backtest import Backtester
from intraday_lab.config import settings


EASTERN = ZoneInfo("America/New_York")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest the paper strategy on Alpaca IEX bars")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", default="backtest-results.json")
    args = parser.parse_args()
    start = datetime.combine(datetime.strptime(args.start, "%Y-%m-%d").date(), time.min, EASTERN)
    end = datetime.combine(datetime.strptime(args.end, "%Y-%m-%d").date(), time.max, EASTERN)
    result = Backtester(settings).run(start, end)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "trades"}, indent=2))
    print(f"Full results written to {args.output}")
