"""Daily health report pipeline.

For one astronaut and one mission day::

    load       measurements of the day + previous reports (baselines)
    summarize  core/resumen_diario.py   (sleep, vitals, body, saliva, glucose)
    baseline   personal z-scores against the previous 28 days
    evaluate   core/rangos.py           (problems C, F, G and safety S, triage)
    score      core/puntaje.py          (readiness 0-100)
    save       daily_health_reports     (upsert: running twice never duplicates)
    alerts     core/alertas.py          (group, plan, filter) -> alerts table
    publish    services/eventos.py      (after commit: notifications, etc.)

Patterns:
    * **Pipeline / Template Method**: ``DailyReportPipeline.process_day`` calls one
      small method per step. Each step can be tested or overridden alone.
    * **Unit of Work**: the whole range runs inside one ``sesion()``. Events are
      published only after it commits.
    * **Observer**: new alerts go to ``services.eventos``. This module does not know
      who listens (email, UI...).
    * **Idempotent**: the report of a day is updated in place and its alerts are
      rebuilt, so the pipeline can be re-run safely.

Simulation overlay: with ``simulated=True`` each table uses the simulated rows of
the day when they exist, and the real ones otherwise. Baselines always come from
real reports, so a simulation never contaminates the astronaut's baseline.

Current scope: one flight surgeon sees the whole crew. ``tasks`` and
``crew_assignments`` are not used here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from config import DB_PATH
from core.alertas import AlertDraft, build_alerts, filter_repeated
from core.ecuaciones import (
    compute_anemia_suspicion_index,
    compute_hrv_log_z_score,
    compute_oxygen_delivery_do2,
    evaluate_liebermeister_rule,
)
from core.puntaje import evaluate_daily_readiness
from core.rangos import (
    EvaluationResult,
    evaluate_overall_health,
    evaluate_problem_c,
    evaluate_problem_f,
    evaluate_problem_g,
    evaluate_safety,
)
from core.resumen_diario import (
    DEFAULT_SLEEP_WINDOW,
    Baseline,
    SleepWindow,
    age_on,
    latest_hemoglobin,
    summarize_body,
    summarize_glucose,
    summarize_saliva,
    summarize_watch,
)
from services.database import Repositorios, sesion
from services.eventos import AlertEvent, EventBus, default_bus

Row = Mapping[str, Any]

# Columns of daily_health_reports that this pipeline fills (everything else is NULL/default)
REPORT_COLUMNS: tuple[str, ...] = (
    "sleep_total_min", "sleep_awake_min", "sleep_light_min", "sleep_deep_min", "sleep_rem_min",
    "sleep_efficiency_pct", "resting_hr_bpm", "hrv_rmssd_daily_ms", "hrv_sdnn_daily_ms",
    "hrv_baseline_28d_ms", "hrv_z_score", "nocturnal_skin_temp_c", "skin_temp_baseline_28d_c",
    "skin_temp_delta_c", "respiratory_rate_daily_rpm", "spo2_daily_pct", "enmo_daily_mean_mg",
    "sedentary_min", "light_activity_min", "mvpa_min", "bmi", "delta_mass_pct", "smi_total_kg_m2",
    "bmr_mifflin_kcal", "glim_malnutrition_risk", "car_absolute_nmol_l", "car_pct",
    "cortisol_diurnal_slope", "aucg_cortisol", "aucg_saa", "sns_hpa_asymmetry_idx",
    "cortisol_dhea_molar_ratio", "glucose_cv_pct", "glucose_tir_pct", "gmi_pct",
    "liebermeister_dissociation_flag", "do2_ml_min", "anemia_suspicion_flag",
    "cumulative_radiation_msv", "readiness_score", "triage_status", "recommended_sampling_mode",
)


# =====================================================================
# 1. CONFIGURATION AND RESULTS
# =====================================================================

@dataclass(frozen=True)
class PipelineConfig:
    """Tunable parameters of the pipeline.

    Attributes:
        sleep_window: Scheduled sleep window (UTC).
        baseline_days: Days before the current one used as personal baseline (28 days,
            as in the team document for temperature and HRV).
        baseline_min_days: Minimum days needed to compute a z-score. 14 follows the team
            document ("línea base personal de las primeras 2 semanas"): during the first
            two weeks only absolute thresholds are evaluated.
        saliva_baseline_min: Minimum previous saliva days (saliva is weekly).
        z_cap: Z-scores are clipped to ``±z_cap`` before the readiness score, so one
            extreme metric cannot erase the rest of the score by itself.
        scale_non_z_metrics: Passed to ``core.puntaje``. ``True`` puts temperature,
            mass loss and glucose CV on the same scale as z-scores.
        repeat_window_hours: Anti-repetition window for alerts. Evaluations are daily,
            so 23.5 h re-issues a persistent alert once per mission day, never twice.
        report_time: Mission time at which the daily report and its alerts are stamped.
    """

    sleep_window: SleepWindow = DEFAULT_SLEEP_WINDOW
    baseline_days: int = 28
    baseline_min_days: int = 14
    saliva_baseline_min: int = 3
    z_cap: float = 4.0
    scale_non_z_metrics: bool = True
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
    skipped_reason: str | None = None

    @property
    def processed(self) -> bool:
        return self.report_id is not None


# =====================================================================
# 2. PIPELINE
# =====================================================================

class DailyReportPipeline:
    """Builds and stores the daily report of one astronaut (one step per method)."""

    def __init__(self, db: Repositorios, config: PipelineConfig | None = None) -> None:
        self.db = db
        self.config = config or PipelineConfig()

    # --- Template method -----------------------------------------------------
    def process_day(self, id_mission_log: int, day: int, *, simulated: bool = False) -> DayResult:
        result = DayResult(id_mission_log, day, simulated)
        context = self._load_context(id_mission_log, day)
        watch_rows = self._rows("cardiovascular", id_mission_log, day, day, simulated)
        watch = summarize_watch(watch_rows, self.config.sleep_window)
        if watch is None:
            result.skipped_reason = "no watch data for this day"
            return result

        metrics = self._summarize(context, watch, simulated)
        history = self._history(id_mission_log, day, simulated)
        signals = self._personal_baselines(metrics, history)
        result.evaluation = self._evaluate(context, metrics, signals, history)
        result.readiness_score = self._score(metrics, signals)
        result.report_id = self._save_report(context, metrics, signals, result)
        self._update_monitoring_mode(context, result)
        result.alerts = self._save_alerts(context, result)
        return result

    # --- Step 1: load ----------------------------------------------------------
    def _load_context(self, id_mission_log: int, day: int) -> dict[str, Any]:
        log = self.db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise ValueError(f"mission_log {id_mission_log} does not exist")
        user = self.db.usuarios.obtener(log["id_user"])
        report_date = date.fromisoformat(log["start_date"]) + timedelta(days=day - 1)
        cycle = self.db.ciclos.listar(id_user=log["id_user"], recorded_date=report_date.isoformat())
        return {
            "log": log,
            "user": user,
            "day": day,
            "report_date": report_date,
            "moment": datetime.combine(report_date, self.config.report_time),
            "age": age_on(user["birth_date"], report_date),
            "cycle_phase": cycle[0]["cycle_phase"] if cycle else None,
        }

    def _rows(self, repo: str, id_log: int, first: int, last: int, simulated: bool) -> list[dict[str, Any]]:
        """Rows of a measurement table, applying the simulation overlay day by day."""
        rows = getattr(self.db, repo).rango_dias(id_log, first, last)
        if not simulated:
            return [r for r in rows if not r["is_simulated"]]
        by_day: dict[int, dict[bool, list[dict[str, Any]]]] = {}
        for row in rows:
            groups = by_day.setdefault(row["mission_day"], {True: [], False: []})
            groups[bool(row["is_simulated"])].append(row)
        chosen: list[dict[str, Any]] = []
        for groups in by_day.values():
            chosen.extend(groups[True] or groups[False])
        return chosen

    # --- Step 2: summarize -----------------------------------------------------
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
        metrics.update(summarize_glucose(self._rows("sangre", log["id"], day, day, simulated)))
        metrics["hemoglobin_g_dl"] = latest_hemoglobin(self._rows("sangre", log["id"], 1, day, simulated))

        radiation = self._rows("radiacion", log["id"], day, day, simulated)
        if radiation:
            metrics["cumulative_radiation_msv"] = radiation[-1]["cumulative_dose_msv"]
            metrics["solar_event_flag"] = radiation[-1]["solar_event_flag"]
        return metrics

    # --- Step 3: personal baselines ------------------------------------------------
    def _history(self, id_log: int, day: int, simulated: bool) -> dict[str, list[dict[str, Any]]]:
        """Previous reports: real ones for baselines, the active view for trends."""
        first = max(1, day - self.config.baseline_days)
        reports = self.db.reportes.rango_dias(id_log, first, day - 1) if day > 1 else []
        real = [r for r in reports if not r["is_simulated"]]
        if not simulated:
            return {"baseline": real, "recent": real}
        overlay: dict[int, dict[str, Any]] = {r["mission_day"]: r for r in real}
        overlay.update({r["mission_day"]: r for r in reports if r["is_simulated"]})
        return {"baseline": real, "recent": [overlay[d] for d in sorted(overlay)]}

    def _personal_baselines(
        self, m: dict[str, Any], history: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        base = history["baseline"]
        n_min = self.config.baseline_min_days

        def baseline(column: str, minimum: int = n_min) -> Baseline | None:
            return Baseline.from_values([r[column] for r in base], minimum)

        hr_b, temp_b = baseline("resting_hr_bpm"), baseline("nocturnal_skin_temp_c")
        rmssd_b, sdnn_b = baseline("hrv_rmssd_daily_ms"), baseline("hrv_sdnn_daily_ms")
        spo2_b, enmo_b, rr_b = baseline("spo2_daily_pct"), baseline("enmo_daily_mean_mg"), \
            baseline("respiratory_rate_daily_rpm")
        aucg_b = baseline("aucg_cortisol", self.config.saliva_baseline_min)
        ratio_b = baseline("cortisol_dhea_molar_ratio", self.config.saliva_baseline_min)
        saa_b = baseline("aucg_saa", self.config.saliva_baseline_min)
        slope_b = baseline("cortisol_diurnal_slope", self.config.saliva_baseline_min)

        rmssd_values = [r["hrv_rmssd_daily_ms"] for r in base if r["hrv_rmssd_daily_ms"]]
        hrv_log_z = (
            compute_hrv_log_z_score(m["hrv_rmssd_daily_ms"], rmssd_values)
            if m.get("hrv_rmssd_daily_ms") and len(rmssd_values) >= n_min else None
        )
        temp_delta = (
            m["nocturnal_skin_temp_c"] - temp_b.mean
            if temp_b and m.get("nocturnal_skin_temp_c") is not None else None
        )
        slope = m.get("cortisol_diurnal_slope")
        return {
            "hrv_baseline_28d_ms": round(rmssd_b.mean, 3) if rmssd_b else None,
            "skin_temp_baseline_28d_c": round(temp_b.mean, 3) if temp_b else None,
            "baseline_resting_hr": hr_b.mean if hr_b else None,
            "hrv_z_score": round(hrv_log_z, 4) if hrv_log_z is not None else None,
            "skin_temp_delta_c": round(temp_delta, 3) if temp_delta is not None else None,
            "resting_hr_z": hr_b.z(m.get("resting_hr_bpm")) if hr_b else None,
            "hrv_rmssd_z": rmssd_b.z(m.get("hrv_rmssd_daily_ms")) if rmssd_b else None,
            "hrv_sdnn_z": sdnn_b.z(m.get("hrv_sdnn_daily_ms")) if sdnn_b else None,
            "spo2_z": spo2_b.z(m.get("spo2_daily_pct")) if spo2_b else None,
            "enmo_z": enmo_b.z(m.get("enmo_daily_mean_mg")) if enmo_b else None,
            "rr_z": rr_b.z(m.get("respiratory_rate_daily_rpm")) if rr_b else None,
            "aucg_cortisol_z": aucg_b.z(m.get("aucg_cortisol")) if aucg_b else None,
            "cortisol_dhea_ratio_z": ratio_b.z(m.get("cortisol_dhea_molar_ratio")) if ratio_b else None,
            "saa_z": saa_b.z(m.get("aucg_saa")) if saa_b else None,
            # Flattened slope: less than half as steep as the personal baseline (team rule)
            "slope_flattened": bool(slope_b and slope is not None and slope > 0.5 * slope_b.mean),
        }

    # --- Step 4: evaluate (core/rangos.py) ---------------------------------------
    def _evaluate(self, ctx: dict[str, Any], m: dict[str, Any], s: dict[str, Any],
                  history: dict[str, list[dict[str, Any]]]) -> EvaluationResult:
        recent = history["recent"]
        sex = ctx["user"]["sex"]

        sleep_history = [r["sleep_total_min"] / 60.0 for r in recent[-2:]] + [m["sleep_total_min"] / 60.0]
        temp_history = [r["skin_temp_delta_c"] for r in recent[-2:] if r["skin_temp_delta_c"] is not None]
        if s["skin_temp_delta_c"] is not None:
            temp_history.append(s["skin_temp_delta_c"])

        car = m.get("car_absolute_nmol_l")
        blunted_weeks = 0
        if car is not None and car < 2.5:
            blunted_weeks = 1
            for report in reversed([r for r in recent if r["car_absolute_nmol_l"] is not None]):
                if report["car_absolute_nmol_l"] >= 2.5:
                    break
                blunted_weeks += 1

        # Problem C is evaluated only on chair days (weekly report P3), so the same
        # measurement does not raise a new alert every day of the week.
        rules_c = evaluate_problem_c(
            delta_mass_pct=m.get("delta_mass_pct"), bmi=m.get("bmi"),
            smi_total_kg_m2=m.get("smi_total_kg_m2"), sex=sex,
            visceral_fat_level=m.get("visceral_fat_level"),
        ) if m.get("new_body_measurement") else []
        rules_f = evaluate_problem_f(
            sleep_hours_history=sleep_history, car_absolute_nmol_l=car,
            car_blunted_consecutive_weeks=blunted_weeks,
            cortisol_morning_nmol_l=m.get("cortisol_morning_nmol_l"),
            cortisol_afternoon_nmol_l=m.get("cortisol_afternoon_nmol_l"),
            cortisol_night_nmol_l=m.get("cortisol_night_nmol_l"),
            cortisol_dhea_ratio_z=s["cortisol_dhea_ratio_z"], aucg_cortisol_z=s["aucg_cortisol_z"],
            cortisol_diurnal_slope_flattened=s["slope_flattened"], saa_z=s["saa_z"],
            # ln(RMSSD) z-score, as in problem G (Task Force 1996 recommends the log transform)
            hrv_rmssd_z=s["hrv_z_score"], hrv_sdnn_z=s["hrv_sdnn_z"],
        )
        rules_g = evaluate_problem_g(
            skin_temp_deltas_history=temp_history, resting_hr_bpm=m.get("resting_hr_bpm"),
            resting_hr_z=s["resting_hr_z"], hrv_z_score=s["hrv_z_score"],
            respiratory_rate_rpm=m.get("respiratory_rate_daily_rpm"),
            respiratory_rate_rising=bool(s["rr_z"] is not None and s["rr_z"] > 1.5),
            sex=sex, cycle_phase=ctx["cycle_phase"], compensate_luteal_in_code=True,
        )
        rules_s = evaluate_safety(
            blood_glucose_mg_dl=m.get("glucose_extreme_mg_dl"),
            systolic_bp_mmhg=m.get("resting_systolic_bp_mmhg"),
            diastolic_bp_mmhg=m.get("resting_diastolic_bp_mmhg"),
            spo2_pct=m.get("spo2_daily_pct"),
            cumulative_radiation_msv=m.get("cumulative_radiation_msv"),
            solar_event_flag=int(m.get("solar_event_flag") or 0),
        )
        return evaluate_overall_health(rules_c, rules_f, rules_g, rules_s)

    # --- Step 5: readiness score (core/puntaje.py) -------------------------------
    def _score(self, m: dict[str, Any], s: dict[str, Any]) -> float:
        cap = self.config.z_cap
        capped = {
            key: max(-cap, min(cap, s[key])) if s[key] is not None else None
            for key in ("hrv_z_score", "resting_hr_z", "aucg_cortisol_z", "cortisol_dhea_ratio_z")
        }
        breakdown = evaluate_daily_readiness(
            {**m, **s, **capped}, scale_non_z_metrics=self.config.scale_non_z_metrics,
        )
        return breakdown.readiness_score

    # --- Step 6: save the report -------------------------------------------------
    def _save_report(self, ctx: dict[str, Any], m: dict[str, Any], s: dict[str, Any],
                     result: DayResult) -> int:
        log, day = ctx["log"], ctx["day"]
        hr, hb, spo2 = m.get("resting_hr_bpm"), m.get("hemoglobin_g_dl"), m.get("spo2_daily_pct")

        liebermeister = 0
        if hr is not None and s["baseline_resting_hr"] is not None and s["skin_temp_delta_c"] is not None:
            _, liebermeister = evaluate_liebermeister_rule(
                hr, s["baseline_resting_hr"], s["skin_temp_delta_c"]
            )
        anemia = 0
        if s["resting_hr_z"] is not None:
            _, anemia = compute_anemia_suspicion_index(
                resting_hr_z=s["resting_hr_z"], spo2_drop_z=-(s["spo2_z"] or 0.0),
                enmo_fatigue_z=-(s["enmo_z"] or 0.0),
                baseline_hb_g_dl=log["baseline_hb_g_dl"], current_hb_g_dl=hb,
            )

        values = {**m, **s}
        values.update({
            "liebermeister_dissociation_flag": liebermeister,
            "anemia_suspicion_flag": anemia,
            "do2_ml_min": round(compute_oxygen_delivery_do2(hr, hb, spo2), 1) if hr and hb and spo2 else None,
            "readiness_score": result.readiness_score,
            "triage_status": result.evaluation.triage_status,
            "recommended_sampling_mode": result.evaluation.recommended_sampling_mode,
        })
        row = {column: values.get(column) for column in REPORT_COLUMNS}
        row["glim_malnutrition_risk"] = row["glim_malnutrition_risk"] or 0

        existing = self.db.reportes.del_dia(log["id"], day, simulado=result.is_simulated)
        if existing:
            self.db.reportes.actualizar(existing["id"], **row)
            return int(existing["id"])
        return self.db.reportes.insertar(
            id_mission_log=log["id"], mission_day=day, report_date=ctx["report_date"],
            is_simulated=int(result.is_simulated), **row,
        )

    # --- Step 7: sampling mode for reminders --------------------------------------
    def _update_monitoring_mode(self, ctx: dict[str, Any], result: DayResult) -> None:
        """Only the most recent day decides the active mode (read by recordatorios.py)."""
        latest_day = self.db.reportes.ultimo_dia(ctx["log"]["id"]) or 0
        if ctx["day"] >= latest_day:
            self.db.mission_logs.actualizar(
                ctx["log"]["id"], active_monitoring_mode=result.evaluation.recommended_sampling_mode
            )

    # --- Step 8: alerts ------------------------------------------------------------
    def _save_alerts(self, ctx: dict[str, Any], result: DayResult) -> list[tuple[int, AlertDraft]]:
        log, day, simulated = ctx["log"], ctx["day"], int(result.is_simulated)
        for old in self.db.alertas.listar(id_mission_log=log["id"], mission_day=day, is_simulated=simulated):
            self.db.alertas.eliminar(old["id"])                    # Rebuild the day (idempotent)

        drafts = build_alerts(result.evaluation, mission_day=day)
        recent = self.db.alertas.listar(id_mission_log=log["id"], mission_day=day - 1, is_simulated=simulated)
        drafts = filter_repeated(drafts, recent, ctx["moment"], self.config.repeat_window_hours)

        stored: list[tuple[int, AlertDraft]] = []
        for draft in drafts:
            row = draft.to_db_row(log["id"], is_simulated=bool(simulated))
            stored.append((self.db.alertas.insertar(**row, created_at=ctx["moment"]), draft))
        return stored


# =====================================================================
# 3. PUBLIC ENTRY POINTS
# =====================================================================

def _publish(results: Sequence[DayResult], db_path: Path | str, bus: EventBus) -> int:
    """Publishes the alerts of the processed days (called after the commit)."""
    pending = [(r, alert_id, draft) for r in results for alert_id, draft in r.alerts]
    if not pending:
        return 0
    with sesion(db_path) as db:
        astronauts: dict[int, dict[str, Any]] = {}
        for result, _, _ in pending:
            if result.id_mission_log not in astronauts:
                log = db.mission_logs.obtener(result.id_mission_log)
                user = db.usuarios.obtener(log["id_user"])
                astronauts[result.id_mission_log] = {
                    "id": user["id"], "full_name": user["full_name"], "email": user["email"],
                }
    delivered = 0
    for result, alert_id, draft in pending:
        delivered += bus.publish(AlertEvent(
            alert_id=alert_id, id_mission_log=result.id_mission_log,
            astronaut=astronauts[result.id_mission_log], alert=draft,
            is_simulated=result.is_simulated,
        ))
    return delivered


def process_days(
    id_mission_log: int,
    days: Sequence[int],
    *,
    simulated: bool = False,
    db_path: Path | str = DB_PATH,
    config: PipelineConfig | None = None,
    bus: EventBus = default_bus,
    on_progress: Callable[[DayResult], None] | None = None,
) -> list[DayResult]:
    """Processes several days of one astronaut in order, in a single transaction.

    Days must be processed in ascending order because each day's baseline uses the
    reports of the previous days. Alerts are published once the transaction commits.
    """
    results: list[DayResult] = []
    with sesion(db_path) as db:
        pipeline = DailyReportPipeline(db, config)
        for day in sorted(days):
            result = pipeline.process_day(id_mission_log, day, simulated=simulated)
            results.append(result)
            if on_progress:
                on_progress(result)
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
