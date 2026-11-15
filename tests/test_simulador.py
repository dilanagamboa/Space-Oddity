"""Tests for services/simulador.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.enums import EscenarioSimulacion
from scripts.generar_datos import MissionConfig, generate_mission
from services.database import sesion
from services.eventos import EventBus
from services.reportes import process_mission_log
from services.simulador import (
    EFFECTS,
    SimulationError,
    active_simulation,
    available_scenarios,
    launch_simulation,
    revert_simulation,
)

DAYS = 24
EXPECTED = {
    "infection": "G1", "circadian_disruption": "F1", "atrophy": "C1", "solar_storm": "S4",
    "hypoglycemia": "S1", "cardiovascular": "S2", "anemia": "S3",
}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sim") / "mission.db"
    generate_mission(MissionConfig(days=DAYS, seed=9), path, sync_nasa=False)
    for log in (1, 2, 3, 4):
        process_mission_log(log, db_path=path, bus=EventBus())
    return path


@pytest.fixture
def db_path(template: Path, tmp_path: Path) -> Path:
    path = tmp_path / "copy.db"
    shutil.copy(template, path)
    return path


def _real_snapshot(path: Path, log: int) -> tuple:
    with sesion(path) as db:
        reports = [(r["mission_day"], r["readiness_score"]) for r in db.reportes.listar(id_mission_log=log)
                   if not r["is_simulated"]]
        hr = [r["heart_rate_bpm"] for r in db.cardiovascular.rango_dias(log, 18, 20, incluir_simulados=False)]
    return reports, hr


def test_every_scenario_has_an_effect() -> None:
    assert set(EFFECTS) == {s.value for s in EscenarioSimulacion}
    assert all(item["description"] for item in available_scenarios())


@pytest.mark.parametrize(("scenario", "notification"), sorted(EXPECTED.items()))
def test_scenario_raises_expected_alert(db_path: Path, scenario: str, notification: str) -> None:
    result = launch_simulation(2, scenario, 18, db_path=db_path, bus=EventBus())
    raised = {draft.notification_id for day in result.days for _, draft in day.alerts}
    assert notification in raised


def test_simulation_never_touches_real_data(db_path: Path) -> None:
    before = _real_snapshot(db_path, 2)
    result = launch_simulation(2, "infection", 18, db_path=db_path, bus=EventBus())
    assert _real_snapshot(db_path, 2) == before
    with sesion(db_path) as db:
        assert db.reportes.del_dia(2, 19, simulado=True)["triage_status"] == "red"
    revert_simulation(result.run_id, db_path=db_path)
    assert _real_snapshot(db_path, 2) == before


def test_revert_removes_everything_and_restores_mode(db_path: Path) -> None:
    with sesion(db_path) as db:
        mode_before = db.mission_logs.obtener(1)["active_monitoring_mode"]
    result = launch_simulation(1, "infection", DAYS - 2, db_path=db_path, bus=EventBus())
    assert active_simulation(1, db_path=db_path)["id"] == result.run_id

    deleted = revert_simulation(result.run_id, db_path=db_path)
    assert deleted["cardiovascular_autonomic"] == 3 * 96
    with sesion(db_path) as db:
        assert db.mission_logs.obtener(1)["active_monitoring_mode"] == mode_before
        assert db.simulaciones.obtener(result.run_id)["status"] == "reverted"
        assert not db.alertas.listar(id_mission_log=1, is_simulated=1)
        actions = [a["action"] for a in db.auditoria.listar()]
    assert actions == ["simulation_launched", "simulation_reverted"]
    assert active_simulation(1, db_path=db_path) is None


def test_simulated_alerts_are_published_as_simulated(db_path: Path) -> None:
    bus, events = EventBus(), []
    bus.subscribe(events.append)
    launch_simulation(3, "hypoglycemia", 18, db_path=db_path, bus=bus)
    assert events and all(e.is_simulated for e in events)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"id_mission_log": 99, "scenario": "infection", "start_day": 5}, "does not exist"),
        ({"id_mission_log": 1, "scenario": "infection", "start_day": DAYS}, "within"),
    ],
)
def test_invalid_requests(db_path: Path, kwargs: dict, message: str) -> None:
    with pytest.raises(SimulationError, match=message):
        launch_simulation(**kwargs, db_path=db_path, bus=EventBus())


def test_unknown_scenario_is_rejected(db_path: Path) -> None:
    with pytest.raises(ValueError):
        launch_simulation(1, "zombies", 5, db_path=db_path, bus=EventBus())


def test_only_one_active_simulation_per_astronaut(db_path: Path) -> None:
    launch_simulation(1, "solar_storm", 10, db_path=db_path, bus=EventBus())
    with pytest.raises(SimulationError, match="already"):
        launch_simulation(1, "hypoglycemia", 12, db_path=db_path, bus=EventBus())
