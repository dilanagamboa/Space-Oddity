"""Generates the demo mission and fills ``data/astro.db``.

What it creates:
    * Users: 4 fictional astronauts, 2 flight surgeons and 1 admin.
    * One mission (LEO / ISS by default), mission logs and crew assignments.
    * Pills and meals (base for reminders).
    * Measurements following the team's sampling plan:
        - Watch: every 15 min (HR, HRV, BP, SpO2, RR, skin temp, sleep stage, ENMO).
        - Glucose: 2 days per week, 4 readings (fasting + 2 h after each meal).
        - Hemoglobin: weekly.
        - Saliva: 1 day per week, 4 samples (wake, +30 min, afternoon, bedtime).
        - Body composition chair: weekly, fasting.
        - Radiation: daily, with real solar events from the cache (DONKI / NOAA).
        - Menstrual cycle for female astronauts.
    * ``reference_values`` with every literature value used, and its source.

Demo cases (one per astronaut, visible in the dashboard):
    * Astronaut 1: one post-meal hyperglycemia (S1).
    * Astronaut 2: early infection on days 30-33 (G1).
    * Astronaut 3: circadian misalignment on days 20-27 (F1).
    * Astronaut 4: progressive mass and muscle loss (C1/C2).

Patterns:
    * **Strategy + Registry**: each table has its own generator class
      (``MeasurementGenerator``). Adding a measurement means adding a class to
      ``GENERATORS``. Each demo case is a ``Scenario`` that modifies physiology.
    * **Reproducible**: the same ``--seed`` always produces the same data.

Daily reports and alerts are NOT generated here: ``services/reportes.py`` computes
them from these measurements.

Run from the repo root::

    python -m scripts.generar_datos                  # 50 days, ISS, real NASA events if online
    python -m scripts.generar_datos --days 14 --no-sync
    python -m scripts.generar_datos --environment lunar --seed 7
"""


from __future__ import annotations

import argparse
import logging
import random
from datetime import UTC, date, datetime, time
from pathlib import Path

from config import SCHEMA_PATH, database_path
from core.enums import Rol, Sexo
from core.protocolo import mission_date
from scripts.crear_db import crear_base_datos
from scripts.generacion.mediciones import GENERATORS, AstronautContext
from scripts.generacion.perfiles import (
    ADMIN,
    CREW,
    DEFAULT_MISSION,
    DEMO_PASSWORD,
    DOCTOR,
    DOSE_RATE_MSV_DAY,
    REFERENCE_VALUES,
    MissionConfig,
)
from services.auth import hash_password
from services.database import Repositorios, sesion
from services.radiacion_api import solar_event_days, sync_space_weather

__all__ = ["CREW", "MissionConfig", "generate_mission"]


def _seed_people_and_mission(db: Repositorios, config: MissionConfig) -> list[tuple[int, int]]:
    """Creates users, mission, logs, assignments, pills and meals.

    Returns:
        ``(id_user, id_mission_log)`` of each astronaut, in ``CREW`` order.
    """
    def add_user(name: str, email: str, role: Rol, birth: date, sex: Sexo) -> int:
        return db.usuarios.insertar(full_name=name, email=email, role=role, birth_date=birth, sex=sex,
                                    password_hash=hash_password(DEMO_PASSWORD))

    add_user(*ADMIN, Rol.ADMIN, date(1980, 1, 1), Sexo.FEMENINO)
    id_doctor = add_user(*DOCTOR, Rol.DOCTOR, date(1978, 3, 10), Sexo.FEMENINO)
    id_mission = db.misiones.insertar(
        name=config.name, start_date=config.start_date,
        end_date=mission_date(config.start_date, config.days),
        description=f"Misión de demostración ({config.environment}), {config.days} días.",
    )

    crew_ids: list[tuple[int, int]] = []
    for p in CREW:
        id_user = add_user(p.full_name, p.email, Rol.ASTRONAUTA, p.birth_date, p.sex)
        id_log = db.mission_logs.insertar(
            id_user=id_user, id_mission=id_mission, start_date=config.start_date,
            start_weight_kg=p.weight_kg, start_height_cm=p.height_cm, start_muscle_pct=p.muscle_pct,
            start_fat_pct=p.fat_pct, baseline_hb_g_dl=p.baseline_hb_g_dl,
        )
        db.asignaciones.insertar(id_doctor=id_doctor, id_mission_log=id_log)
        db.pastillas.insertar(
            id_user=id_user, assigned_by=id_doctor, name="Vitamina D3", dose="1000 UI",
            description="Suplemento diario estándar en vuelo.", scheduled_time=time(8, 0),
            start_date=config.start_date,
        )
        for name, meal_type, kcal, protein, hm in (
            ("Desayuno", "breakfast", 600, 30, (7, 0)), ("Almuerzo", "lunch", 800, 35, (12, 30)),
            ("Merienda", "snack", 300, 10, (16, 30)), ("Cena", "dinner", 800, 35, (19, 0)),
        ):
            db.comidas.insertar(id_user=id_user, name=name, meal_type=meal_type,
                                target_calories_kcal=kcal, target_protein_g=protein,
                                scheduled_time=time(*hm))
        crew_ids.append((id_user, id_log))
    return crew_ids


