from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Settings, settings
from .experiment import ExperimentSessionStore
from .journal import TradeJournal
from .model_c import ModelCAdvisor
from .models import Decision
from .risk import RiskManager
from .strategy import MomentumCatalystStrategy, OpeningRangeVwapStrategy


EASTERN = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _paper_config() -> Settings:
    return Settings(
        api_key="validation",
        secret_key="validation",
        paper=True,
        starting_balance=100_000,
        model_capital=50_000,
        risk_per_trade_pct=0.005,
        max_position_pct=0.25,
        daily_loss_pct=0.02,
        momentum_require_news=True,
    )


def _bars_for_model_a() -> pd.DataFrame:
    index = pd.date_range("2026-09-25 09:30", periods=25, freq="min", tz=EASTERN)
    close = np.concatenate(
        [np.full(20, 100.0), np.array([100.2, 100.4, 100.7, 101.0, 102.0])]
    )
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.1,
            "low": close - 0.2,
            "close": close,
            "volume": [100] * 24 + [1000],
        },
        index=index,
    )


def _bars_for_model_b() -> pd.DataFrame:
    prior = pd.date_range("2026-09-24 15:59", periods=1, freq="min", tz=EASTERN)
    session = pd.date_range("2026-09-25 09:30", periods=25, freq="min", tz=EASTERN)
    index = prior.append(session)
    close = [8.00] + [9.00] * 18 + [9.05, 9.10, 9.12, 9.14, 9.16, 9.20, 9.35]
    volume = [100] + [100] * 24 + [1000]
    arr = np.array(close, dtype=float)
    return pd.DataFrame(
        {
            "open": arr - 0.03,
            "high": arr + 0.04,
            "low": arr - 0.05,
            "close": arr,
            "volume": volume,
        },
        index=index,
    )


def offline_checks() -> list[Check]:
    checks: list[Check] = []

    for module in (
        "config",
        "models",
        "experiment",
        "finance_stack",
        "universe",
        "strategy",
        "risk",
        "journal",
        "model_c",
        "news",
        "broker",
        "engine",
        "app",
    ):
        try:
            importlib.import_module(f"intraday_lab.{module}")
            checks.append(Check(f"import:{module}", True, "ok"))
        except Exception as exc:
            checks.append(Check(f"import:{module}", False, str(exc)))

    try:
        cfg = _paper_config()
        cfg.validate(require_credentials=False)
        checks.append(Check("paper-safety-config", True, "paper-only configuration accepted"))
    except Exception as exc:
        checks.append(Check("paper-safety-config", False, str(exc)))

    try:
        with tempfile.TemporaryDirectory() as directory:
            store = ExperimentSessionStore(directory)
            now = datetime(2026, 9, 28, 9, 20, tzinfo=EASTERN)
            session = store.create_or_load(83_421.17, now)
            total = round(sum(session.allocations.values()), 2)
            spread = max(session.allocations.values()) - min(session.allocations.values())
            passed = total == 83_421.17 and round(spread, 2) <= 0.01 and set(session.allocations) == {"A", "B", "C"}
            checks.append(
                Check(
                    "dynamic-three-way-split",
                    passed,
                    f"A={session.allocations['A']:.2f}, B={session.allocations['B']:.2f}, "
                    f"C={session.allocations['C']:.2f}",
                )
            )
    except Exception as exc:
        checks.append(Check("dynamic-three-way-split", False, str(exc)))

    try:
        risk = RiskManager(_paper_config(), "B", capital=40_000)
        decision = risk.entry_check(
            10.0,
            9.5,
            False,
            datetime(2026, 9, 25, 10, 0, tzinfo=EASTERN),
        )
        passed = (
            decision.allowed
            and round(decision.quantity, 6) == 800
            and round(decision.dollars_at_risk, 2) == 400
        )
        checks.append(
            Check(
                "scaled-risk-sizing",
                passed,
                f"capital=40000 qty={decision.quantity:.2f}, "
                f"risk={decision.dollars_at_risk:.2f}, notional={decision.notional:.2f}",
            )
        )
    except Exception as exc:
        checks.append(Check("scaled-risk-sizing", False, str(exc)))

    try:
        signal = OpeningRangeVwapStrategy(1.5).evaluate(
            "TESTA",
            _bars_for_model_a(),
            True,
            datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN),
        )
        checks.append(
            Check(
                "model-a-signal",
                signal.decision == Decision.BUY,
                f"{signal.decision}: {signal.reason}",
            )
        )
    except Exception as exc:
        checks.append(Check("model-a-signal", False, str(exc)))

    try:
        cfg = _paper_config()
        catalyst = {"headline": "Validation catalyst"}
        signal = MomentumCatalystStrategy(cfg).evaluate(
            "TESTB",
            _bars_for_model_b(),
            True,
            datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN),
            catalyst,
        )
        checks.append(
            Check(
                "model-b-pipeline",
                signal.price > 0
                and signal.catalyst
                and signal.setup == "momentum_action_day",
                f"{signal.decision}: gap={signal.gap_pct:.2f}% "
                f"change={signal.change_pct:.2f}% rvol={signal.relative_volume:.2f}",
            )
        )
    except Exception as exc:
        checks.append(Check("model-b-pipeline", False, str(exc)))

    try:
        advisor = ModelCAdvisor("", "")
        candidate = advisor.build_candidate(
            "TESTC",
            _bars_for_model_b(),
            now=datetime(2026, 9, 25, 9, 54, tzinfo=EASTERN),
            catalyst={"headline": "Validation catalyst"},
            market_aligned=True,
        )
        checks.append(
            Check(
                "model-c-prefilter",
                candidate is not None and candidate["pre_score"] > 0,
                f"candidate={candidate['symbol'] if candidate else None} "
                f"score={candidate['pre_score'] if candidate else 0}",
            )
        )
    except Exception as exc:
        checks.append(Check("model-c-prefilter", False, str(exc)))

    try:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(
                directory,
                now_fn=lambda: datetime(2026, 9, 28, 10, 15, tzinfo=EASTERN),
            )

            def order(order_id, price):
                return SimpleNamespace(
                    id=order_id,
                    client_order_id=f"v-{order_id}",
                    status="filled",
                    submitted_at=datetime(2026, 9, 28, 10, 15, tzinfo=EASTERN),
                    filled_qty="100",
                    filled_avg_price=str(price),
                    filled_at=datetime(2026, 9, 28, 10, 15, tzinfo=EASTERN),
                )

            journal.record_order(
                order("buy", 10.10),
                model="A",
                side="BUY",
                symbol="VAL",
                reason="validation",
                trade_id="A-validation",
                session_id="validation-session",
                requested_qty=100,
            )
            journal.record_order(
                order("sell", 9.90),
                model="A",
                side="SELL",
                symbol="VAL",
                reason="validation",
                trade_id="A-validation",
                session_id="validation-session",
                requested_qty=100,
            )
            result = journal.status()
            pnl = result["summary"]["models"]["A"]["realized_pl"]
            checks.append(
                Check("actual-fill-pnl", pnl == -20.0, f"realized P&L={pnl}")
            )
    except Exception as exc:
        checks.append(Check("actual-fill-pnl", False, str(exc)))

    return checks


