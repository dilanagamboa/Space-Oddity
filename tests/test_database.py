"""Tests de la capa de datos (esquema, restricciones y repositorios)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from config import normalizar_url_db
from core.enums import EstadoAlerta, Rol, Sexo
from services import schema as s
from services.database import Repositorios, sesion


def _t(dia: int, hora: int = 8) -> datetime:
    return datetime(2026, 1, dia, hora, tzinfo=UTC)


# --- Esquema -----------------------------------------------------------------
def test_crea_todas_las_tablas(engine: Engine) -> None:
    assert set(sa.inspect(engine).get_table_names()) == set(s.metadata.tables)


def test_normaliza_url_supabase() -> None:
    assert normalizar_url_db("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalizar_url_db("sqlite:///x.db") == "sqlite:///x.db"


# --- Restricciones -------------------------------------------------------------
def test_email_unico_e_insensible_a_mayusculas(db: Repositorios, escenario: dict) -> None:
    assert db.usuarios.por_email("  LEO@SpaceOddity.dev ")["id"] == escenario["astro"]
    with pytest.raises(IntegrityError):
        db.usuarios.insertar(
            full_name="Copia", email="leo@spaceoddity.dev", role=Rol.ASTRONAUTA,
            birth_date=date(1990, 1, 1), sex=Sexo.MASCULINO,
        )


def test_rechaza_rol_invalido(db: Repositorios) -> None:
    with pytest.raises(IntegrityError):
        db.usuarios.insertar(
            full_name="X", email="x@x.com", role=7, birth_date=date(2000, 1, 1), sex=0
        )


def test_llaves_foraneas_activas(db: Repositorios) -> None:
    with pytest.raises(IntegrityError):
        db.mission_logs.insertar(
            id_user=999, id_mission=999, start_date=date(2026, 1, 1),
            start_weight_kg=70, start_height_cm=170, start_muscle_pct=40, start_fat_pct=20,
        )


def test_borrado_en_cascada(db: Repositorios, escenario: dict) -> None:
    db.sangre.insertar(
        id_mission_log=escenario["log"], mission_day=1, recorded_at=_t(1), blood_glucose_mg_dl=92
    )
    db.usuarios.eliminar(escenario["astro"])
    assert db.mission_logs.obtener(escenario["log"]) is None
    assert db.sangre.listar() == []


def test_sesion_revierte_si_hay_error(engine: Engine) -> None:
    with pytest.raises(RuntimeError), sesion(engine) as db:
        db.misiones.insertar(name="Revertida", start_date=date(2026, 1, 1))
        raise RuntimeError("falla a mitad de la transacción")
    with sesion(engine) as db:
        assert db.misiones.listar() == []


# --- Repositorios ----------------------------------------------------------------
def test_doctor_ve_solo_sus_pacientes(db: Repositorios, escenario: dict) -> None:
    db.asignaciones.insertar(id_doctor=escenario["doctor"], id_mission_log=escenario["log"])
    pacientes = db.asignaciones.pacientes_de(escenario["doctor"])
    assert [p["full_name"] for p in pacientes] == ["Leo Mora"]
    assert db.asignaciones.supervisa(escenario["doctor"], escenario["log"])
    assert not db.asignaciones.supervisa(escenario["astro"], escenario["log"])


def test_pastilla_asignada_por_doctor(db: Repositorios, escenario: dict) -> None:
    db.pastillas.insertar(
        id_user=escenario["astro"], assigned_by=escenario["doctor"], name="Vitamina D",
        dose="1000 UI", scheduled_time=time(8, 0), start_date=date(2026, 1, 1),
    )
    (pastilla,) = db.pastillas.activas_de(escenario["astro"])
    assert pastilla["assigned_by"] == escenario["doctor"]
    assert pastilla["scheduled_time"] == time(8, 0)


def test_rango_de_dias_y_simulados(db: Repositorios, escenario: dict) -> None:
    log = escenario["log"]
    for dia, simulado in [(1, False), (2, False), (3, True), (9, False)]:
        db.sangre.insertar(
            id_mission_log=log, mission_day=dia, recorded_at=_t(dia),
            blood_glucose_mg_dl=90 + dia, is_simulated=simulado,
        )
    assert [f["mission_day"] for f in db.sangre.rango_dias(log, 1, 3)] == [1, 2, 3]
    reales = db.sangre.rango_dias(log, 1, 3, incluir_simulados=False)
    assert [f["mission_day"] for f in reales] == [1, 2]
    assert db.sangre.ultimo_dia(log) == 9


def test_limpiar_simulados_no_toca_datos_reales(db: Repositorios, escenario: dict) -> None:
    log = escenario["log"]
    db.sangre.insertar(id_mission_log=log, mission_day=1, recorded_at=_t(1), blood_glucose_mg_dl=90)
    db.sangre.insertar(
        id_mission_log=log, mission_day=1, recorded_at=_t(1, 9),
        blood_glucose_mg_dl=55, is_simulated=True,
    )
    db.alertas.insertar(
        id_mission_log=log, mission_day=1, alert_category="glycemic", severity="red",
        title="Hipoglucemia", triggered_rules_json={"glucose_lt": 70},
        action_plan_text="Ingerir 15 g de carbohidratos", is_simulated=True,
    )
    borradas = db.limpiar_simulados(log)
    assert borradas["blood_measures"] == 1 and borradas["alerts"] == 1
    assert len(db.sangre.listar()) == 1


def test_alertas_activas_y_json(db: Repositorios, escenario: dict) -> None:
    id_alerta = db.alertas.insertar(
        id_mission_log=escenario["log"], mission_day=2, alert_category="radiation",
        severity="orange", title="Evento SEP", triggered_rules_json={"pfu": 15, "nivel": "S1"},
        action_plan_text="Ir al refugio",
    )
    (alerta,) = db.alertas.activas(escenario["log"])
    assert alerta["triggered_rules_json"] == {"pfu": 15, "nivel": "S1"}
    db.alertas.cambiar_estado(id_alerta, EstadoAlerta.RECONOCIDA)
    assert db.alertas.activas() == []


def test_cache_clima_espacial_es_idempotente(db: Repositorios) -> None:
    evento = {
        "external_id": "2026-01-02T10:00:00-SEP-001", "source": "nasa_donki",
        "event_type": "SEP", "start_time": _t(2, 10),
    }
    assert db.clima_espacial.insertar_nuevos([evento]) == 1
    assert db.clima_espacial.insertar_nuevos([evento]) == 0
    assert len(db.clima_espacial.entre(_t(1), _t(3), tipo="SEP")) == 1


def test_recordatorio_de_tarea(db: Repositorios, escenario: dict) -> None:
    id_tarea = db.tareas.insertar(
        id_mission_log=escenario["log"], assigned_by=escenario["doctor"],
        title="30 min de cicloergómetro", category="exercise", due_at=_t(3, 18),
    )
    db.recordatorios.insertar(
        id_mission_log=escenario["log"], reminder_type="task", id_task=id_tarea,
        title="Ejercicio", mission_day=3, scheduled_at=_t(3, 17),
    )
    assert len(db.tareas.pendientes(escenario["log"])) == 1
    assert len(db.recordatorios.pendientes_hasta(escenario["log"], _t(3, 23))) == 1


def test_auditoria(db: Repositorios, escenario: dict) -> None:
    db.auditoria.registrar(escenario["doctor"], "assign_pill", "pills", 1, {"name": "Vitamina D"})
    (registro,) = db.auditoria.listar()
    assert registro["action"] == "assign_pill"
    assert registro["details_json"] == {"name": "Vitamina D"}
