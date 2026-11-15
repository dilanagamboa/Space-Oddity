"""Daily summaries: turns one day of raw measurements into report metrics.

Pure functions only (no database, no network): they receive rows as dictionaries,
exactly as ``services/database.py`` returns them, and delegate every formula to
``core/ecuaciones.py``. ``services/reportes.py`` orchestrates the I/O around them.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import fmean, stdev
from typing import Any

from core.ecuaciones import (
    compute_aucg,
    compute_bmi,
    compute_bmr_mifflin,
    compute_car,
    compute_cortisol_dhea_molar_ratio,
    compute_cortisol_diurnal_slope,
    compute_delta_mass_pct,
    compute_glim_malnutrition_risk,
    compute_glucose_cv_pct,
    compute_glucose_tir_pct,
    compute_gmi_pct,
    compute_smi,
    compute_sns_hpa_asymmetry_index,
)

Row = Mapping[str, Any]
BLOCK_MINUTES: int = 15


# =====================================================================
# 1. HELPERS
# =====================================================================

def _hour(row: Row, column: str = "recorded_at") -> int:
    return int(str(row[column])[11:13])


def _moment(row: Row, column: str = "recorded_at") -> datetime:
    return datetime.fromisoformat(str(row[column]))


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def age_on(birth_date: date | str, on: date) -> int:
    """Age in whole years on a given date."""
    born = date.fromisoformat(birth_date) if isinstance(birth_date, str) else birth_date
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


@dataclass(frozen=True)
class Baseline:
    """Personal baseline of one metric (mean and sample standard deviation)."""

    mean: float
    std: float
    n: int

    @classmethod
    def from_values(cls, values: Sequence[float | None], min_n: int = 3) -> Baseline | None:
        """Builds a baseline ignoring missing values. ``None`` if there are fewer than ``min_n``."""
        clean = [float(v) for v in values if v is not None]
        if len(clean) < max(min_n, 2):
            return None
        return cls(mean=fmean(clean), std=stdev(clean), n=len(clean))

    def z(self, value: float | None) -> float | None:
        """Z-score of a value against this baseline (``None`` if not computable)."""
        if value is None or self.std <= 0:
            return None
        return (value - self.mean) / self.std


# =====================================================================
# 2. WATCH (cardiovascular_autonomic, every 15 min)
# =====================================================================

@dataclass(frozen=True)
class SleepWindow:
    """Scheduled sleep window in UTC hours (22:00-06:00 by default)."""

    start_hour: int = 22
    end_hour: int = 6

    def contains(self, hour: int) -> bool:
        if self.start_hour > self.end_hour:          # Crosses midnight
            return hour >= self.start_hour or hour < self.end_hour
        return self.start_hour <= hour < self.end_hour


DEFAULT_SLEEP_WINDOW = SleepWindow()


def summarize_watch(
    rows: Sequence[Row], window: SleepWindow = DEFAULT_SLEEP_WINDOW
) -> dict[str, Any] | None:
    """Sleep architecture, nocturnal vitals and activity minutes for one day.

    Resting values (HR, HRV, skin temperature, respiratory rate) are taken while
    asleep, as the team document defines them ("FC nocturna", "temperatura de
    piel nocturna"). Returns ``None`` if there are no rows.
    """
    if not rows:
        return None

    in_window = [r for r in rows if window.contains(_hour(r))]
    asleep = [r for r in rows if r["sleep_stage"] > 0]
    awake = [r for r in rows if r["sleep_stage"] == 0 and not window.contains(_hour(r))]
    resting_awake = [r for r in awake if r["activity_intensity_level"] == 0]

    def minutes(stage: int) -> int:
        return BLOCK_MINUTES * sum(1 for r in rows if r["sleep_stage"] == stage and window.contains(_hour(r)))

    sleep_total = BLOCK_MINUTES * len(asleep)
    window_minutes = BLOCK_MINUTES * len(in_window)
    nightly = asleep or in_window                     # Fallback for a night without sleep

    def avg(source: Sequence[Row], column: str) -> float | None:
        values = [float(r[column]) for r in source if r.get(column) is not None]
        return round(fmean(values), 3) if values else None

    return {
        "sleep_total_min": sleep_total,
        "sleep_awake_min": minutes(0),
        "sleep_light_min": minutes(1),
        "sleep_deep_min": minutes(2),
        "sleep_rem_min": minutes(3),
        "sleep_efficiency_pct": round(100.0 * sleep_total / window_minutes, 2) if window_minutes else 0.0,
        "resting_hr_bpm": avg(nightly, "heart_rate_bpm"),
        "hrv_rmssd_daily_ms": avg(nightly, "hrv_rmssd_ms"),
        "hrv_sdnn_daily_ms": avg(nightly, "hrv_sdnn_ms"),
        "nocturnal_skin_temp_c": avg(nightly, "skin_temperature_c"),
        "respiratory_rate_daily_rpm": avg(nightly, "respiratory_rate_rpm"),
        "spo2_daily_pct": avg(rows, "spo2_pct"),
        "enmo_daily_mean_mg": avg(rows, "enmo_mg"),
        "sedentary_min": BLOCK_MINUTES * sum(1 for r in awake if r["activity_intensity_level"] == 0),
        "light_activity_min": BLOCK_MINUTES * sum(1 for r in awake if r["activity_intensity_level"] == 1),
        "mvpa_min": BLOCK_MINUTES * sum(1 for r in awake if r["activity_intensity_level"] >= 2),
        # Not stored in the report, used for the safety evaluation (S2)
        "resting_systolic_bp_mmhg": avg(resting_awake or awake, "estimated_systolic_bp_mmhg"),
        "resting_diastolic_bp_mmhg": avg(resting_awake or awake, "estimated_diastolic_bp_mmhg"),
    }


# =====================================================================
# 3. BODY COMPOSITION CHAIR
# =====================================================================

def summarize_body(
    body: Row | None,
    *,
    start_weight_kg: float,
    height_cm: float,
    sex: int,
    age_years: int,
    mission_day: int,
) -> dict[str, Any]:
    """BMI, mass loss, SMI, Mifflin BMR and GLIM stage from the latest chair reading."""
    if body is None:
        return {}
    weight = float(body["weight_kg"])
    delta_mass = compute_delta_mass_pct(start_weight_kg, weight)
    bmi = compute_bmi(weight, height_cm)
    return {
        "bmi": round(bmi, 2),
        "delta_mass_pct": round(delta_mass, 3),
        "smi_total_kg_m2": round(compute_smi(weight, float(body["muscle_pct"]), height_cm), 3),
        "bmr_mifflin_kcal": round(compute_bmr_mifflin(weight, height_cm, age_years, sex), 1),
        "glim_malnutrition_risk": compute_glim_malnutrition_risk(
            delta_mass, bmi, mission_months=mission_day / 30.0
        ),
        "visceral_fat_level": body.get("visceral_fat_level"),
    }


# =====================================================================
# 4. SALIVA (HPA axis)
# =====================================================================

SALIVA_SLOTS: tuple[str, ...] = ("wake_0m", "wake_30m", "afternoon", "bedtime")


def summarize_saliva(rows: Sequence[Row]) -> dict[str, Any]:
    """CAR, diurnal slope, AUCg (cortisol and sAA), C/DHEA ratio and SNS/HPA index.

    Requires the 4 samples of the saliva day. Returns ``{}`` otherwise, so the
    report keeps those columns as NULL ("nullable on days without saliva").
    """
    by_slot = {r["collection_slot"]: r for r in rows}
    if not all(slot in by_slot for slot in SALIVA_SLOTS):
        return {}

    ordered = [by_slot[s] for s in SALIVA_SLOTS]
    t0 = _moment(ordered[0])
    hours = [(_moment(r) - t0).total_seconds() / 3600.0 for r in ordered]
    cortisol = [float(r["salivary_cortisol_nmol_l"]) for r in ordered]
    saa = [float(r["salivary_alpha_amylase_u_ml"]) for r in ordered]

    car_abs, car_pct = compute_car(cortisol[0], cortisol[1])
    peak_index = 0 if cortisol[0] >= cortisol[1] else 1
    aucg_cortisol = compute_aucg(cortisol, hours)
    aucg_saa = compute_aucg(saa, hours)
    ratios = [
        compute_cortisol_dhea_molar_ratio(float(r["salivary_cortisol_nmol_l"]),
                                          dhea_nmol_l=float(r["salivary_dhea_nmol_l"]))
        for r in ordered
    ]
    return {
        "car_absolute_nmol_l": round(car_abs, 3),
        "car_pct": round(car_pct, 2),
        "cortisol_diurnal_slope": round(
            compute_cortisol_diurnal_slope(cortisol[peak_index], cortisol[3], hours[3] - hours[peak_index]), 4
        ),
        "aucg_cortisol": round(aucg_cortisol, 3),
        "aucg_saa": round(aucg_saa, 3),
        "sns_hpa_asymmetry_idx": round(compute_sns_hpa_asymmetry_index(aucg_saa, aucg_cortisol), 4),
        "cortisol_dhea_molar_ratio": round(fmean(ratios), 4),
        # Not stored, used by rangos.evaluate_problem_f
        "cortisol_morning_nmol_l": cortisol[1],
        "cortisol_afternoon_nmol_l": cortisol[2],
        "cortisol_night_nmol_l": cortisol[3],
    }


# =====================================================================
# 5. GLUCOSE
# =====================================================================

def summarize_glucose(rows: Sequence[Row]) -> dict[str, Any]:
    """Glycemic CV, TIR and GMI, plus the most extreme reading of the day.

    The extreme reading is the lowest one if any is below 70 mg/dL (hypoglycemia
    is the more urgent risk), otherwise the highest one.
    """
    values = [float(r["blood_glucose_mg_dl"]) for r in rows if r.get("blood_glucose_mg_dl") is not None]
    if not values:
        return {}
    extreme = min(values) if min(values) < 70.0 else max(values)
    summary: dict[str, Any] = {"glucose_extreme_mg_dl": extreme}
    if len(values) >= 2:
        summary.update({
            "glucose_cv_pct": round(compute_glucose_cv_pct(values), 3),
            "glucose_tir_pct": round(compute_glucose_tir_pct(values), 2),
            "gmi_pct": round(compute_gmi_pct(fmean(values)), 3),
        })
    return summary


def latest_hemoglobin(rows: Sequence[Row]) -> float | None:
    """Most recent hemoglobin value among the given blood rows."""
    with_hb = [r for r in rows if r.get("hemoglobin_g_dl") is not None]
    if not with_hb:
        return None
    return float(max(with_hb, key=lambda r: str(r["recorded_at"]))["hemoglobin_g_dl"])
