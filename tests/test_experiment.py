from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from intraday_lab.engine import TradingEngine
from intraday_lab.experiment import ExperimentSessionStore


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 9, 20, tzinfo=EASTERN)


def test_session_caps_a_and_c_at_fixed_100_each(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    session = store.create_or_load(83_421.17, NOW, 100.0)

    assert session.starting_equity == 83_421.17
    assert session.allocations == {"A": 100.0, "C": 100.0}
    assert round(sum(session.allocations.values()), 2) == 200.0


def test_allocation_is_frozen_for_same_trading_day(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    first = store.create_or_load(100_000, NOW, 100.0)
    second = store.create_or_load(80_000, NOW, 100.0)

    assert second.session_id == first.session_id
    assert second.starting_equity == 100_000
    assert second.allocations == {"A": 100.0, "C": 100.0}


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

    migrated = store.replace_with_two_way(99_588.70, NOW, 100.0)

    assert migrated.session_id != legacy.session_id
    assert migrated.starting_equity == 99_588.70
    assert migrated.allocations == {"A": 100.0, "C": 100.0}
    assert migrated.state == "READY"
    assert round(sum(migrated.allocations.values()), 2) == 200.0
    assert (tmp_path / "session-2026-09-28.legacy.json").exists()


def legacy_engine(store):
    engine = object.__new__(TradingEngine)
    engine.session_store = store
    engine.session = store.load_today(NOW)
    engine.config = SimpleNamespace(model_allocation_dollars=100.0)
    engine.journal = SimpleNamespace(open_trades=lambda: [])
    engine.models = {
        name: SimpleNamespace(
            risk=SimpleNamespace(configure_session_capital=lambda capital: None),
            peak_equity=0.0,
            capital=100.0,
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
    assert engine.session.allocations == {"A": 100.0, "C": 100.0}


def test_foreign_shared_account_positions_do_not_block_fixed_allocation_migration(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    legacy = store.create_or_load(100_000, NOW)
    legacy.allocations = {"A": 50_000.0, "B": 50_000.0}
    store.save(legacy, NOW)
    engine = legacy_engine(store)

    foreign_position = SimpleNamespace(symbol="OTHER")
    foreign_order = SimpleNamespace(client_order_id="my-own-model-order")
    migrated = engine.upgrade_session_if_flat(
        account={"equity": 83_421.17},
        positions=[foreign_position],
        open_orders=[foreign_order],
        now=NOW,
    )

    assert migrated is True
    assert store.load_today(NOW).allocations == {"A": 100.0, "C": 100.0}


def test_model_ac_open_order_blocks_session_migration(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    legacy = store.create_or_load(100_000, NOW)
    legacy.allocations = {"A": 50_000.0, "B": 50_000.0}
    store.save(legacy, NOW)
    engine = legacy_engine(store)

    owned_order = SimpleNamespace(client_order_id="model-a-test-order")
    migrated = engine.upgrade_session_if_flat(
        account={"equity": 83_421.17},
        positions=[],
        open_orders=[owned_order],
        now=NOW,
    )

    assert migrated is False
