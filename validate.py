from __future__ import annotations

import argparse
import json

from intraday_lab.validation import print_report, run


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Validate the dual-model paper-trading app without placing any orders.")
    parser.add_argument("--alpaca",action="store_true",help="Also test local .env credentials, paper account connectivity, and read-only market data.")
    parser.add_argument("--no-pytest",action="store_true",help="Skip the pytest suite.")
    parser.add_argument("--json",action="store_true",help="Print JSON instead of the human-readable report.")
    args=parser.parse_args()

    report=run(include_pytest=not args.no_pytest,include_alpaca=args.alpaca)
    if args.json:
        print(json.dumps(report,indent=2))
    else:
        print_report(report)
    raise SystemExit(0 if report["ok"] else 1)
