"""Daily clinical evaluation: personal baselines, rule evaluation, score and flags.

Pure functions (no database). ``services/reportes.py`` loads the data, calls these
functions in order and stores the result::

    personal_signals()  ->  z-scores against the astronaut's own baseline
    evaluate_day()      ->  core/rangos.py rules (C, F, G, S) and triage
    readiness()         ->  core/puntaje.py score (0-100)
    derived_flags()     ->  Liebermeister, anemia suspicion and DO2

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.ecuaciones import (
    compute_anemia_suspicion_index,
    compute_hrv_log_z_score,
    compute_oxygen_delivery_do2,
    compute_skin_temp_delta,
    evaluate_liebermeister_rule,
)
from core.puntaje import evaluate_daily_readiness
from core.rangos import (
    THRESHOLDS,
    EvaluationResult,
    evaluate_overall_health,
    evaluate_problem_c,
    evaluate_problem_f,
    evaluate_problem_g,
    evaluate_safety,
)
from core.resumen_diario import Baseline

Report = Mapping[str, Any]

# z-score -> (report column it is computed from, uses the weekly-saliva minimum)
Z_SCORES: dict[str, tuple[str, bool]] = {
    "resting_hr_z": ("resting_hr_bpm", False),
    "hrv_sdnn_z": ("hrv_sdnn_daily_ms", False),
    "spo2_z": ("spo2_daily_pct", False),
    "enmo_z": ("enmo_daily_mean_mg", False),
    "rr_z": ("respiratory_rate_daily_rpm", False),
    "aucg_cortisol_z": ("aucg_cortisol", True),
    "cortisol_dhea_ratio_z": ("cortisol_dhea_molar_ratio", True),
    "saa_z": ("aucg_saa", True),
}

# z-scores that feed the readiness score (clipped to ±z_cap first)
SCORED_Z: tuple[str, ...] = ("hrv_z_score", "resting_hr_z", "aucg_cortisol_z", "cortisol_dhea_ratio_z")

RISING_RR_Z: float = 1.5            # "En alza vs. base" for respiratory rate (team rule)
FLATTENED_SLOPE_RATIO: float = 0.5  # Slope less than half as steep as baseline (team rule)


@dataclass(frozen=True)
class ClinicalSettings:
    """Clinical parameters of the evaluation.

    Attributes:
        baseline_min_days: Days needed before z-scores are evaluated. 14 follows the
            team document ("línea base personal de las primeras 2 semanas").
        saliva_baseline_min: Minimum previous saliva days (saliva is weekly).
        z_cap: z-scores are clipped to ``±z_cap`` before the readiness score, so one
            extreme metric cannot erase the whole score by itself.
        scale_non_z_metrics: Passed to ``core.puntaje``. ``True`` puts temperature,
            mass loss and glucose CV on the same scale as z-scores.
    """

    baseline_min_days: int = 14
    saliva_baseline_min: int = 3
    z_cap: float = 4.0
    scale_non_z_metrics: bool = True


DEFAULT_SETTINGS = ClinicalSettings()


def _rounded(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


# =====================================================================
# 1. PERSONAL BASELINES
# =====================================================================

def personal_signals(
    metrics: Mapping[str, Any],
    baseline_reports: Sequence[Report],
    settings: ClinicalSettings = DEFAULT_SETTINGS,
) -> dict[str, Any]:
    """z-scores and deltas of today against the astronaut's previous real reports."""

    def baseline(column: str, saliva: bool = False) -> Baseline | None:
        minimum = settings.saliva_baseline_min if saliva else settings.baseline_min_days
        return Baseline.from_values([r[column] for r in baseline_reports], minimum)

    def mean(column: str, saliva: bool = False) -> float | None:
        b = baseline(column, saliva)
        return b.mean if b else None

    signals: dict[str, Any] = {}
    for name, (column, saliva) in Z_SCORES.items():
        b = baseline(column, saliva)
        signals[name] = b.z(metrics.get(column)) if b else None

    rmssd_values = [r["hrv_rmssd_daily_ms"] for r in baseline_reports if r["hrv_rmssd_daily_ms"]]
    today_rmssd = metrics.get("hrv_rmssd_daily_ms")
    hrv_log_z = (
        compute_hrv_log_z_score(today_rmssd, rmssd_values)
        if today_rmssd and len(rmssd_values) >= settings.baseline_min_days else None
    )
    temp_mean, today_temp = mean("nocturnal_skin_temp_c"), metrics.get("nocturnal_skin_temp_c")
    temp_delta = (
        compute_skin_temp_delta(today_temp, temp_mean)
        if temp_mean is not None and today_temp is not None else None
    )
    slope_mean, slope = mean("cortisol_diurnal_slope", saliva=True), metrics.get("cortisol_diurnal_slope")
    signals.update({
        "hrv_baseline_28d_ms": _rounded(mean("hrv_rmssd_daily_ms"), 3),
        "skin_temp_baseline_28d_c": _rounded(temp_mean, 3),
        "baseline_resting_hr": mean("resting_hr_bpm"),
        "hrv_z_score": _rounded(hrv_log_z, 4),
        "skin_temp_delta_c": _rounded(temp_delta, 3),
        "slope_flattened": bool(
            slope_mean and slope is not None and slope > FLATTENED_SLOPE_RATIO * slope_mean
        ),
    })
    return signals


