"""Fixtures compartidas: cada test recibe una base SQLite nueva en una carpeta temporal."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from core.enums import Rol, Sexo
from scripts.crear_db import crear_base_datos
from services.database import Repositorios, sesion


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
