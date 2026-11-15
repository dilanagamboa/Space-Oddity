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
    path = tmp_path_factory.mktemp("rem") / "mission.db"
    generate_mission(MissionConfig(days=7, seed=3), path, sync_nasa=False)
    return path


@pytest.fixture
def db_path(template: Path, tmp_path: Path) -> Path:
    path = tmp_path / "copy.db"
    shutil.copy(template, path)
    return path


def test_registry_has_expected_strategies_and_no_tasks() -> None:
    assert set(STRATEGIES) == {"pill", "meal", "glucose", "saliva", "bia_chair"}
    assert "task" not in STRATEGIES


def test_generate_reminders_is_idempotent_and_keeps_id_task_null(db_path: Path) -> None:
    first = generate_daily_reminders(1, 1, db_path=db_path)
    second = generate_daily_reminders(1, 1, db_path=db_path)
    assert first and second == []
    assert all(r["id_task"] is None and r["reminder_type"] != "task" for r in first)


@pytest.mark.parametrize(
    ("mode", "day", "expected"),
    [
        ("standard", 2, {"pill": 1, "meal": 4, "glucose": 0, "saliva": 0, "bia_chair": 0}),
        ("alert_infection", 2, {"pill": 1, "meal": 4, "glucose": 6, "saliva": 3, "bia_chair": 0}),
        ("alert_atrophy", 2, {"pill": 1, "meal": 4, "glucose": 2, "saliva": 0, "bia_chair": 1}),
        ("alert_circadian", 2, {"pill": 1, "meal": 4, "glucose": 2, "saliva": 4, "bia_chair": 0}),
    ],
)
def test_sampling_frequencies_follow_active_monitoring_mode(
    db_path: Path, mode: str, day: int, expected: dict[str, int]
) -> None:
    with sesion(db_path) as db:
        db.mission_logs.actualizar(1, active_monitoring_mode=mode)
    rows = generate_daily_reminders(1, day, db_path=db_path)
    counts = {k: sum(1 for r in rows if r["reminder_type"] == k) for k in expected}
    assert counts == expected


def test_overdue_pill_detection_and_completion(db_path: Path) -> None:
    generate_daily_reminders(1, 1, db_path=db_path)
    assert not overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 8, 30, tzinfo=UTC))

    overdue = overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 9, 5, tzinfo=UTC))
    assert len(overdue) == 1

    updated = complete_reminder(overdue[0]["id"], db_path=db_path, now=datetime(2024, 5, 1, 9, 6, tzinfo=UTC))
    assert updated["status"] == "completed" and updated["completed_at"] is not None
    assert not overdue_pill_reminders(1, 1, db_path=db_path, now=datetime(2024, 5, 1, 9, 30, tzinfo=UTC))
