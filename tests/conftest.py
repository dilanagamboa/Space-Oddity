"""Fixtures compartidas: cada test recibe una base SQLite en memoria, nueva y vacía."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy.engine import Engine

from core.enums import Rol, Sexo
from services.database import Repositorios, crear_engine, crear_tablas, sesion


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = crear_engine("sqlite://")
    crear_tablas(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Repositorios]:
    """Repositorios dentro de una transacción que se confirma al final del test."""
    with sesion(engine) as repos:
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
