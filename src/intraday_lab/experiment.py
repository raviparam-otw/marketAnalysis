from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")


@dataclass
class ExperimentSession:
    session_id: str
    trading_date: str
    started_at: str
    starting_equity: float
    allocations: dict[str, float]
    state: str = "READY"
    updated_at: str | None = None
    ended_at: str | None = None

    @property
    def allocation_per_model(self) -> float:
        return float(self.allocations.get("A", 0.0))


class ExperimentSessionStore:
    """Crash-safe session snapshot. One A/C allocation is frozen per trading day."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _path(self, now: datetime | None = None) -> Path:
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        return self.directory / f"session-{current.date().isoformat()}.json"

    def load_today(self, now: datetime | None = None) -> ExperimentSession | None:
        path = self._path(now)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return ExperimentSession(**json.load(handle))

    @staticmethod
    def two_way_allocations(starting_equity: float) -> tuple[float, dict[str, float]]:
        if starting_equity <= 0:
            raise ValueError("Cannot start an experiment with non-positive account equity.")
        total = Decimal(str(starting_equity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        allocation_a = (total / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        allocation_c = total - allocation_a
        return float(total), {
            "A": float(allocation_a),
            "C": float(allocation_c),
        }

    def create_or_load(self, starting_equity: float, now: datetime | None = None) -> ExperimentSession:
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        existing = self.load_today(current)
        if existing:
            return existing
        total, allocations = self.two_way_allocations(starting_equity)
        session = ExperimentSession(
            session_id=f"{current.strftime('%Y%m%d')}-{uuid4().hex[:10]}",
            trading_date=current.date().isoformat(),
            started_at=current.isoformat(timespec="seconds"),
            starting_equity=total,
            allocations=allocations,
            state="READY",
            updated_at=current.isoformat(timespec="seconds"),
        )
        self.save(session, current)
        return session

    def replace_with_two_way(
        self,
        starting_equity: float,
        now: datetime | None = None,
    ) -> ExperimentSession:
        """Replace a flat legacy session with a fresh A/C split."""
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        path = self._path(current)
        if path.exists():
            backup = path.with_name(path.stem + ".legacy.json")
            if not backup.exists():
                self.directory.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)

        total, allocations = self.two_way_allocations(starting_equity)
        session = ExperimentSession(
            session_id=f"{current.strftime('%Y%m%d')}-{uuid4().hex[:10]}",
            trading_date=current.date().isoformat(),
            started_at=current.isoformat(timespec="seconds"),
            starting_equity=total,
            allocations=allocations,
            state="READY",
            updated_at=current.isoformat(timespec="seconds"),
        )
        self.save(session, current)
        return session

    def save(self, session: ExperimentSession, now: datetime | None = None) -> None:
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        self.directory.mkdir(parents=True, exist_ok=True)
        session.updated_at = current.isoformat(timespec="seconds")
        path = self._path(current)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(asdict(session), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def set_state(self, session: ExperimentSession, state: str, now: datetime | None = None) -> None:
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        session.state = state
        if state in {"STOPPED", "FLATTENED"}:
            session.ended_at = current.isoformat(timespec="seconds")
        self.save(session, current)
