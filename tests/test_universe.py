from types import SimpleNamespace

from intraday_lab.universe import asset_is_eligible, rank_candidates, snapshot_candidate


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


def snapshot(price=100, bid=99.9, ask=100.1, volume=20_000):
    return SimpleNamespace(
        latest_trade=SimpleNamespace(price=price),
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask),
        daily_bar=SimpleNamespace(close=price, volume=volume),
        previous_daily_bar=SimpleNamespace(volume=volume),
    )


def test_asset_metadata_exclusions():
    assert asset_is_eligible(asset(), set())
    assert not asset_is_eligible(asset(exchange="OTC"), set())
    assert not asset_is_eligible(asset(tradable=False), set())
    assert not asset_is_eligible(asset(fractionable=False), set())
    assert not asset_is_eligible(asset(name="Example 3X Daily Bull ETF"), set())
    assert asset_is_eligible(asset(name="United Airlines Holdings Inc"), set())
    assert not asset_is_eligible(asset(symbol="AAPL"), {"AAPL"})


def test_snapshot_price_liquidity_and_spread_filters():
    accepted = snapshot_candidate("AAPL", snapshot(), 5, 1_000_000, 0.005)
    assert accepted is not None
    assert accepted.dollar_volume == 2_000_000
    assert snapshot_candidate("LOW", snapshot(price=4), 5, 1_000_000, 0.005) is None
    assert snapshot_candidate("WIDE", snapshot(bid=99, ask=101), 5, 1_000_000, 0.005) is None
    assert snapshot_candidate("THIN", snapshot(volume=1_000), 5, 1_000_000, 0.005) is None


def test_candidates_rank_by_dollar_volume():
    first = snapshot_candidate("AAA", snapshot(volume=30_000), 5, 0, 0.005)
    second = snapshot_candidate("BBB", snapshot(volume=20_000), 5, 0, 0.005)
    assert first and second
    assert rank_candidates([second, first], 1) == ["AAA"]
