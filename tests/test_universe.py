from types import SimpleNamespace

from intraday_lab.universe import UniverseCandidate, asset_is_eligible, rank_candidates, snapshot_candidate


def asset(**overrides):
    values = {
        "symbol": "AAPL",
        "name": "Apple Inc Common Stock",
        "exchange": "NASDAQ",
        "status": "active",
        "tradable": True,
        "fractionable": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def snapshot(price=100, bid=99.9, ask=100.1, volume=20_000, prev_close=95, day_open=99):
    return SimpleNamespace(
        latest_trade=SimpleNamespace(price=price),
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask),
        daily_bar=SimpleNamespace(open=day_open, close=price, volume=volume),
        previous_daily_bar=SimpleNamespace(close=prev_close, volume=volume),
    )


def test_asset_metadata_exclusions():
    assert asset_is_eligible(asset(), set())
    assert not asset_is_eligible(asset(exchange="OTC"), set())
    assert not asset_is_eligible(asset(tradable=False), set())
    # Quantity orders mean non-fractionable tradable stocks are now eligible.
    assert asset_is_eligible(asset(fractionable=False), set())
    assert not asset_is_eligible(asset(name="Example 3X Daily Bull ETF"), set())
    assert not asset_is_eligible(asset(symbol="AAPL"), {"AAPL"})


def test_snapshot_calculates_momentum_context():
    accepted = snapshot_candidate("AAPL", snapshot(), 5, 1_000_000, 0.005)
    assert accepted is not None
    assert accepted.dollar_volume == 2_000_000
    assert accepted.change_pct > 5
    assert snapshot_candidate("LOW", snapshot(price=4), 5, 1_000_000, 0.005) is None
    assert snapshot_candidate("WIDE", snapshot(bid=99, ask=101), 5, 1_000_000, 0.005) is None


def test_candidates_rank_movers_before_plain_dollar_volume():
    mover = snapshot_candidate("MOVE", snapshot(price=10, bid=9.99, ask=10.01, volume=200_000, prev_close=8, day_open=9), 1, 0, 0.005)
    liquid = snapshot_candidate("LIQ", snapshot(price=100, volume=100_000, prev_close=99, day_open=99), 1, 0, 0.005)
    assert mover and liquid
    assert rank_candidates([liquid, mover], 1) == ["MOVE"]


def test_long_only_ranking_prefers_positive_mover():
    rising = UniverseCandidate("UP", 10, 1_000_000, 0.001, gap_pct=2.0, change_pct=3.0)
    falling = UniverseCandidate("DOWN", 10, 5_000_000, 0.001, gap_pct=-10.0, change_pct=-12.0)
    assert rank_candidates([falling, rising], 1) == ["UP"]
