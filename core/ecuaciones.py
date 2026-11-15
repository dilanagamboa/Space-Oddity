"""Clinical and physiological mathematical models for astro-health.

Implements the 17 equations (15 peer-reviewed + 2 custom team models) across:
- Baseline statistics (Z-score, log-transformed HRV Z-score).
- Problem C: Body mass loss, BMI, SMI, ASMI, Mifflin-St Jeor BMR, GLIM risk.
- Problem F: Cortisol Awakening Response (CAR), diurnal slope, trapezoidal AUCg,
  Cortisol/DHEA molar ratio, SNS/HPA asymmetry index.
- Problem G: Nocturnal skin temperature delta, RMSSD, SDNN, Liebermeister's rule.
- Glycemic & Cardiovascular: Glucose CV, TIR, GMI, Max HR (Tanaka), ENMO,
  Oxygen Delivery (DO2), Anemia Suspicion Index, and Daily Readiness Score.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from statistics import fmean, stdev

# Molecular weight of DHEA = 288.42 g/mol -> 1 pg/mL = 0.003467 nmol/L
DHEA_PG_ML_TO_NMOL_L: float = 0.003467

# Default weights for the Daily Operational Readiness Score (Team Custom Model)
DEFAULT_READINESS_WEIGHTS: dict[str, float] = {
    "sleep_deficit": 8.0,
    "hrv_drop": 7.0,
    "resting_hr_elevation": 6.0,
    "skin_temp_deviation": 7.0,
    "cortisol_imbalance": 5.0,
    "muscle_loss": 5.0,
    "glycemic_instability": 4.0,
}


# =====================================================================
# 1. BASELINE & STATISTICAL EQUATIONS
# =====================================================================

def compute_z_score(value: float, mean: float, std: float) -> float:
    """Personal baseline deviation Z-score: z = (x - mu) / sigma.

    Source: Mishra T, et al. Nat Biomed Eng. 2020;4:1208-1220.
    """
    return 0.0 if std <= 0.0 else (value - mean) / std


def compute_hrv_log_z_score(
    rmssd_today_ms: float,
    baseline_rmssd_series_ms: Sequence[float],
) -> float:
    """Log-transformed HRV Z-score: Z_HRV = (ln(RMSSD_today) - mu_ln) / sigma_ln.

    Source: Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    valid = [math.log(v) for v in baseline_rmssd_series_ms if v > 0.0]
    if rmssd_today_ms <= 0.0 or len(valid) < 2:
        return 0.0
    return compute_z_score(math.log(rmssd_today_ms), fmean(valid), stdev(valid))


# =====================================================================
# 2. PROBLEM C: MUSCLE ATROPHY, MALNUTRITION & VISCERAL FAT
# =====================================================================

def compute_delta_mass_pct(initial_weight_kg: float, current_weight_kg: float) -> float:
    """Body mass loss percentage: ((M0 - M_current) / M0) * 100 (positive = weight loss).

    Source: Cederholm T, et al. GLIM criteria. Clin Nutr. 2019;38(1):1-9.
    """
    if initial_weight_kg <= 0.0:
        raise ValueError("initial_weight_kg must be greater than 0.")
    return ((initial_weight_kg - current_weight_kg) / initial_weight_kg) * 100.0


def compute_bmi(weight_kg: float, height_cm: float) -> float:
    """Body Mass Index: BMI = weight (kg) / height (m)^2.

    Source: Cederholm T, et al. Clin Nutr. 2019;38(1):1-9.
    """
    if height_cm <= 0.0 or weight_kg < 0.0:
        raise ValueError("height_cm must be > 0 and weight_kg must be >= 0.")
    return weight_kg / ((height_cm / 100.0) ** 2)


def compute_skeletal_muscle_mass_kg(weight_kg: float, muscle_pct: float) -> float:
    """Total Skeletal Muscle Mass (SMM) in kg: M * (%muscle / 100).

    Source: Janssen I, et al. Am J Epidemiol. 2004;159(4):413-421.
    """
    return weight_kg * (muscle_pct / 100.0)


