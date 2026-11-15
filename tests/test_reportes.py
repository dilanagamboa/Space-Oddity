"""Tests for core/resumen_diario.py and services/reportes.py."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.resumen_diario import Baseline, SleepWindow, summarize_glucose, summarize_saliva
from scripts.generar_datos import MissionConfig, generate_mission
from services.database import sesion
from services.eventos import AlertEvent, EventBus
from services.reportes import process_days, process_mission_log

DAYS = 36   # Covers the infection (30-33) and the synthetic solar event (35)


@pytest.fixture(scope="module")
def processed_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Mission generated and processed once for the whole module."""
    path = tmp_path_factory.mktemp("reportes") / "mission.db"
    generate_mission(MissionConfig(days=DAYS, seed=5), path, sync_nasa=False)
    for log in (1, 2, 3, 4):
        process_mission_log(log, db_path=path, bus=EventBus())
    return path


@pytest.fixture
def mission_db(processed_template: Path, tmp_path: Path) -> Iterator[Path]:
    """Private copy per test, so tests never affect each other."""
    path = tmp_path / "copy.db"
    shutil.copy(processed_template, path)
    yield path


def _alerts(path: Path, log: int) -> set[tuple[int, str, str]]:
    with sesion(path) as db:
        rows = db.alertas.listar(id_mission_log=log)
    return {(a["mission_day"], a["triggered_rules_json"]["notification_id"], a["severity"]) for a in rows}


# --- Pure summaries --------------------------------------------------------------
def test_baseline_needs_minimum_days() -> None:
    assert Baseline.from_values([1.0, 2.0], min_n=3) is None
    base = Baseline.from_values([10.0, 12.0, None, 14.0], min_n=3)
    assert base.mean == 12.0 and base.z(16.0) == pytest.approx(2.0)


def test_sleep_window_crosses_midnight() -> None:
    window = SleepWindow()
    assert window.contains(23) and window.contains(3) and not window.contains(12)


def test_glucose_extreme_prefers_hypoglycemia() -> None:
    rows = [{"blood_glucose_mg_dl": v} for v in (65.0, 120.0, 200.0)]
    assert summarize_glucose(rows)["glucose_extreme_mg_dl"] == 65.0
    assert summarize_glucose([{"blood_glucose_mg_dl": None, "hemoglobin_g_dl": 14}]) == {}


def test_saliva_needs_the_four_samples() -> None:
    assert summarize_saliva([{"collection_slot": "wake_0m"}]) == {}


# --- Pipeline over a generated mission -------------------------------------------
def test_one_report_per_day_with_valid_triage(mission_db: Path) -> None:
    with sesion(mission_db) as db:
        reports = db.reportes.listar(id_mission_log=1, orden="mission_day")
    assert [r["mission_day"] for r in reports] == list(range(1, DAYS + 1))
    assert all(r["triage_status"] in {"green", "yellow", "red"} for r in reports)
    assert all(0 <= r["readiness_score"] <= 100 for r in reports)


def test_demo_cases_raise_their_alerts(mission_db: Path) -> None:
    infection = {(d, n) for d, n, _ in _alerts(mission_db, 2) if n == "G1"}
    assert infection and all(30 <= d <= 33 for d, _ in infection)
    assert (15, "S1", "yellow") in _alerts(mission_db, 1)
    assert {d for d, n, s in _alerts(mission_db, 3) if n == "F1" and s == "red"} >= {21, 22, 23}
    assert any(n == "C1" for _, n, _ in _alerts(mission_db, 4))
    assert all((35, "S4", "red") in _alerts(mission_db, log) for log in (1, 2, 3, 4))


def test_no_z_score_alerts_during_calibration(mission_db: Path) -> None:
    """Days 1-14 are the personal baseline: only absolute thresholds apply there."""
    with sesion(mission_db) as db:
        early = [r for r in db.reportes.listar() if r["mission_day"] <= 14]
    assert all(r["hrv_z_score"] is None for r in early)


def test_body_alerts_only_on_chair_days(mission_db: Path) -> None:
    chair_days = {1, 8, 15, 22, 29, 36}
    assert all(d in chair_days for d, n, _ in _alerts(mission_db, 4) if n.startswith("C"))


def test_pipeline_is_idempotent(mission_db: Path) -> None:
    def snapshot() -> tuple[int, int, list[float]]:
        with sesion(mission_db) as db:
            reports = db.reportes.listar(id_mission_log=2, orden="mission_day")
            scores = [r["readiness_score"] for r in reports]
            return len(reports), len(db.alertas.listar(id_mission_log=2)), scores

    before = snapshot()
    process_mission_log(2, db_path=mission_db, bus=EventBus())
    assert snapshot() == before


def test_alerts_are_published_after_commit(mission_db: Path) -> None:
    bus, received = EventBus(), []

    def handler(event: AlertEvent) -> None:
        with sesion(mission_db) as db:                     # Already visible in the database
            assert db.alertas.obtener(event.alert_id) is not None
        received.append(event)

    bus.subscribe(handler)
    process_days(2, range(30, 34), db_path=mission_db, bus=bus)
    assert any(e.alert.notification_id == "G1" for e in received)
    assert all(e.astronaut["email"] == "mateo.rojas@spaceoddity.dev" for e in received)


def test_monitoring_mode_follows_latest_day(mission_db: Path) -> None:
    with sesion(mission_db) as db:
        latest = db.reportes.del_dia(2, DAYS)
        assert db.mission_logs.obtener(2)["active_monitoring_mode"] == latest["recommended_sampling_mode"]
