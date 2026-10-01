from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from intraday_lab.engine import TradingEngine
from intraday_lab.experiment import ExperimentSessionStore


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 9, 20, tzinfo=EASTERN)


def test_session_splits_actual_equity_equally_between_a_and_c(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    session = store.create_or_load(83_421.17, NOW)

    assert session.starting_equity == 83_421.17
    assert session.allocations == {"A": 41_710.59, "C": 41_710.58}
    assert round(sum(session.allocations.values()), 2) == 83_421.17


def test_allocation_is_frozen_for_same_trading_day(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    first = store.create_or_load(100_000, NOW)
    second = store.create_or_load(80_000, NOW)

    assert second.session_id == first.session_id
    assert second.starting_equity == 100_000
    assert second.allocations == {"A": 50_000.0, "C": 50_000.0}


def test_session_state_is_persisted(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    session = store.create_or_load(90_000, NOW)
    store.set_state(session, "PAUSED", NOW)

    loaded = store.load_today(NOW)
    assert loaded is not None
    assert loaded.state == "PAUSED"


def test_legacy_session_can_be_replaced_with_two_way_split(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    legacy = store.create_or_load(99_733.74, NOW)
    legacy.allocations = {"A": 33_244.58, "B": 33_244.58, "C": 33_244.58}
    legacy.state = "STOPPED"
    store.save(legacy, NOW)

    migrated = store.replace_with_two_way(99_588.70, NOW)

    assert migrated.session_id != legacy.session_id
    assert migrated.starting_equity == 99_588.70
    assert migrated.allocations == {"A": 49_794.35, "C": 49_794.35}
    assert migrated.state == "READY"
    assert round(sum(migrated.allocations.values()), 2) == 99_588.70
    assert (tmp_path / "session-2026-09-28.legacy.json").exists()


def legacy_engine(store):
    engine = object.__new__(TradingEngine)
    engine.session_store = store
    engine.session = store.load_today(NOW)
    engine.models = {
        name: SimpleNamespace(
            risk=SimpleNamespace(configure_session_capital=lambda capital: None),
            peak_equity=0.0,
            capital=0.0,
            log=lambda *args, **kwargs: None,
        )
        for name in ("A", "C")
    }
    return engine


def test_flat_a_b_legacy_session_migrates_to_a_c(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    legacy = store.create_or_load(100_000, NOW)
    legacy.allocations = {"A": 50_000.0, "B": 50_000.0}
    store.save(legacy, NOW)
    engine = legacy_engine(store)

    migrated = engine.upgrade_session_if_flat(
        account={"equity": 83_421.17}, positions=[], open_orders=[], now=NOW
    )

    assert migrated is True
    assert engine.session.allocations == {"A": 41_710.59, "C": 41_710.58}


def test_legacy_session_is_not_rewritten_with_positions_or_orders(tmp_path):
    for positions, orders in (([SimpleNamespace(symbol="OLD")], []), ([], [object()])):
        case_dir = tmp_path / ("positions" if positions else "orders")
        store = ExperimentSessionStore(case_dir)
        legacy = store.create_or_load(100_000, NOW)
        legacy.allocations = {"A": 50_000.0, "B": 50_000.0}
        store.save(legacy, NOW)
        engine = legacy_engine(store)

        migrated = engine.upgrade_session_if_flat(
            account={"equity": 83_421.17},
            positions=positions,
            open_orders=orders,
            now=NOW,
        )

        assert migrated is False
        assert store.load_today(NOW).allocations == {"A": 50_000.0, "B": 50_000.0}