def compute_asmi(appendicular_muscle_kg: float, height_cm: float) -> float:
    """Appendicular Skeletal Muscle Index: ASMI = ASM (kg) / height (m)^2.

    Source: Cruz-Jentoft AJ, et al. EWGSOP2. Age Ageing. 2019;48(1):16-31.
    """
    if height_cm <= 0.0:
        raise ValueError("height_cm must be greater than 0.")
    return appendicular_muscle_kg / ((height_cm / 100.0) ** 2)


def compute_smi(weight_kg: float, muscle_pct: float, height_cm: float) -> float:
    """Total Skeletal Muscle Index: SMI = SMM (kg) / height (m)^2.

    Source: Janssen I, et al. Am J Epidemiol. 2004;159(4):413-421.
    """
    return compute_asmi(compute_skeletal_muscle_mass_kg(weight_kg, muscle_pct), height_cm)


def compute_bmr_mifflin(
    weight_kg: float,
    height_cm: float,
    age_years: int | float,
    sex: int,
) -> float:
    """Resting Energy Expenditure (kcal/day): 10*W + 6.25*H - 5*A + (5 if male else -161).

    Source: Mifflin MD, et al. Am J Clin Nutr. 1990;51(2):241-247.
    """
    return (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age_years) + (5.0 if sex == 1 else -161.0)


def compute_glim_malnutrition_risk(
    delta_mass_pct: float,
    bmi: float,
    mission_months: float = 6.0,
) -> int:
    """GLIM malnutrition stage (0 = None, 1 = Moderate, 2 = Severe).

    Source: Cederholm T, et al. Clin Nutr. 2019;38(1):1-9.
    """
    mod_min, sev_min = (5.0, 10.0) if mission_months <= 6.0 else (10.0, 20.0)
    if delta_mass_pct > sev_min or bmi < 18.5:
        return 2
    if delta_mass_pct >= mod_min or bmi < 20.0:
        return 1
    return 0


# =====================================================================
# 3. PROBLEM F: CIRCADIAN RHYTHM, SLEEP & HPA AXIS
# =====================================================================

def compute_car(
    cortisol_wake_0m_nmol_l: float,
    cortisol_wake_30m_nmol_l: float,
) -> tuple[float, float]:
    """Cortisol Awakening Response: (delta_CAR in nmol/L, CAR_pct in %).

    Source: Wüst S, et al. Noise Health. 2000;2(7):79-88.
    """
    delta = cortisol_wake_30m_nmol_l - cortisol_wake_0m_nmol_l
    pct = 0.0 if cortisol_wake_0m_nmol_l <= 0.0 else (delta / cortisol_wake_0m_nmol_l) * 100.0
    return delta, pct


def compute_cortisol_diurnal_slope(
    cortisol_morning_peak_nmol_l: float,
    cortisol_bedtime_nmol_l: float,
    delta_hours: float,
) -> float:
    """Diurnal Cortisol Slope: (C_night - C_morning_peak) / delta_t_hours.

    Source: Medicina 2021;57(10):1079.
    """
    if delta_hours <= 0.0:
        raise ValueError("delta_hours must be greater than 0.")
    return (cortisol_bedtime_nmol_l - cortisol_morning_peak_nmol_l) / delta_hours


def compute_aucg(
    measurements: Sequence[float],
    time_points_hours: Sequence[float],
) -> float:
    """Area Under the Curve with respect to ground (AUCg) via trapezoidal rule.

    Source: Pruessner JC, et al. Psychoneuroendocrinology. 2003;28(7):916-931.
    """
    if len(measurements) != len(time_points_hours) or len(measurements) < 2:
        raise ValueError("measurements and time_points_hours must have same length >= 2.")
    deltas = [time_points_hours[i + 1] - time_points_hours[i] for i in range(len(measurements) - 1)]
    if any(dt < 0.0 for dt in deltas):
        raise ValueError("time_points_hours must be in strictly ascending order.")
    return sum(((measurements[i] + measurements[i + 1]) / 2.0) * dt for i, dt in enumerate(deltas))


