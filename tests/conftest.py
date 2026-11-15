"""Fixtures compartidas: bases SQLite temporales, vacías o con una misión generada."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from core.enums import Rol, Sexo
from scripts.crear_db import crear_base_datos
from scripts.generar_datos import CREW, MissionConfig, generate_mission
from services.database import Repositorios, sesion
from services.eventos import EventBus
from services.reportes import process_mission_log

MissionFactory = Callable[..., Path]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Crea una base con el esquema real (scripts/schema.sql) y devuelve su ruta."""
    ruta = tmp_path / "test.db"
    crear_base_datos(ruta)
    return ruta


@pytest.fixture
def db(db_path: Path) -> Iterator[Repositorios]:
    """Repositorios dentro de una transacción que se confirma al final del test."""
    with sesion(db_path) as repos:
        yield repos


@pytest.fixture
def escenario(db: Repositorios) -> dict[str, int]:
    """Datos mínimos: un doctor, un astronauta, una misión y su mission log."""
    id_doctor = db.usuarios.insertar(
        full_name="Dra. Vega", email="vega@spaceoddity.dev", role=Rol.DOCTOR,
        birth_date=date(1985, 3, 2), sex=Sexo.FEMENINO,
    )
    id_astro = db.usuarios.insertar(
        full_name="Leo Mora", email="leo@spaceoddity.dev", role=Rol.ASTRONAUTA,
        birth_date=date(1990, 7, 15), sex=Sexo.MASCULINO,
    )
    id_mision = db.misiones.insertar(name="LEO-1", start_date=date(2026, 1, 1))
    id_log = db.mission_logs.insertar(
        id_user=id_astro, id_mission=id_mision, start_date=date(2026, 1, 1),
        start_weight_kg=75.0, start_height_cm=178.0, start_muscle_pct=42.0, start_fat_pct=18.0,
    )
    return {"doctor": id_doctor, "astro": id_astro, "mision": id_mision, "log": id_log}


@pytest.fixture(scope="session")
def _mission_templates() -> dict[tuple[int, int, bool], Path]:
    return {}


@pytest.fixture
def mission_copy(
    _mission_templates: dict[tuple[int, int, bool], Path],
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
) -> MissionFactory:
    """Copia privada de una misión generada (y procesada, si se pide).

    Cada plantilla ``(days, seed, processed)`` se genera una sola vez por sesión de
    tests, y cada test recibe su propia copia, así los tests no se afectan entre sí.
    """

    def make(days: int, seed: int, *, processed: bool = True) -> Path:
        key = (days, seed, processed)
        if key not in _mission_templates:
            template = tmp_path_factory.mktemp("mission") / "template.db"
            generate_mission(MissionConfig(days=days, seed=seed), template, sync_nasa=False)
            for id_log in range(1, len(CREW) + 1) if processed else ():
                process_mission_log(id_log, db_path=template, bus=EventBus())
            _mission_templates[key] = template
        copy = tmp_path / f"mission_{days}_{seed}.db"
        shutil.copy(_mission_templates[key], copy)
        return copy

    return make