def _seed_reference_values(db: Repositorios) -> None:
    retrieved = datetime.now(UTC)
    db.referencias.insertar_varios([{**ref, "retrieved_at": retrieved} for ref in REFERENCE_VALUES])


def _sync_nasa(db_path: Path, start: date, end: date) -> None:
    """Downloads real space weather events for the mission dates into the cache."""
    with sesion(db_path) as db:
        report = sync_space_weather(db, start, end)
    for source, error in report.errors.items():
        print(f"[AVISO] {source} no disponible: {error[:150]}")
    for source, count in report.inserted.items():
        print(f"[OK] {source}: {count} eventos nuevos")


def generate_mission(
    config: MissionConfig = DEFAULT_MISSION,
    db_path: Path | None = None,
    *,
    reset: bool = True,
    sync_nasa: bool = True,
) -> dict[str, int]:
    """Creates (or recreates) the database and fills it with the demo mission.

    Args:
        config: Mission parameters.
        db_path: Destination database.
        reset: Deletes the existing file first. With ``reset=False`` the space weather
            cache is kept (useful offline), but the database must not already
            contain a mission.
        sync_nasa: Downloads real space weather events before generating. Either way,
            the events already in the cache are used.

    Returns:
        Rows inserted per table.
    """
    db_path = Path(db_path or database_path())
    if config.environment not in DOSE_RATE_MSV_DAY:
        raise ValueError(f"environment must be one of {sorted(DOSE_RATE_MSV_DAY)}")
    if reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    crear_base_datos(db_path, SCHEMA_PATH)

    end = mission_date(config.start_date, config.days)
    if sync_nasa:
        try:
            _sync_nasa(db_path, config.start_date, end)
        except Exception as exc:  # Never block generation because of the network
            print(f"[AVISO] No se pudieron sincronizar eventos solares: {exc}")
    with sesion(db_path) as db:
        solar_days = solar_event_days(db, config.start_date, end)
    if solar_days:
        print(f"[OK] {len(solar_days)} días con eventos solares reales en la misión")
    else:
        print("[AVISO] Sin eventos solares reales en caché: se usa un evento simulado el día 35")

    counts: dict[str, int] = {}
    with sesion(db_path) as db:
        crew_ids = _seed_people_and_mission(db, config)
        _seed_reference_values(db)
        for profile, (id_user, id_log) in zip(CREW, crew_ids, strict=True):
            ctx = AstronautContext(
                config=config, profile=profile, id_user=id_user, id_mission_log=id_log,
                rng=random.Random(f"{config.seed}-{profile.email}"), solar_days=solar_days,
            )
            for generator in GENERATORS:
                table = type(generator).__name__.removesuffix("Generator")
                counts[table] = counts.get(table, 0) + generator.save(db, generator.generate(ctx))

    with sesion(db_path) as db:
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera la misión de demo en data/astro.db.")
    parser.add_argument("--days", type=int, default=MissionConfig.days)
    parser.add_argument("--start", type=date.fromisoformat, default=MissionConfig.start_date,
                        help="Fecha de inicio YYYY-MM-DD (por defecto 2024-05-01).")
    parser.add_argument("--environment", choices=sorted(DOSE_RATE_MSV_DAY), default="iss")
    parser.add_argument("--seed", type=int, default=MissionConfig.seed)
    parser.add_argument("--db", type=Path, help="Base destino (por defecto, data/astro.db).")
    parser.add_argument("--no-sync", action="store_true", help="No consulta DONKI ni NOAA.")
    parser.add_argument("--keep", action="store_true", help="No borra la base existente.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)  # Los avisos de red ya se imprimen resumidos

    config = MissionConfig(environment=args.environment, start_date=args.start,
                           days=args.days, seed=args.seed)
    counts = generate_mission(config, args.db, reset=not args.keep, sync_nasa=not args.no_sync)
    print(f"[OK] Misión '{config.name}' generada ({config.days} días, {config.environment}):")
    for table, count in counts.items():
        print(f"     {table:<20} {count:>6} filas")


if __name__ == "__main__":
    main()