def convert_dhea_pg_ml_to_nmol_l(dhea_pg_ml: float) -> float:
    """Converts salivary DHEA from pg/mL to nmol/L (MW = 288.42 g/mol)."""
    return dhea_pg_ml * DHEA_PG_ML_TO_NMOL_L


def compute_cortisol_dhea_molar_ratio(
    cortisol_nmol_l: float,
    dhea_nmol_l: float | None = None,
    dhea_pg_ml: float | None = None,
) -> float:
    """Catabolic/Anabolic Molar Ratio: [Cortisol (nmol/L)] / [DHEA (nmol/L)].

    Source: Mehta SK, et al. npj Microgravity. 2017;3:11.
    """
    if dhea_nmol_l is None:
        if dhea_pg_ml is None:
            raise ValueError("Either dhea_nmol_l or dhea_pg_ml must be provided.")
        dhea_nmol_l = convert_dhea_pg_ml_to_nmol_l(dhea_pg_ml)
    return 0.0 if dhea_nmol_l <= 0.0 else cortisol_nmol_l / dhea_nmol_l


def compute_sns_hpa_asymmetry_index(aucg_saa: float, aucg_cortisol: float) -> float:
    """SNS/HPA Asymmetry Index (AOCg): AUCg(sAA) / AUCg(Cortisol).

    Source: Ali N, Pruessner JC. Psychoneuroendocrinology. 2012;37(1):126-135.
    """
    return 0.0 if aucg_cortisol <= 0.0 else aucg_saa / aucg_cortisol


# =====================================================================
# 4. PROBLEM G: EARLY INFECTION, AUTONOMIC OVERLOAD & HRV
# =====================================================================

def compute_rmssd(rr_intervals_ms: Sequence[float]) -> float:
    """Root Mean Square of Successive Differences (RMSSD) of RR intervals (ms).

    Source: Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    if len(rr_intervals_ms) < 2:
        return 0.0
    diffs_sq = [(rr_intervals_ms[i + 1] - rr_intervals_ms[i]) ** 2 for i in range(len(rr_intervals_ms) - 1)]
    return math.sqrt(fmean(diffs_sq))


def compute_sdnn(rr_intervals_ms: Sequence[float]) -> float:
    """Standard Deviation of NN (RR) intervals (SDNN) in ms.

    Source: Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    return 0.0 if len(rr_intervals_ms) < 2 else stdev(rr_intervals_ms)


def compute_skin_temp_delta(nocturnal_skin_temp_c: float, baseline_28d_temp_c: float) -> float:
    """Nocturnal skin temperature deviation from 28-day personal baseline (°C).

    Source: Oura Body Temperature deviation from personal baseline.
    """
    return nocturnal_skin_temp_c - baseline_28d_temp_c


def evaluate_liebermeister_rule(
    resting_hr_bpm: float,
    baseline_hr_bpm: float,
    temp_delta_c: float,
    tolerance_bpm: float = 5.0,
) -> tuple[float, int]:
    """Expected HR under fever (+8.5 bpm per +1 °C) and autonomic dissociation flag (0|1).

    Source: Karjalainen J, Viitasalo M. Arch Intern Med. 1986;146(6):1169-1171.
    """
    expected_hr = baseline_hr_bpm + (8.5 * max(temp_delta_c, 0.0))
    return expected_hr, int((resting_hr_bpm - expected_hr) > tolerance_bpm)


# =====================================================================
# 5. GLYCEMIC, CARDIOVASCULAR, RADIATION & TEAM MODELS
# =====================================================================

def compute_glucose_cv_pct(glucose_series_mg_dl: Sequence[float]) -> float:
    """Glycemic Coefficient of Variation: CV% = (sigma / mu) * 100 (target <= 36%).

    Source: Battelino T, et al. Diabetes Care. 2019;42(8):1593-1603.
    """
    if len(glucose_series_mg_dl) < 2 or (mean_g := fmean(glucose_series_mg_dl)) <= 0.0:
        return 0.0
    return (stdev(glucose_series_mg_dl) / mean_g) * 100.0


