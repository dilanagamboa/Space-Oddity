"""Daily health report pipeline (I/O orchestration).

For one astronaut and one mission day::

    load       measurements of the day + previous reports
    summarize  core/resumen_diario.py   (sleep, vitals, body, saliva, glucose)
    evaluate   core/evaluacion.py       (baselines, rangos C/F/G/S, score, flags)
    save       daily_health_reports     (upsert: running twice never duplicates)
    alerts     core/alertas.py          (group, plan, filter) -> alerts table
    publish    services/eventos.py      (after commit: notifications, etc.)

All clinical logic lives in ``core/``. This module only reads, calls ``core`` and writes.

Patterns:
    * **Pipeline / Template Method**: ``DailyReportPipeline.process_day`` runs one
      small method per step.
    * **Unit of Work**: a range of days runs inside one ``sesion()``. Events are
      published only after it commits.
    * **Observer**: new alerts go to ``services.eventos``; this module does not know
      who listens.
    * **Idempotent**: a day's report is updated in place and its alerts rebuilt.

Simulation overlay: with ``simulated=True`` each table uses the simulated rows of the
day when they exist, and the real ones otherwise. Baselines always come from real
reports, so a simulation never contaminates the astronaut's baseline.

Current scope: one flight surgeon sees the whole crew (``tasks`` and
``crew_assignments`` are not used).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from config import DB_PATH
from core.alertas import AlertDraft, build_alerts, filter_repeated
from core.evaluacion import (
    DEFAULT_SETTINGS,
    ClinicalSettings,
    derived_flags,
    evaluate_day,
    personal_signals,
    readiness,
)
from core.protocolo import SLEEP_WINDOW, SleepWindow, mission_date, mission_moment
from core.rangos import EvaluationResult
from core.resumen_diario import (
    age_on,
    latest_hemoglobin,
    summarize_body,
    summarize_glucose,
    summarize_saliva,
    summarize_watch,
)
from services.database import Repositorios, sesion
from services.eventos import AlertEvent, EventBus, default_bus

Row = dict[str, Any]

# Report columns that identify the row; every other column is computed by the pipeline
REPORT_KEY_COLUMNS: frozenset[str] = frozenset(
    {"id", "id_mission_log", "mission_day", "report_date", "is_simulated"}
)


@dataclass(frozen=True)
class PipelineConfig:
    """Parameters of the pipeline.

    Attributes:
        clinical: Baseline minimums, z cap and score scaling (``core.evaluacion``).
        sleep_window: Scheduled sleep window (UTC).
        baseline_days: Previous days used as personal baseline (28, as in the team
            document for temperature and HRV).
        repeat_window_hours: Anti-repetition window for alerts. Evaluations are daily,
            so 23.5 h re-issues a persistent alert once per mission day, never twice.
        report_time: Mission time at which the daily report and its alerts are stamped.
    """

    clinical: ClinicalSettings = DEFAULT_SETTINGS
    sleep_window: SleepWindow = SLEEP_WINDOW
    baseline_days: int = 28
    repeat_window_hours: float = 23.5
    report_time: time = time(23, 59)


@dataclass
class DayResult:
    """Outcome of processing one day."""

    id_mission_log: int
    mission_day: int
    is_simulated: bool
    report_id: int | None = None
    evaluation: EvaluationResult | None = None
    readiness_score: float | None = None
    alerts: list[tuple[int, AlertDraft]] = field(default_factory=list)

    @property
    def processed(self) -> bool:
        return self.report_id is not None


class DailyReportPipeline:
    """Builds and stores the daily report of one astronaut (one step per method)."""

    def __init__(self, db: Repositorios, config: PipelineConfig | None = None) -> None:
        self.db = db
        self.config = config or PipelineConfig()

    def process_day(self, id_mission_log: int, day: int, *, simulated: bool = False) -> DayResult:
        result = DayResult(id_mission_log, day, simulated)
        watch = summarize_watch(
            self._rows("cardiovascular", id_mission_log, day, day, simulated), self.config.sleep_window
        )
        if watch is None:                                     # No watch data: nothing to report
            return result

        ctx = self._load_context(id_mission_log, day)
        metrics = self._summarize(ctx, watch, simulated)
        baseline_reports, recent_reports = self._history(id_mission_log, day, simulated)
        clinical = self.config.clinical
        signals = personal_signals(metrics, baseline_reports, clinical)
        result.evaluation = evaluate_day(
            metrics, signals, recent_reports, sex=ctx["user"]["sex"], cycle_phase=ctx["cycle_phase"]
        )
        result.readiness_score = readiness(metrics, signals, clinical)
        flags = derived_flags(metrics, signals, ctx["log"]["baseline_hb_g_dl"])
        result.report_id = self._save_report(ctx, {**metrics, **signals, **flags}, result)
        self._update_monitoring_mode(ctx, result)
        result.alerts = self._save_alerts(ctx, result)
        return result

    # --- Load -----------------------------------------------------------------------
    def _load_context(self, id_mission_log: int, day: int) -> dict[str, Any]:
        log = self.db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise ValueError(f"mission_log {id_mission_log} does not exist")
        user = self.db.usuarios.obtener(log["id_user"])
        report_date = mission_date(log["start_date"], day)
        cycle = self.db.ciclos.listar(id_user=log["id_user"], recorded_date=report_date.isoformat())
        return {
            "log": log,
            "user": user,
            "day": day,
            "report_date": report_date,
            "moment": mission_moment(log["start_date"], day, self.config.report_time),
            "age": age_on(user["birth_date"], report_date),
            "cycle_phase": cycle[0]["cycle_phase"] if cycle else None,
        }

    def _rows(self, repo: str, id_log: int, first: int, last: int, simulated: bool) -> list[Row]:
        """Rows of a measurement table, applying the simulation overlay day by day."""
        rows = getattr(self.db, repo).rango_dias(id_log, first, last)
        if not simulated:
            return [r for r in rows if not r["is_simulated"]]
        by_day: dict[int, dict[bool, list[Row]]] = {}
        for row in rows:
            groups = by_day.setdefault(row["mission_day"], {True: [], False: []})
            groups[bool(row["is_simulated"])].append(row)
        return [row for groups in by_day.values() for row in (groups[True] or groups[False])]

    def _history(self, id_log: int, day: int, simulated: bool) -> tuple[list[Row], list[Row]]:
        """Previous reports: ``(real ones for baselines, active view for trends)``."""
        first = max(1, day - self.config.baseline_days)
        reports = self.db.reportes.rango_dias(id_log, first, day - 1) if day > 1 else []
        real = [r for r in reports if not r["is_simulated"]]
        if not simulated:
            return real, real
        simulated_days = {r["mission_day"]: r for r in reports if r["is_simulated"]}
        overlay = {r["mission_day"]: r for r in real} | simulated_days
        return real, [overlay[d] for d in sorted(overlay)]

    # --- Summarize ------------------------------------------------------------------
    def _summarize(self, ctx: dict[str, Any], watch: dict[str, Any], simulated: bool) -> dict[str, Any]:
        log, day = ctx["log"], ctx["day"]
        metrics = dict(watch)

        bodies = self._rows("composicion", log["id"], 1, day, simulated)
        latest_body = max(bodies, key=lambda r: r["recorded_at"]) if bodies else None
        metrics["new_body_measurement"] = bool(latest_body and latest_body["mission_day"] == day)
        metrics.update(summarize_body(
            latest_body, start_weight_kg=log["start_weight_kg"], height_cm=log["start_height_cm"],
            sex=ctx["user"]["sex"], age_years=ctx["age"], mission_day=day,
        ))
        metrics.update(summarize_saliva(self._rows("saliva", log["id"], day, day, simulated)))
        blood = self._rows("sangre", log["id"], 1, day, simulated)
        metrics.update(summarize_glucose([r for r in blood if r["mission_day"] == day]))
        metrics["hemoglobin_g_dl"] = latest_hemoglobin(blood)

        radiation = self._rows("radiacion", log["id"], day, day, simulated)
        if radiation:
            metrics["cumulative_radiation_msv"] = radiation[-1]["cumulative_dose_msv"]
            metrics["solar_event_flag"] = radiation[-1]["solar_event_flag"]
        return metrics

    # --- Save -----------------------------------------------------------------------
    def _save_report(self, ctx: dict[str, Any], values: dict[str, Any], result: DayResult) -> int:
        values = {
            **values,
            "readiness_score": result.readiness_score,
            "triage_status": result.evaluation.triage_status,
            "recommended_sampling_mode": result.evaluation.recommended_sampling_mode,
        }
        row = {c: values.get(c) for c in self.db.reportes.columnas - REPORT_KEY_COLUMNS}
        row["glim_malnutrition_risk"] = row["glim_malnutrition_risk"] or 0

        log, day = ctx["log"], ctx["day"]
        existing = self.db.reportes.del_dia(log["id"], day, simulado=result.is_simulated)
        if existing:
            self.db.reportes.actualizar(existing["id"], **row)
            return int(existing["id"])
        return self.db.reportes.insertar(
            id_mission_log=log["id"], mission_day=day, report_date=ctx["report_date"],
            is_simulated=int(result.is_simulated), **row,
        )

    def _update_monitoring_mode(self, ctx: dict[str, Any], result: DayResult) -> None:
        """Only the most recent day decides the active mode (read by recordatorios.py)."""
        latest_day = self.db.reportes.ultimo_dia(ctx["log"]["id"]) or 0
        if ctx["day"] >= latest_day:
            self.db.mission_logs.actualizar(
                ctx["log"]["id"], active_monitoring_mode=result.evaluation.recommended_sampling_mode
            )

    def _save_alerts(self, ctx: dict[str, Any], result: DayResult) -> list[tuple[int, AlertDraft]]:
        log, day, simulated = ctx["log"], ctx["day"], int(result.is_simulated)
        for old in self.db.alertas.listar(id_mission_log=log["id"], mission_day=day, is_simulated=simulated):
            self.db.alertas.eliminar(old["id"])                    # Rebuild the day (idempotent)

        drafts = build_alerts(result.evaluation, mission_day=day)
        recent = self.db.alertas.listar(id_mission_log=log["id"], mission_day=day - 1, is_simulated=simulated)
        drafts = filter_repeated(drafts, recent, ctx["moment"], self.config.repeat_window_hours)
        return [
            (self.db.alertas.insertar(**d.to_db_row(log["id"], is_simulated=bool(simulated)),
                                      created_at=ctx["moment"]), d)
            for d in drafts
        ]


# =====================================================================
# PUBLIC ENTRY POINTS
# =====================================================================

def _publish(results: Sequence[DayResult], db_path: Path | str, bus: EventBus) -> None:
    """Publishes the alerts of the processed days (called after the commit)."""
    pending = [(r, alert_id, draft) for r in results for alert_id, draft in r.alerts]
    if not pending:
        return
    with sesion(db_path) as db:
        astronauts: dict[int, dict[str, Any]] = {}
        for result, _, _ in pending:
            if result.id_mission_log not in astronauts:
                user = db.usuarios.obtener(db.mission_logs.obtener(result.id_mission_log)["id_user"])
                astronauts[result.id_mission_log] = {k: user[k] for k in ("id", "full_name", "email")}
    for result, alert_id, draft in pending:
        bus.publish(AlertEvent(
            alert_id=alert_id, id_mission_log=result.id_mission_log,
            astronaut=astronauts[result.id_mission_log], alert=draft, is_simulated=result.is_simulated,
        ))


def process_days(
    id_mission_log: int,
    days: Sequence[int],
    *,
    simulated: bool = False,
    db_path: Path | str = DB_PATH,
    config: PipelineConfig | None = None,
    bus: EventBus = default_bus,
) -> list[DayResult]:
    """Processes several days of one astronaut in order, in a single transaction.

    Days are processed in ascending order because each day's baseline uses the
    reports of the previous days. Alerts are published once the transaction commits.
    """
    with sesion(db_path) as db:
        pipeline = DailyReportPipeline(db, config)
        results = [pipeline.process_day(id_mission_log, d, simulated=simulated) for d in sorted(days)]
    _publish(results, db_path, bus)
    return results


def process_mission_log(
    id_mission_log: int,
    *,
    db_path: Path | str = DB_PATH,
    config: PipelineConfig | None = None,
    bus: EventBus = default_bus,
) -> list[DayResult]:
    """Processes every day that has watch data for a mission log (real data only)."""
    with sesion(db_path) as db:
        last_day = db.cardiovascular.ultimo_dia(id_mission_log) or 0
    return process_days(id_mission_log, range(1, last_day + 1), db_path=db_path, config=config, bus=bus)