# =====================================================================
# 2. RULE EVALUATION (core/rangos.py)
# =====================================================================

def _blunted_car_weeks(car: float | None, recent: Sequence[Report]) -> int:
    """Consecutive saliva days (including today) with a blunted awakening response."""
    limit = THRESHOLDS["car_delta_nmol_l"]["yellow"]
    if car is None or car >= limit:
        return 0
    weeks = 1
    for report in reversed([r for r in recent if r["car_absolute_nmol_l"] is not None]):
        if report["car_absolute_nmol_l"] >= limit:
            break
        weeks += 1
    return weeks


def evaluate_day(
    metrics: Mapping[str, Any],
    signals: Mapping[str, Any],
    recent_reports: Sequence[Report],
    *,
    sex: int,
    cycle_phase: str | None,
) -> EvaluationResult:
    """Runs problems C, F, G and S for one day and consolidates the triage."""
    m, s = metrics, signals
    sleep_history = [r["sleep_total_min"] / 60.0 for r in recent_reports[-2:]] + [m["sleep_total_min"] / 60.0]
    temp_history = [r["skin_temp_delta_c"] for r in recent_reports[-2:] if r["skin_temp_delta_c"] is not None]
    if s["skin_temp_delta_c"] is not None:
        temp_history.append(s["skin_temp_delta_c"])
    car = m.get("car_absolute_nmol_l")

    # Problem C only on chair days (weekly report P3): the same measurement must not
    # raise a new alert every day of the week.
    rules_c = evaluate_problem_c(
        delta_mass_pct=m.get("delta_mass_pct"), bmi=m.get("bmi"),
        smi_total_kg_m2=m.get("smi_total_kg_m2"), sex=sex,
        visceral_fat_level=m.get("visceral_fat_level"),
    ) if m.get("new_body_measurement") else []
    rules_f = evaluate_problem_f(
        sleep_hours_history=sleep_history, car_absolute_nmol_l=car,
        car_blunted_consecutive_weeks=_blunted_car_weeks(car, recent_reports),
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
        respiratory_rate_rising=bool(s["rr_z"] is not None and s["rr_z"] > RISING_RR_Z),
        sex=sex, cycle_phase=cycle_phase, compensate_luteal_in_code=True,
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


# =====================================================================
# 3. READINESS SCORE AND DERIVED FLAGS
# =====================================================================

def readiness(
    metrics: Mapping[str, Any],
    signals: Mapping[str, Any],
    settings: ClinicalSettings = DEFAULT_SETTINGS,
) -> float:
    """Readiness score (0-100) from ``core.puntaje`` with z-scores clipped to ``±z_cap``."""
    cap = settings.z_cap
    capped = {k: max(-cap, min(cap, signals[k])) if signals[k] is not None else None for k in SCORED_Z}
    breakdown = evaluate_daily_readiness(
        {**metrics, **signals, **capped}, scale_non_z_metrics=settings.scale_non_z_metrics,
    )
    return breakdown.readiness_score


def derived_flags(
    metrics: Mapping[str, Any],
    signals: Mapping[str, Any],
    baseline_hb_g_dl: float | None,
) -> dict[str, Any]:
    """Liebermeister dissociation, anemia suspicion and oxygen delivery (DO2)."""
    hr, hb = metrics.get("resting_hr_bpm"), metrics.get("hemoglobin_g_dl")
    spo2 = metrics.get("spo2_daily_pct")
    base_hr, temp_delta = signals["baseline_resting_hr"], signals["skin_temp_delta_c"]
    liebermeister = 0
    if hr is not None and base_hr is not None and temp_delta is not None:
        _, liebermeister = evaluate_liebermeister_rule(hr, base_hr, temp_delta)
    anemia = 0
    if signals["resting_hr_z"] is not None:
        _, anemia = compute_anemia_suspicion_index(
            resting_hr_z=signals["resting_hr_z"], spo2_drop_z=-(signals["spo2_z"] or 0.0),
            enmo_fatigue_z=-(signals["enmo_z"] or 0.0),
            baseline_hb_g_dl=baseline_hb_g_dl, current_hb_g_dl=hb,
        )
    return {
        "liebermeister_dissociation_flag": liebermeister,
        "anemia_suspicion_flag": anemia,
        "do2_ml_min": round(compute_oxygen_delivery_do2(hr, hb, spo2), 1) if hr and hb and spo2 else None,
    }
