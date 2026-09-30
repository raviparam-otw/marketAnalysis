from datetime import datetime
from zoneinfo import ZoneInfo

from intraday_lab.experiment import ExperimentSessionStore


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 9, 20, tzinfo=EASTERN)


def test_session_splits_actual_equity_equally(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    session = store.create_or_load(83_421.17, NOW)

    assert session.starting_equity == 83_421.17
    assert session.allocations["A"] == 27_807.06
    assert session.allocations["B"] == 27_807.06
    assert session.allocations["C"] == 27_807.05
    assert round(sum(session.allocations.values()), 2) == 83_421.17


def test_allocation_is_frozen_for_same_trading_day(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    first = store.create_or_load(100_000, NOW)
    second = store.create_or_load(80_000, NOW)

    assert second.session_id == first.session_id
    assert second.starting_equity == 100_000
    assert second.allocations == {"A": 33_333.33, "B": 33_333.33, "C": 33_333.34}


def test_session_state_is_persisted(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    session = store.create_or_load(90_000, NOW)
    store.set_state(session, "PAUSED", NOW)

    loaded = store.load_today(NOW)
    assert loaded is not None
    assert loaded.state == "PAUSED"


def test_legacy_ab_session_can_be_replaced_with_three_way_split(tmp_path):
    store = ExperimentSessionStore(tmp_path)
    legacy = store.create_or_load(99_733.74, NOW)
    legacy.allocations = {"A": 49_866.87, "B": 49_866.87}
    legacy.state = "STOPPED"
    store.save(legacy, NOW)

    migrated = store.replace_legacy_with_three_way(99_588.70, NOW)

    assert migrated.session_id != legacy.session_id
    assert migrated.starting_equity == 99_588.70
    assert migrated.allocations == {
        "A": 33_196.23,
        "B": 33_196.23,
        "C": 33_196.24,
    }
    assert migrated.state == "READY"
    assert round(sum(migrated.allocations.values()), 2) == 99_588.70
    assert (tmp_path / "session-2026-09-28.legacy-ab.json").exists()
