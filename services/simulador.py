"""Critical-condition simulator for the flight surgeon.

The doctor picks an astronaut, a scenario and a range of mission days. The
simulator copies the real measurements of those days, alters them according to the
scenario, stores the copies with ``is_simulated = 1``, and runs the report
pipeline in simulation mode. Reverting deletes every simulated row of that
astronaut, so real data is never modified.

Patterns:
    * **Strategy + Registry**: each scenario is a ``ScenarioEffect`` subclass that
      knows how to alter each table. Adding a scenario means adding one class to
      ``EFFECTS`` (and its value to ``EscenarioSimulacion`` + ``schema.sql``).
    * **Command**: a simulation is a stored action (``simulation_runs``) with a
      ``launch`` and a ``revert``, including the previous monitoring mode.

Current scope: one active simulation per astronaut at a time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from config import DB_PATH
from core.enums import EscenarioSimulacion, EstadoSimulacion
from services.database import Repositorios, sesion
from services.eventos import EventBus, default_bus
from services.reportes import DayResult, PipelineConfig, process_days

Row = dict[str, Any]

# Repositories whose rows a scenario may copy and alter
MEASUREMENT_REPOS: tuple[str, ...] = ("cardiovascular", "sangre", "saliva", "composicion", "radiacion")


class SimulationError(RuntimeError):
    """Invalid simulation request (unknown log, overlapping run, no data...)."""


# =====================================================================
# 1. SCENARIO EFFECTS (Strategy)
# =====================================================================

class ScenarioEffect:
    """Base effect: leaves every row unchanged.

    Subclasses override ``alter_<repo>`` methods. Each one receives a copy of a real
    row and returns the altered copy. ``extra_rows`` may add rows that do not exist in
    the real data (for example, an extra glucose reading).
    """

    scenario: ClassVar[EscenarioSimulacion]
    default_days: ClassVar[int] = 3
    description: ClassVar[str] = ""

    def alter(self, repo: str, row: Row, offset: int) -> Row:
        """Dispatches to ``alter_<repo>``. ``offset`` is 0 on the first simulated day."""
        method: Callable[[Row, int], Row] | None = getattr(self, f"alter_{repo}", None)
        return method(row, offset) if method else row

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        return {}


def _night(row: Row) -> bool:
    return row["sleep_stage"] > 0


class InfectionEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.INFECCION
    description = "Fiebre nocturna, taquicardia, VFC baja y respiración rápida (G1)."

    def alter_cardiovascular(self, row: Row, offset: int) -> Row:
        fever = 0.9 + 0.15 * offset          # °C, above the luteal-compensated threshold (+0.9)
        row["skin_temperature_c"] = round(row["skin_temperature_c"] + fever, 2)
        row["skin_temp_deviation_c"] = round((row["skin_temp_deviation_c"] or 0) + fever, 2)
        row["heart_rate_bpm"] = round(row["heart_rate_bpm"] + 8.5 * fever + 14, 1)   # Karjalainen
        row["rr_mean_ms"] = round(60000.0 / row["heart_rate_bpm"], 1)
        row["hrv_rmssd_ms"] = round(row["hrv_rmssd_ms"] * 0.5, 1)
        row["hrv_sdnn_ms"] = round(row["hrv_sdnn_ms"] * 0.55, 1)
        row["respiratory_rate_rpm"] = round(row["respiratory_rate_rpm"] + 7.5, 1)
        row["spo2_pct"] = round(max(row["spo2_pct"] - 1.2, 85.0), 1)
        return row


class CircadianEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.DISRUPCION_CIRCADIANA
    description = "Sueño menor a 5 h, CAR aplanado y cortisol nocturno alto (F1)."

    def alter_cardiovascular(self, row: Row, offset: int) -> Row:
        hour = int(str(row["recorded_at"])[11:13])
        if _night(row) and (hour >= 22 or hour < 2):                 # Lose ~2-3 h of sleep
            row["sleep_stage"] = 0
            row["enmo_mg"] = round(row["enmo_mg"] + 15, 1)
        row["hrv_rmssd_ms"] = round(row["hrv_rmssd_ms"] * 0.7, 1)
        row["heart_rate_bpm"] = round(row["heart_rate_bpm"] + 4, 1)
        return row

    def alter_saliva(self, row: Row, offset: int) -> Row:
        if row["collection_slot"] == "wake_30m":
            row["salivary_cortisol_nmol_l"] = round(row["salivary_cortisol_nmol_l"] * 0.75, 2)
        if row["collection_slot"] == "bedtime":
            row["salivary_cortisol_nmol_l"] = 6.0
        return row


class AtrophyEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.ATROFIA
    default_days = 1
    description = "Pérdida de masa del 9 % y de músculo (C1, C2)."

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        if offset != 0:
            return {}
        previous = [r for r in db.composicion.rango_dias(id_log, 1, day) if not r["is_simulated"]]
        if not previous:
            return {}
        base = dict(previous[-1])
        log = db.mission_logs.obtener(id_log)
        base.update({
            "mission_day": day,
            "recorded_at": _moment_on(db, id_log, day, 6, 15),
            "weight_kg": round(log["start_weight_kg"] * 0.91, 2),
            "muscle_pct": round(log["start_muscle_pct"] - 4.0, 2),
        })
        return {"composicion": [base]}


class SolarStormEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.TORMENTA_SOLAR
    default_days = 1
    description = "Evento de partículas solares: dosis ×2.5 y refugio (S4)."

    def alter_radiacion(self, row: Row, offset: int) -> Row:
        extra = row["daily_dose_msv"] * 1.5
        row["daily_dose_msv"] = round(row["daily_dose_msv"] + extra, 4)
        row["weighted_dose_msv"] = row["daily_dose_msv"]
        row["cumulative_dose_msv"] = round(row["cumulative_dose_msv"] + extra * (offset + 1), 4)
        row.update({"solar_event_flag": 1, "solar_event_type": "SEP", "data_source": "simulated"})
        return row


class HypoglycemiaEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.HIPOGLUCEMIA
    default_days = 1
    description = "Glucosa de 48 mg/dL a media mañana (S1 roja)."

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        return {"sangre": [{
            "id_mission_log": id_log, "mission_day": day,
            "recorded_at": _moment_on(db, id_log, day, 10, 30),
            "blood_glucose_mg_dl": 48.0, "measurement_context": "random",
        }]}


class CardiovascularEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.CARDIOVASCULAR
    default_days = 1
    description = "Crisis hipertensiva: presión ≥ 180/120 mmHg (S2 roja)."

    def alter_cardiovascular(self, row: Row, offset: int) -> Row:
        if not _night(row):
            row["estimated_systolic_bp_mmhg"] = row["estimated_systolic_bp_mmhg"] + 75
            row["estimated_diastolic_bp_mmhg"] = row["estimated_diastolic_bp_mmhg"] + 50
            row["estimated_map_mmhg"] = round(
                row["estimated_diastolic_bp_mmhg"]
                + (row["estimated_systolic_bp_mmhg"] - row["estimated_diastolic_bp_mmhg"]) / 3, 1
            )
            row["heart_rate_bpm"] = round(row["heart_rate_bpm"] + 10, 1)
        return row


class AnemiaEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.ANEMIA
    description = "Hemoglobina 2 g/dL bajo la base, taquicardia y SpO2 < 95 % (S3)."

    def alter_cardiovascular(self, row: Row, offset: int) -> Row:
        row["heart_rate_bpm"] = round(row["heart_rate_bpm"] + 12, 1)
        row["rr_mean_ms"] = round(60000.0 / row["heart_rate_bpm"], 1)
        row["spo2_pct"] = round(max(row["spo2_pct"] - 3.5, 85.0), 1)
        row["enmo_mg"] = round(row["enmo_mg"] * 0.6, 1)
        return row

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        log = db.mission_logs.obtener(id_log)
        return {"sangre": [{
            "id_mission_log": id_log, "mission_day": day,
            "recorded_at": _moment_on(db, id_log, day, 6, 25),
            "hemoglobin_g_dl": round(log["baseline_hb_g_dl"] - 2.0, 2), "measurement_context": "fasting",
        }]}


EFFECTS: dict[str, ScenarioEffect] = {
    effect.scenario.value: effect
    for effect in (InfectionEffect(), CircadianEffect(), AtrophyEffect(), SolarStormEffect(),
                   HypoglycemiaEffect(), CardiovascularEffect(), AnemiaEffect())
}


def available_scenarios() -> list[dict[str, Any]]:
    """Scenarios for the doctor's menu: value, description and default duration."""
    return [
        {"scenario": key, "description": e.description, "default_days": e.default_days}
        for key, e in EFFECTS.items()
    ]