def compute_glucose_tir_pct(
    glucose_series_mg_dl: Sequence[float],
    low_mg_dl: float = 70.0,
    high_mg_dl: float = 180.0,
) -> float:
    """Time in Range (TIR %) for glucose readings within [70, 180] mg/dL.

    Source: Battelino T, et al. Diabetes Care. 2019;42(8):1593-1603.
    """
    if not glucose_series_mg_dl:
        return 0.0
    return (sum(low_mg_dl <= g <= high_mg_dl for g in glucose_series_mg_dl) / len(glucose_series_mg_dl)) * 100.0


def compute_gmi_pct(mean_glucose_mg_dl: float) -> float:
    """Glucose Management Indicator (GMI %): 3.31 + 0.02392 * mean_glucose.

    Source: Bergenstal RM, et al. Diabetes Care. 2018;41(11):2275-2280.
    """
    return 3.31 + (0.02392 * mean_glucose_mg_dl)


def compute_max_hr_tanaka(age_years: int | float) -> float:
    """Age-predicted Maximal Heart Rate: HR_max = 208 - (0.7 * age).

    Source: Tanaka H, Monahan KD, Seals DR. J Am Coll Cardiol. 2001;37(1):153-156.
    """
    return 208.0 - (0.7 * age_years)


def compute_enmo_mg(ax_g: float, ay_g: float, az_g: float) -> float:
    """Euclidean Norm Minus One (ENMO) in milli-g (mg): max(sqrt(x^2+y^2+z^2) - 1, 0) * 1000.

    Source: van Hees VT, et al. PLoS ONE. 2013;8(4):e61691.
    """
    return max(math.hypot(ax_g, ay_g, az_g) - 1.0, 0.0) * 1000.0


def compute_oxygen_delivery_do2(
    heart_rate_bpm: float,
    hemoglobin_g_dl: float,
    spo2_pct: float,
    stroke_volume_ml: float = 70.0,
) -> float:
    """Oxygen Delivery (DO2 in mL O2/min): CO (L/min) * CaO2 (mL/dL) * 10.

    Source: Tomić Mahečić T, et al. J Clin Med. 2022;11(20):6154.
    """
    cardiac_output_l_min = (heart_rate_bpm * stroke_volume_ml) / 1000.0
    cao2_ml_dl = 1.34 * hemoglobin_g_dl * (spo2_pct / 100.0)
    return cardiac_output_l_min * cao2_ml_dl * 10.0


def compute_cumulative_radiation_msv(daily_doses_msv: Sequence[float]) -> float:
    """Cumulative mission radiation dose: D_cum = sum(daily_dose_msv).

    Source: Zhang S, et al. Sci Adv. 2020; Hassler DM, et al. Science. 2014.
    """
    return float(sum(daily_doses_msv))


def compute_anemia_suspicion_index(
    resting_hr_z: float,
    spo2_drop_z: float,
    enmo_fatigue_z: float,
    baseline_hb_g_dl: float | None = None,
    current_hb_g_dl: float | None = None,
    threshold: float = 1.5,
) -> tuple[float, int]:
    """Custom team Anemia Suspicion Index (IA = sum(w_i * z_i)) and binary flag (0|1).

    Source: Team Custom Design (based on Tomić Mahečić 2022 & space anemia physiology).
    """
    score = 0.5 * max(resting_hr_z, 0.0) + 0.3 * max(spo2_drop_z, 0.0) + 0.2 * max(enmo_fatigue_z, 0.0)
    hb_drop = baseline_hb_g_dl is not None and current_hb_g_dl is not None and (baseline_hb_g_dl - current_hb_g_dl) >= 1.5
    return round(score, 3), int(score >= threshold or hb_drop)


def compute_readiness_score(
    z_penalties: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> float:
    """Daily Operational Readiness Score (0-100): P = 100 - sum(w_i * max(z_i, 0)).

    Source: Team Custom Design (Space Apps 2026).
    """
    active_weights = weights if weights is not None else DEFAULT_READINESS_WEIGHTS
    penalty = sum(active_weights.get(k, 5.0) * max(v, 0.0) for k, v in z_penalties.items())
    return round(max(0.0, min(100.0, 100.0 - penalty)), 1)
