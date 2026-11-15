"""Critical-condition simulator for the flight surgeon.

The doctor picks an astronaut, a scenario and a range of mission days. The
simulator copies the real measurements of those days, alters them according to the
scenario, stores the copies with ``is_simulated = 1``, and runs the report
pipeline in simulation mode. Reverting deletes every simulated row of that
astronaut, so real data is never modified.

Patterns:
    * **Strategy + Registry**: each scenario is a ``ScenarioEffect`` subclass that
      knows how to alter each table, using the physiology shared with the data
      generator (``core.escenarios``). Adding a scenario means adding one class to
      ``EFFECTS`` (and its value to ``EscenarioSimulacion`` + ``schema.sql``).
    * **Command**: a simulation is a stored action (``simulation_runs``) with a
      ``launch`` and a ``revert``, including the previous monitoring mode.

Current scope: one active simulation per astronaut at a time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from core.enums import EscenarioSimulacion, EstadoSimulacion
from core.escenarios import (
    ANEMIA,
    BLUNTED_CAR_RATIO,
    CIRCADIAN,
    HIGH_NIGHT_CORTISOL_NMOL_L,
    HYPERTENSIVE_CRISIS,
    SPE_DOSE_FACTOR,
    WatchChange,
    infection,
)
from core.protocolo import CHAIR_AT, HEMOGLOBIN_AT, HourMinute, mission_moment
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

    ``watch_change`` alters the watch signals with the shared physiology of
    ``core.escenarios``. ``alter_rows`` may change any other table, and
    ``extra_rows`` may add rows that do not exist in the real data.
    """

    scenario: ClassVar[EscenarioSimulacion]
    default_days: ClassVar[int] = 3
    description: ClassVar[str] = ""

    def watch_change(self, offset: int) -> WatchChange | None:
        """Change of the watch signals on the ``offset``-th simulated day (0 = first)."""
        return None

    def alter_rows(self, repo: str, rows: list[Row], offset: int) -> list[Row]:
        change = self.watch_change(offset) if repo == "cardiovascular" else None
        return [change.apply(r) for r in rows] if change else rows

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        return {}


def _moment(db: Repositorios, id_log: int, day: int, at: HourMinute) -> datetime:
    return mission_moment(db.mission_logs.obtener(id_log)["start_date"], day, at)


class InfectionEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.INFECCION
    description = "Fiebre nocturna, taquicardia, VFC baja y respiración rápida (G1)."

    def watch_change(self, offset: int) -> WatchChange:
        return infection(0.9 + 0.15 * offset)       # Above the luteal-compensated threshold (+0.9 °C)


class CircadianEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.DISRUPCION_CIRCADIANA
    description = "Sueño menor a 5 h, CAR aplanado y cortisol nocturno alto (F1)."
    LOST_SLEEP_HOURS: ClassVar[frozenset[int]] = frozenset({22, 23, 0, 1})

    def watch_change(self, offset: int) -> WatchChange:
        return CIRCADIAN

    def alter_rows(self, repo: str, rows: list[Row], offset: int) -> list[Row]:
        rows = super().alter_rows(repo, rows, offset)
        if repo == "cardiovascular":
            for row in rows:
                if row["sleep_stage"] > 0 and int(str(row["recorded_at"])[11:13]) in self.LOST_SLEEP_HOURS:
                    row.update(sleep_stage=0, enmo_mg=round(row["enmo_mg"] + 15, 1))
        if repo == "saliva":
            by_slot = {r["collection_slot"]: r for r in rows}
            if "wake_0m" in by_slot and "wake_30m" in by_slot:
                wake = by_slot["wake_0m"]["salivary_cortisol_nmol_l"]
                by_slot["wake_30m"]["salivary_cortisol_nmol_l"] = round(wake * BLUNTED_CAR_RATIO, 2)
            if "bedtime" in by_slot:
                by_slot["bedtime"]["salivary_cortisol_nmol_l"] = HIGH_NIGHT_CORTISOL_NMOL_L
        return rows


class AtrophyEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.ATROFIA
    default_days = 1
    description = "Pérdida de masa del 9 % y de músculo (C1, C2)."

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        previous = [r for r in db.composicion.rango_dias(id_log, 1, day) if not r["is_simulated"]]
        if offset != 0 or not previous:
            return {}
        log = db.mission_logs.obtener(id_log)
        return {"composicion": [{
            **previous[-1],
            "mission_day": day,
            "recorded_at": _moment(db, id_log, day, CHAIR_AT),
            "weight_kg": round(log["start_weight_kg"] * 0.91, 2),
            "muscle_pct": round(log["start_muscle_pct"] - 4.0, 2),
        }]}


class SolarStormEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.TORMENTA_SOLAR
    default_days = 1
    description = "Evento de partículas solares: dosis ×2.5 y refugio (S4)."

    def alter_rows(self, repo: str, rows: list[Row], offset: int) -> list[Row]:
        if repo != "radiacion":
            return rows
        for row in rows:
            extra = row["daily_dose_msv"] * (SPE_DOSE_FACTOR - 1)
            row.update(
                daily_dose_msv=round(row["daily_dose_msv"] + extra, 4),
                cumulative_dose_msv=round(row["cumulative_dose_msv"] + extra * (offset + 1), 4),
                solar_event_flag=1, solar_event_type="SEP", data_source="simulated",
            )
            row["weighted_dose_msv"] = row["daily_dose_msv"]
        return rows


class HypoglycemiaEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.HIPOGLUCEMIA
    default_days = 1
    description = "Glucosa de 48 mg/dL a media mañana (S1 roja)."

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        return {"sangre": [{
            "id_mission_log": id_log, "mission_day": day, "recorded_at": _moment(db, id_log, day, (10, 30)),
            "blood_glucose_mg_dl": 48.0, "measurement_context": "random",
        }]}


class CardiovascularEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.CARDIOVASCULAR
    default_days = 1
    description = "Crisis hipertensiva: presión ≥ 180/120 mmHg (S2 roja)."

    def watch_change(self, offset: int) -> WatchChange:
        return HYPERTENSIVE_CRISIS


class AnemiaEffect(ScenarioEffect):
    scenario = EscenarioSimulacion.ANEMIA
    description = "Hemoglobina 2 g/dL bajo la base, taquicardia y SpO2 < 95 % (S3)."

    def watch_change(self, offset: int) -> WatchChange:
        return ANEMIA

    def extra_rows(self, db: Repositorios, id_log: int, day: int, offset: int) -> dict[str, list[Row]]:
        log = db.mission_logs.obtener(id_log)
        return {"sangre": [{
            "id_mission_log": id_log, "mission_day": day,
            "recorded_at": _moment(db, id_log, day, HEMOGLOBIN_AT),
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
    """Copies the real rows of each day, alters them and stores them as simulated."""
    inserted = dict.fromkeys(MEASUREMENT_REPOS, 0)
    for offset, day in enumerate(days):
        for repo in MEASUREMENT_REPOS:
            real = [
                {k: v for k, v in r.items() if k != "id"}
                for r in getattr(db, repo).rango_dias(id_log, day, day) if not r["is_simulated"]
            ]
            extra = effect.extra_rows(db, id_log, day, offset).get(repo, [])
            rows = effect.alter_rows(repo, real, offset) + extra
            inserted[repo] += getattr(db, repo).insertar_varios(
                [{**{k: v for k, v in r.items() if k != "id"}, "is_simulated": 1} for r in rows]
            )
    return inserted


def launch_simulation(
    id_mission_log: int,
    scenario: str | EscenarioSimulacion,
    start_day: int,
    days: int | None = None,
    *,
    launched_by: int | None = None,
    db_path: Path | str | None = None,
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
    db_path: Path | str | None = None,
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


def active_simulation(id_mission_log: int, *, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """The active simulation of an astronaut, if any (for the doctor's UI)."""
    with sesion(db_path) as db:
        runs = db.simulaciones.listar(id_mission_log=id_mission_log, status=EstadoSimulacion.ACTIVA)
    return runs[0] if runs else None
