"""Tests for scripts/generar_datos.py (small missions, no network)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.ecuaciones import compute_car, compute_delta_mass_pct
from scripts.crear_db import crear_base_datos
from scripts.generar_datos import CREW, MissionConfig, generate_mission
from services.database import sesion

SHORT = MissionConfig(days=14, seed=1)


@pytest.fixture
def mission_db(tmp_path: Path) -> Path:
    path = tmp_path / "mission.db"
    generate_mission(SHORT, path, sync_nasa=False)
    return path


def _sleep_hours(db, id_log: int, day: int) -> float:
    rows = db.cardiovascular.rango_dias(id_log, day, day)
    return sum(1 for r in rows if r["sleep_stage"] > 0) / 4


def test_counts_follow_sampling_plan(mission_db: Path) -> None:
    with sesion(mission_db) as db:
        assert len(db.usuarios.listar()) == len(CREW) + 2          # + 1 doctor + admin
        assert len(db.cardiovascular.listar(id_mission_log=1)) == 14 * 96
        assert len(db.radiacion.listar(id_mission_log=1)) == 14
        assert {r["mission_day"] for r in db.saliva.listar(id_mission_log=1)} == {3, 10}
        assert len(db.composicion.listar(id_mission_log=1)) == 2   # days 1 and 8
        assert len(db.referencias.listar()) >= 10
        # The single flight surgeon supervises the whole crew
        (doctor,) = db.usuarios.listar(role=1)
        assert len(db.asignaciones.pacientes_de(doctor["id"])) == len(CREW)


def test_values_stay_in_physiological_ranges(mission_db: Path) -> None:
    with sesion(mission_db) as db:
        rows = db.cardiovascular.listar(id_mission_log=2)
        assert all(85 <= r["spo2_pct"] <= 100 for r in rows)
        assert all(40 <= r["heart_rate_bpm"] <= 190 for r in rows)
        assert 4.5 <= _sleep_hours(db, 2, 5) <= 7.5


def test_same_seed_same_data(tmp_path: Path) -> None:
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    generate_mission(SHORT, a, sync_nasa=False)
    generate_mission(SHORT, b, sync_nasa=False)
    with sesion(a) as da, sesion(b) as dbb:
        assert da.cardiovascular.listar(id_mission_log=3, limite=50) == \
            dbb.cardiovascular.listar(id_mission_log=3, limite=50)


def test_demo_scenarios(tmp_path: Path) -> None:
    path = tmp_path / "full.db"
    generate_mission(MissionConfig(days=50, seed=3), path, sync_nasa=False)
    with sesion(path) as db:
        # Infection (astronaut 2, days 30-33): warmer and faster heart at night
        def night_avg(day: int, column: str) -> float:
            rows = [r for r in db.cardiovascular.rango_dias(2, day, day) if r["sleep_stage"] > 0]
            return sum(r[column] for r in rows) / len(rows)
        assert night_avg(31, "skin_temperature_c") - night_avg(10, "skin_temperature_c") >= 0.5
        assert night_avg(31, "heart_rate_bpm") - night_avg(10, "heart_rate_bpm") >= 10

        # Circadian (astronaut 3, days 20-27): short sleep and blunted CAR
        assert _sleep_hours(db, 3, 22) < 5
        saliva = {r["collection_slot"]: r["salivary_cortisol_nmol_l"]
                  for r in db.saliva.listar(id_mission_log=3, mission_day=24)}
        assert compute_car(saliva["wake_0m"], saliva["wake_30m"])[0] < 2.5
        assert saliva["bedtime"] > 4.0

        # Atrophy (astronaut 4): 5-10 % mass loss -> GLIM stage 1
        weights = [r["weight_kg"] for r in db.composicion.listar(id_mission_log=4, orden="mission_day")]
        assert 5.0 <= compute_delta_mass_pct(82.0, weights[-1]) <= 10.0

        # Glycemic (astronaut 1, day 15)
        assert max(r["blood_glucose_mg_dl"] or 0 for r in db.sangre.listar(id_mission_log=1)) > 180

        # Without cached events, one synthetic SPE on day 35
        flagged = [r for r in db.radiacion.listar(id_mission_log=1) if r["solar_event_flag"]]
        assert [(r["mission_day"], r["data_source"]) for r in flagged] == [(35, "simulated")]


def test_uses_real_events_from_cache(tmp_path: Path) -> None:
    path = tmp_path / "cached.db"
    crear_base_datos(path)
    with sesion(path) as db:
        db.clima_espacial.insertar(
            external_id="2024-05-11T02:15:00-SEP-001", source="nasa_donki", event_type="SEP",
            start_time=datetime(2024, 5, 11, 2, 15),
        )
    generate_mission(MissionConfig(start_date=date(2024, 5, 1), days=14), path,
                     reset=False, sync_nasa=False)
    with sesion(path) as db:
        flagged = [r for r in db.radiacion.listar(id_mission_log=1) if r["solar_event_flag"]]
        assert [(r["mission_day"], r["data_source"]) for r in flagged] == [(11, "nasa_donki")]


def test_rejects_unknown_environment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="environment"):
        generate_mission(MissionConfig(environment="mars"), tmp_path / "x.db", sync_nasa=False)