def _moment_on(db: Repositorios, id_log: int, day: int, hour: int, minute: int) -> datetime:
    log = db.mission_logs.obtener(id_log)
    start = datetime.fromisoformat(log["start_date"])
    return start + timedelta(days=day - 1, hours=hour, minutes=minute)


# =====================================================================
# 2. COMMANDS: LAUNCH AND REVERT
# =====================================================================

@dataclass(frozen=True)
class SimulationResult:
    """What a launched simulation produced."""

    run_id: int
    rows_inserted: dict[str, int]
    days: list[DayResult]

    @property
    def alerts(self) -> int:
        return sum(len(d.alerts) for d in self.days)


def _copy_rows(db: Repositorios, effect: ScenarioEffect, id_log: int, days: Sequence[int]) -> dict[str, int]:
    inserted = {repo: 0 for repo in MEASUREMENT_REPOS}
    for offset, day in enumerate(days):
        for repo in MEASUREMENT_REPOS:
            real = [r for r in getattr(db, repo).rango_dias(id_log, day, day) if not r["is_simulated"]]
            altered = []
            for row in real:
                copy = {k: v for k, v in row.items() if k != "id"}
                altered.append({**effect.alter(repo, copy, offset), "is_simulated": 1})
            extra = [
                {**{k: v for k, v in r.items() if k != "id"}, "is_simulated": 1}
                for r in effect.extra_rows(db, id_log, day, offset).get(repo, [])
            ]
            inserted[repo] += getattr(db, repo).insertar_varios(altered + extra)
    return inserted