def run_pytest() -> Check:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    output = (proc.stdout + "\n" + proc.stderr).strip()
    tail = " | ".join(output.splitlines()[-4:]) if output else "no output"
    return Check("unit-tests", proc.returncode == 0, tail)


def alpaca_checks() -> list[Check]:
    checks = []
    try:
        settings.validate(require_credentials=True)
        checks.append(Check("local-env", True, "paper credentials and safety lock valid"))
    except Exception as exc:
        return [Check("local-env", False, str(exc))]

    try:
        from .broker import PaperBroker

        broker = PaperBroker(settings)
        account = broker.account_snapshot()
        checks.append(
            Check(
                "alpaca-paper-account",
                not account.get("trading_blocked", False),
                f"connected; equity={account.get('equity', 0):,.2f}; "
                f"status={account.get('status')}",
            )
        )
        split = account["equity"] / 3
        checks.append(
            Check(
                "alpaca-allocation-preview",
                account["equity"] > 0,
                f"current equity={account['equity']:.2f}; each model≈{split:.2f}",
            )
        )
    except Exception as exc:
        checks.append(Check("alpaca-paper-account", False, str(exc)))

    try:
        from .broker import PaperBroker

        broker = PaperBroker(settings)
        # Read-only validation must also work after market close, on weekends,
        # and around holidays. Two hours can legitimately contain no equity
        # bars, so inspect up to seven calendar days instead.
        bars = broker.minute_bars(["SPY"], lookback_hours=24 * 7)
        spy = bars.get("SPY", [])
        count = len(spy)
        latest = None
        if count:
            latest = getattr(spy.index[-1], "isoformat", lambda: str(spy.index[-1]))()
        detail = f"SPY bars returned={count}"
        if latest:
            detail += f"; latest={latest}"
        checks.append(Check("alpaca-market-data", count > 0, detail))
    except Exception as exc:
        checks.append(Check("alpaca-market-data", False, str(exc)))
    return checks


def run(include_pytest: bool = True, include_alpaca: bool = False) -> dict:
    checks = offline_checks()
    if include_pytest:
        checks.append(run_pytest())
    if include_alpaca:
        checks.extend(alpaca_checks())
    passed = sum(check.passed for check in checks)
    return {
        "passed": passed,
        "failed": len(checks) - passed,
        "ok": all(check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
    }


def print_report(report: dict) -> None:
    print("\nIntraday Lab validation")
    print("=" * 72)
    for check in report["checks"]:
        icon = "PASS" if check["passed"] else "FAIL"
        print(f"[{icon}] {check['name']:<24} {check['detail']}")
    print("-" * 72)
    print(f"Passed: {report['passed']}   Failed: {report['failed']}")
    print("RESULT:", "READY" if report["ok"] else "NOT READY")
