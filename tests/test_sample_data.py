from sample_test import load_sample_bars, run_sample


def test_sample_fixture_contains_three_model_symbols_and_benchmarks():
    bars = load_sample_bars()
    assert {"ALPHA", "BETA", "GAMMA", "SPY", "QQQ"}.issubset(bars)
    assert all(len(bars[symbol]) >= 30 for symbol in ("ALPHA", "BETA", "GAMMA"))


def test_all_three_models_fire_on_offline_sample_fixture():
    result = run_sample(live_llm=False)
    assert result["passed"] is True
    assert result["models"]["A"]["decision"] == "BUY"
    assert result["models"]["B"]["decision"] == "BUY"
    assert result["models"]["C"]["decision"] == "BUY"
    assert result["models"]["C"]["llm_mode"] == "FIXTURE"
    assert result["models"]["A"]["risk"]["allowed"] is True
    assert result["models"]["B"]["risk"]["allowed"] is True
    assert result["models"]["C"]["risk"]["allowed"] is True