def launch_simulation(
    id_mission_log: int,
    scenario: str | EscenarioSimulacion,
    start_day: int,
    days: int | None = None,
    *,
    launched_by: int | None = None,
    db_path: Path | str = DB_PATH,
    config: PipelineConfig | None = None,
    bus: EventBus = default_bus,
    now: datetime | None = None,
) -> SimulationResult:
    """Creates simulated data for a scenario and recomputes reports and alerts.

    Args:
        id_mission_log: Astronaut's mission log.
        scenario: Value of ``EscenarioSimulacion`` (e.g. "infection").
        start_day: First mission day to alter.
        days: Number of days. Defaults to the scenario's ``default_days``.
        launched_by: Doctor's user id (stored for traceability).
        db_path: Database to use.
        config: Pipeline configuration.
        bus: Event bus where simulated alerts are published.
        now: Current moment (injectable for tests).

    Raises:
        SimulationError: Unknown scenario or log, active simulation, or no data.
    """
    key = EscenarioSimulacion(scenario).value
    effect = EFFECTS.get(key)
    if effect is None:
        raise SimulationError(f"No effect registered for scenario '{key}'.")
    duration = days or effect.default_days
    day_range = list(range(start_day, start_day + duration))

    with sesion(db_path) as db:
        log = db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise SimulationError(f"mission_log {id_mission_log} does not exist.")
        if db.simulaciones.listar(id_mission_log=id_mission_log, status=EstadoSimulacion.ACTIVA):
            raise SimulationError("This astronaut already has an active simulation. Revert it first.")
        last_day = db.cardiovascular.ultimo_dia(id_mission_log) or 0
        if start_day < 1 or day_range[-1] > last_day:
            raise SimulationError(f"Days must be within 1-{last_day} for this astronaut.")

        run_id = db.simulaciones.insertar(
            id_mission_log=id_mission_log, launched_by=launched_by, scenario=key,
            params_json={"previous_mode": log["active_monitoring_mode"], "days": duration},
            start_day=day_range[0], end_day=day_range[-1],
            started_at=now or datetime.now(UTC),
        )
        inserted = _copy_rows(db, effect, id_mission_log, day_range)
        db.auditoria.registrar(launched_by, "simulation_launched", "simulation_runs", run_id,
                               {"scenario": key, "days": day_range})

    results = process_days(id_mission_log, day_range, simulated=True, db_path=db_path,
                           config=config, bus=bus)
    return SimulationResult(run_id=run_id, rows_inserted=inserted, days=results)


def revert_simulation(
    run_id: int,
    *,
    reverted_by: int | None = None,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> dict[str, int]:
    """Deletes every simulated row of the astronaut and restores the monitoring mode.

    Returns:
        Rows deleted per table.

    Raises:
        SimulationError: If the run does not exist or is not active.
    """
    with sesion(db_path) as db:
        run = db.simulaciones.obtener(run_id)
        if run is None or run["status"] != EstadoSimulacion.ACTIVA.value:
            raise SimulationError(f"Simulation {run_id} is not active.")
        deleted = db.limpiar_simulados(run["id_mission_log"])
        previous_mode = (run["params_json"] or {}).get("previous_mode")
        if previous_mode:
            db.mission_logs.actualizar(run["id_mission_log"], active_monitoring_mode=previous_mode)
        db.simulaciones.actualizar(
            run_id, status=EstadoSimulacion.REVERTIDA, ended_at=now or datetime.now(UTC)
        )
        db.auditoria.registrar(reverted_by, "simulation_reverted", "simulation_runs", run_id, deleted)
    return deleted


def active_simulation(id_mission_log: int, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """The active simulation of an astronaut, if any (for the doctor's UI)."""
    with sesion(db_path) as db:
        runs = db.simulaciones.listar(id_mission_log=id_mission_log, status=EstadoSimulacion.ACTIVA)
    return runs[0] if runs else None
