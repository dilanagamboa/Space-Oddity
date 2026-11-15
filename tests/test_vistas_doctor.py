"""Tests for services/vistas.py (read models) and services/doctor.py (actions)."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from core.protocolo import EVERY_DAY, weekdays_mask
from services import doctor, vistas
from services.database import sesion

PILL = {"name": "Ondansetrón", "dose": "4 mg", "scheduled_time": time(9, 0), "weekdays": EVERY_DAY,
        "start_date": date(2024, 5, 10), "end_date": None, "description": "Náuseas"}


@pytest.fixture
def db_path(mission_copy) -> Path:
    return mission_copy(16, 5)


def _doctor_id(db_path: Path) -> int:
    with sesion(db_path) as db:
        return db.usuarios.listar(role=1)[0]["id"]


def test_crew_table_and_search(db_path: Path) -> None:
    rows = vistas.crew(db_path=db_path)
    assert [r["name"] for r in rows] == ["Andrea", "Mateo", "Valeria", "Daniel"]
    assert rows[0]["last_name"] == "Solís" and rows[0]["sex"] == "F" and rows[0]["age"] > 30
    assert [r["name"] for r in vistas.crew("VARGAS", db_path=db_path)] == ["Daniel"]


def test_astronaut_detail_sections(db_path: Path) -> None:
    detail = vistas.astronaut_detail(1, db_path=db_path)
    assert detail["reports"][0]["mission_day"] == 16                  # Newest first
    assert len(detail["reports"]) == vistas.REPORT_DAYS_SHOWN
    assert detail["pills"][0]["name"] == "Vitamina D3" and len(detail["meals"]) == 4
    assert detail["live"]["mission_day"] == 16 and detail["cycle"]       # Female astronaut
    assert vistas.astronaut_detail(2, db_path=db_path)["cycle"] is None


def test_dashboard_and_log_of_user(db_path: Path) -> None:
    status = {r["name"]: r for r in vistas.crew_status(db_path=db_path)}
    assert status["Andrea"]["mission_day"] == 16 and status["Andrea"]["triage"] in {"green", "yellow", "red"}
    assert vistas.log_of_user(status["Andrea"]["id_user"], db_path=db_path) == 1
    assert vistas.log_of_user(_doctor_id(db_path), db_path=db_path) is None


def test_prescribe_update_and_retire_pill(db_path: Path) -> None:
    doc, user = _doctor_id(db_path), vistas.astronaut_detail(1, db_path=db_path)["id_user"]
    pill_id = doctor.save_item("pill", PILL, id_user=user, doctor_id=doc, db_path=db_path)
    assert vistas.item("pill", pill_id, db_path=db_path)["assigned_by"] == doc

    doctor.save_item("pill", {**PILL, "dose": "8 mg"}, id_user=user, doctor_id=doc, item_id=pill_id,
                     db_path=db_path)
    assert vistas.item("pill", pill_id, db_path=db_path)["dose"] == "8 mg"

    doctor.retire_item("pill", pill_id, doctor_id=doc, db_path=db_path)
    assert pill_id not in {p["id"] for p in vistas.astronaut_detail(1, db_path=db_path)["pills"]}
    with sesion(db_path) as db:
        actions = [a["action"] for a in db.auditoria.listar()]
    assert actions[-3:] == ["pill_assigned", "pill_updated", "pill_retired"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [({"name": " "}, "name"), ({"weekdays": 0}, "weekday"),
     ({"end_date": date(2024, 5, 1)}, "end date"), ({"scheduled_time": None}, "time")],
)
def test_invalid_prescriptions_are_rejected(db_path: Path, changes: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        doctor.save_item("pill", {**PILL, **changes}, id_user=1, doctor_id=1, db_path=db_path)


def test_plan_meal_and_notice_reach_the_astronaut(db_path: Path) -> None:
    meal = {"name": "Batido proteico", "meal_type": "snack", "target_calories_kcal": 250,
            "target_protein_g": 25, "scheduled_time": time(10, 30), "weekdays": weekdays_mask([0, 2, 4])}
    meal_id = doctor.save_item("meal", meal, id_user=4, doctor_id=_doctor_id(db_path), db_path=db_path)
    assert vistas.item("meal", meal_id, db_path=db_path)["weekdays"] == 0b10101

    user = vistas.astronaut_detail(1, db_path=db_path)["id_user"]
    notice = {"name": "Llamada con familia", "message": "Módulo Columbus", "scheduled_time": time(19, 0),
              "weekdays": EVERY_DAY}
    notice_id = doctor.save_item("notice", notice, id_user=user, doctor_id=_doctor_id(db_path),
                                 db_path=db_path)
    assert vistas.active_items("notice", user, db_path=db_path)[0]["assigned_by"] == _doctor_id(db_path)

    feed = vistas.my_notifications(1, db_path=db_path)
    (reminder,) = [r for r in feed["reminders"] if r["id_notice"] == notice_id]
    assert reminder["title"] == "Llamada con familia" and reminder["scheduled_at"].endswith("19:00:00")
    assert vistas.my_notifications(1, db_path=db_path)["reminders"] == feed["reminders"]   # Idempotent
