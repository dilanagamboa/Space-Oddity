"""Tests for services/recordatorios.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.enums import TipoRecordatorio
from core.protocolo import mission_date, weekdays_mask
from services.database import sesion
from services.recordatorios import (
    STRATEGIES,
    complete_reminder,
    generate_daily_reminders,
    overdue_pill_reminders,
)


@pytest.fixture
def db_path(mission_copy) -> Path:
    """7-day mission, not processed (reminders only need the plan)."""
    return mission_copy(7, 3, processed=False)


def test_registry_excludes_task() -> None:
    """Verifies that all valid strategies are registered and 'task' is excluded."""
    assert set(STRATEGIES) == set(TipoRecordatorio) - {TipoRecordatorio.TAREA}


def test_idempotency_and_null_id_task(db_path: Path) -> None:
    """Checks that generating twice on the same day creates 0 duplicates and keeps id_task NULL."""
    first = generate_daily_reminders(1, 1, db_path=db_path)
    # Second call on the same day must return an empty list (idempotent)
    assert first and generate_daily_reminders(1, 1, db_path=db_path) == []
    # Every reminder must have id_task = None and never be of type 'task'
    assert all(r["id_task"] is None and r["reminder_type"] != "task" for r in first)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # Day 2 (non-sampling day in standard mode): only 1 pill and 4 meals
        ("standard", (1, 4, 0, 0, 0)),
        # Infection mode: 6 daily glucose + 3 daily saliva
        ("alert_infection", (1, 4, 6, 3, 0)),
        # Atrophy mode: 2 daily glucose + 1 daily fasting BIA chair
        ("alert_atrophy", (1, 4, 2, 0, 1)),
        # Circadian mode: 2 daily glucose + 4 daily saliva (CAR + diurnal curve)
        ("alert_circadian", (1, 4, 2, 4, 0)),
    ],
)
def test_frequencies_by_monitoring_mode(db_path: Path, mode: str, expected: tuple[int, ...]) -> None:
    """Verifies that changing `active_monitoring_mode` adapts the sampling schedule."""
    with sesion(db_path) as db:
        db.mission_logs.actualizar(1, active_monitoring_mode=mode)
    rows = generate_daily_reminders(1, 2, db_path=db_path)
    types = (TipoRecordatorio.PASTILLA, TipoRecordatorio.COMIDA, TipoRecordatorio.MUESTRA_GLUCOSA,
             TipoRecordatorio.MUESTRA_SALIVA, TipoRecordatorio.PESAJE_BIA)
    assert tuple(sum(1 for r in rows if r["reminder_type"] == t) for t in types) == expected


def test_overdue_pill_and_completion(db_path: Path) -> None:
    """Verifies 60-minute overdue detection for pills and status transition to completed."""
    generate_daily_reminders(1, 1, db_path=db_path)
    # Pill is scheduled at 08:00 UTC -> at 08:30 (30 min elapsed) it is not overdue yet
    assert not overdue_pill_reminders(1, db_path=db_path, now=datetime(2024, 5, 1, 8, 30, tzinfo=UTC))

    # At 09:05 UTC (65 min elapsed >= 60 min) it must be flagged as overdue
    (overdue,) = overdue_pill_reminders(1, db_path=db_path, now=datetime(2024, 5, 1, 9, 5, tzinfo=UTC))
    # Marking it completed stamps completed_at and clears it from the overdue list
    updated = complete_reminder(overdue["id"], db_path=db_path, now=datetime(2024, 5, 1, 9, 6, tzinfo=UTC))
    assert updated["status"] == "completed" and updated["completed_at"] is not None
    assert not overdue_pill_reminders(1, db_path=db_path, now=datetime(2024, 5, 1, 9, 30, tzinfo=UTC))


def test_items_follow_their_weekdays(db_path: Path) -> None:
    """A pill set to L and M only (Monday, Wednesday) is reminded only on those calendar days."""
    with sesion(db_path) as db:
        log = db.mission_logs.obtener(1)
        id_pill = db.pastillas.activas_de(log["id_user"])[0]["id"]
        db.pastillas.actualizar(id_pill, weekdays=weekdays_mask([0, 2]))
    reminded = {mission_date(log["start_date"], day).weekday()
                for day in range(1, 8)
                for r in generate_daily_reminders(1, day, db_path=db_path) if r["id_pill"] == id_pill}
    assert reminded == {0, 2}
