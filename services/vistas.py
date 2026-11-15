"""Read models for the UI: each function returns exactly what one screen shows.

Pages never query the database: they call these functions and get plain
dictionaries and lists, ready to render with Streamlit or the shared widgets.

Current scope: one flight surgeon sees the whole crew.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from core.enums import Sexo
from core.resumen_diario import age_on
from services import recordatorios
from services.database import Repositorios, sesion

Row = dict[str, Any]
ItemKind = Literal["pill", "meal", "notice"]
ITEM_REPOS: dict[ItemKind, str] = {"pill": "pastillas", "meal": "comidas", "notice": "avisos"}

REPORT_DAYS_SHOWN = 14
ALERTS_SHOWN = 20
CYCLE_DAYS_SHOWN = 28


# =====================================================================
# 1. PEOPLE
# =====================================================================

def person(user: Mapping[str, Any], today: date | None = None) -> Row:
    """Name split as in the design (first name / last names), sex and age."""
    first, _, last = user["full_name"].partition(" ")
    return {
        "id_user": user["id"],
        "name": first,
        "last_name": last,
        "full_name": user["full_name"],
        "email": user["email"],
        "sex": "F" if user["sex"] == Sexo.FEMENINO else "M",
        "birth_date": user["birth_date"],
        "age": age_on(user["birth_date"], today or date.today()),
    }


def user_profile(id_user: int, *, db_path: Path | str | None = None) -> Row:
    with sesion(db_path) as db:
        return person(db.usuarios.obtener(id_user))


def log_of_user(id_user: int, *, db_path: Path | str | None = None) -> int | None:
    """Active mission log of an astronaut (``None`` for doctors and admins)."""
    with sesion(db_path) as db:
        logs = [log for log in db.mission_logs.activos() if log["id_user"] == id_user]
    return logs[0]["id"] if logs else None


def _crew_rows(db: Repositorios) -> list[Row]:
    rows = []
    for log in db.mission_logs.activos():
        rows.append({
            **person(db.usuarios.obtener(log["id_user"])),
            "id_log": log["id"],
            "monitoring_mode": log["active_monitoring_mode"],
        })
    return rows


def crew(search: str = "", *, db_path: Path | str | None = None) -> list[Row]:
    """Patients table (name, last name, sex, age, monitoring mode), filtered by text."""
    text = search.strip().lower()
    with sesion(db_path) as db:
        rows = _crew_rows(db)
    return [r for r in rows if text in f"{r['full_name']} {r['email']}".lower()]


# =====================================================================
# 2. ASTRONAUT DETAIL AND DASHBOARD
# =====================================================================

def _current_day(db: Repositorios, id_log: int) -> int:
    """Latest mission day with watch data (the demo's "today")."""
    return db.cardiovascular.ultimo_dia(id_log) or 0


def astronaut_detail(id_log: int, *, db_path: Path | str | None = None) -> Row:
    """Header plus every section of the astronaut detail screen."""
    with sesion(db_path) as db:
        log = db.mission_logs.obtener(id_log)
        user = db.usuarios.obtener(log["id_user"])
        last_day = _current_day(db, id_log)
        reports = db.reportes.rango_dias(id_log, last_day - REPORT_DAYS_SHOWN + 1, last_day,
                                         incluir_simulados=False)
        live = db.cardiovascular.rango_dias(id_log, last_day, last_day, incluir_simulados=False)
        cycle = (db.ciclos.listar(id_user=user["id"], orden="-recorded_date", limite=CYCLE_DAYS_SHOWN)
                 if user["sex"] == Sexo.FEMENINO else None)
        return {
            **person(user),
            "id_log": id_log,
            "monitoring_mode": log["active_monitoring_mode"],
            "reports": list(reversed(reports)),
            "pills": db.pastillas.activas_de(user["id"]),
            "meals": db.comidas.activas_de(user["id"]),
            "notices": db.avisos.activas_de(user["id"]),
            "alerts": db.alertas.listar(id_mission_log=id_log, is_simulated=0, orden="-created_at",
                                        limite=ALERTS_SHOWN),
            "live": live[-1] if live else None,
            "cycle": cycle,
        }


def crew_status(*, db_path: Path | str | None = None) -> list[Row]:
    """Dashboard: latest real report and open alerts of each astronaut."""
    with sesion(db_path) as db:
        rows = _crew_rows(db)
        for row in rows:
            last_day = db.reportes.ultimo_dia(row["id_log"]) or 0
            report = db.reportes.del_dia(row["id_log"], last_day) or {}
            row.update({
                "mission_day": last_day,
                "triage": report.get("triage_status"),
                "readiness": report.get("readiness_score"),
                "open_alerts": len(db.alertas.activas(row["id_log"])),
            })
    return rows


def my_notifications(id_log: int, *, db_path: Path | str | None = None) -> Row:
    """Astronaut's notifications: today's reminders (creates them if missing) and open alerts."""
    with sesion(db_path) as db:
        day = _current_day(db, id_log)
    recordatorios.generate_daily_reminders(id_log, day, db_path=db_path)
    with sesion(db_path) as db:
        return {
            "reminders": db.recordatorios.listar(id_mission_log=id_log, mission_day=day, is_simulated=0,
                                                 orden="scheduled_at"),
            "alerts": db.alertas.activas(id_log),
        }


def active_items(kind: ItemKind, id_user: int, *, db_path: Path | str | None = None) -> list[Row]:
    """Active pills, meals or notices of an astronaut."""
    with sesion(db_path) as db:
        return getattr(db, ITEM_REPOS[kind]).activas_de(id_user)


def item(kind: ItemKind, item_id: int, *, db_path: Path | str | None = None) -> Row | None:
    """One pill, meal or notice, to prefill the edit form."""
    with sesion(db_path) as db:
        return getattr(db, ITEM_REPOS[kind]).obtener(item_id)
