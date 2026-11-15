"""Tests for services/recordatorios.py."""

from __future__ import annotations

from datetime import UTC, datetime
import shutil
from pathlib import Path

import pytest

from scripts.generar_datos import MissionConfig, generate_mission
from services.database import sesion
from services.recordatorios import (
    STRATEGIES,
    complete_reminder,
    generate_daily_reminders,
    overdue_pill_reminders,
)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generates a 7-day mission once for all reminder tests."""
    path = tmp_path_factory.mktemp("rem") / "mission.db"
    generate_mission(MissionConfig(days=7, seed=3), path, sync_nasa=False)
    return path


@pytest.fixture
def db_path(template: Path, tmp_path: Path) -> Path:
    """Private copy of the template database per test."""
    path = tmp_path / "copy.db"
    shutil.copy(template, path)
    return path


def test_registry_excludes_task() -> None:
    """Verifies Strategy + Registry keys and ensures 'task' is never registered."""
    assert set(STRATEGIES) == {"pill", "meal", "glucose", "saliva", "bia_chair"}


def test_idempotency_and_null_id_task(db_path: Path) -> None:
    """Running `generate_daily_reminders` twice does not duplicate rows; `id_task` is NULL."""
    first = generate_daily_reminders(1, 1, db_path=db_path)
    assert first and generate_daily_reminders(1, 1, db_path=db_path) == []
    assert all(r["id_task"] is None and r["reminder_type"] != "task" for r in first)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # Day 2 (non-sampling day in standard mode): only 1 pill + 4 meals
        ("standard", {"pill": 1, "meal": 4, "glucose": 0, "saliva": 0, "bia_chair": 0}),
        # Infection mode: 6 glucose + 3 saliva
        ("alert_infection", {"pill": 1, "meal": 4, "glucose": 6, "saliva": 3, "bia_chair": 0}),
        # Atrophy mode: 2 glucose + daily BIA chair
        ("alert_atrophy", {"pill": 1, "meal": 4, "glucose": 2, "saliva": 0, "bia_chair": 1}),
        # Circadian mode: 2 glucose + 4 saliva (CAR + diurnal curve)
        ("alert_circadian", {"pill": 1, "meal": 4, "glucose": 2, "saliva": 4, "bia_chair": 0}),
    ],
)
def test_frequencies_by_monitoring_mode(db_path: Path, mode: str, expected: dict[str, int]) -> None:
    """Checks that `active_monitoring_mode` dynamically adjusts daily sampling reminders."""
    with sesion(db_path) as db:
        db.mission_logs.actualizar(1, active_monitoring_mode=mode)
    rows = generate_daily_reminders(1, 2, db_path=db_path)
    assert {k: sum(1 for r in rows if r["reminder_type"] == k) for k in expected} == expected


def test_overdue_pill_and_completion(db_path: Path) -> None:
    """Pill at 08:00 is not overdue at 08:30, overdue at 09:05 (>=60m), and cleared when completed."""
    generate_daily_reminders(1, 1, db_path=db_path)
    assert not overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 8, 30, tzinfo=UTC))

    (overdue,) = overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 9, 5, tzinfo=UTC))
    updated = complete_reminder(overdue["id"], db_path=db_path, now=datetime(2024, 5, 1, 9, 6, tzinfo=UTC))
    assert updated["status"] == "completed" and updated["completed_at"] is not None
    assert not overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 9, 30, tzinfo=UTC))
