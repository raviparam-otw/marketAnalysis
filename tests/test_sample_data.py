import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sample_test", ROOT / "sample_test.py")
sample_test = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sample_test)

load_sample_bars = sample_test.load_sample_bars
run_sample = sample_test.run_sample
validate_live_model_c = sample_test.validate_live_model_c


def test_sample_fixture_contains_a_c_candidate_symbols_and_benchmarks():
    bars = load_sample_bars()
    assert {"ALPHA", "BETA", "GAMMA", "SPY", "QQQ"}.issubset(bars)
    assert all(len(bars[symbol]) >= 30 for symbol in ("ALPHA", "BETA", "GAMMA"))


def test_models_a_and_c_fire_on_offline_sample_fixture():
    result = run_sample(live_llm=False)
    assert result["passed"] is True
    assert result["models"]["A"]["decision"] == "BUY"
    assert result["models"]["C"]["decision"] == "BUY"
    assert result["models"]["C"]["llm_mode"] == "FIXTURE"
    assert result["models"]["A"]["risk"]["allowed"] is True
    assert result["models"]["C"]["risk"]["allowed"] is True


def test_live_model_c_hold_is_a_success_without_signal_or_risk():
    checks = validate_live_model_c(
        decision={"decision": "HOLD", "symbol": None, "confidence": 0.55},
        shortlist=[{"symbol": "ALPHA"}],
        signal=None,
        risk=None,
        min_confidence=0.68,
    )
    assert all(checks.values())


def test_live_model_c_rejects_invalid_or_invented_decisions():
    invalid = validate_live_model_c(
        decision={"decision": "WAIT", "confidence": 0.9},
        shortlist=[{"symbol": "ALPHA"}],
        signal=None,
        risk=None,
        min_confidence=0.68,
    )
    assert invalid["C decision is BUY or HOLD"] is False

    invented = validate_live_model_c(
        decision={
            "decision": "BUY",
            "symbol": "MADEUP",
            "confidence": 0.9,
            "stop_pct": 0.01,
            "take_profit_pct": 0.02,
        },
        shortlist=[{"symbol": "ALPHA"}],
        signal=None,
        risk=None,
        min_confidence=0.68,
    )
    assert invented["C BUY symbol is in deterministic shortlist"] is False
