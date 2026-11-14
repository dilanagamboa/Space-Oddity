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

import math
from typing import Sequence

# Molecular weight of DHEA = 288.42 g/mol -> 1 pg/mL = 0.003467 nmol/L
DHEA_PG_ML_TO_NMOL_L: float = 0.003467


# =====================================================================
# 1. BASELINE & STATISTICAL EQUATIONS
# =====================================================================

def compute_z_score(value: float, mean: float, std: float) -> float:
    """Computes personal baseline deviation Z-score: z = (x - mu) / sigma.

    Source:
        Mishra T, et al. Pre-symptomatic detection of COVID-19 from smartwatch data.
        Nat Biomed Eng. 2020;4:1208-1220.
    """
    if std <= 0.0:
        return 0.0
    return (value - mean) / std


def compute_hrv_log_z_score(
    rmssd_today_ms: float,
    baseline_rmssd_series_ms: Sequence[float],
) -> float:
    """Computes log-transformed HRV Z-score: Z_HRV = (ln(RMSSD_today) - mu_ln) / sigma_ln.

    Source:
        Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    valid_baseline = [val for val in baseline_rmssd_series_ms if val > 0.0]
    if rmssd_today_ms <= 0.0 or len(valid_baseline) < 2:
        return 0.0

    log_baseline = [math.log(val) for val in valid_baseline]
    mu_ln = sum(log_baseline) / len(log_baseline)
    variance_ln = sum((x - mu_ln) ** 2 for x in log_baseline) / (len(log_baseline) - 1)
    sigma_ln = math.sqrt(variance_ln)

    if sigma_ln <= 0.0:
        return 0.0
    return (math.log(rmssd_today_ms) - mu_ln) / sigma_ln


# =====================================================================
# 2. PROBLEM C: MUSCLE ATROPHY, MALNUTRITION & VISCERAL FAT
# =====================================================================

def compute_delta_mass_pct(initial_weight_kg: float, current_weight_kg: float) -> float:
    """Computes body mass loss percentage: ((M0 - M_current) / M0) * 100.

    Note: A positive value indicates weight loss (e.g., +6.0 means 6% weight lost).

    Source:
        Cederholm T, et al. GLIM criteria for the diagnosis of malnutrition.
        Clin Nutr. 2019;38(1):1-9.
    """
    if initial_weight_kg <= 0.0:
        raise ValueError("initial_weight_kg must be greater than 0.")
    return ((initial_weight_kg - current_weight_kg) / initial_weight_kg) * 100.0


def compute_bmi(weight_kg: float, height_cm: float) -> float:
    """Computes Body Mass Index: BMI = weight (kg) / height (m)^2.

    Source:
        Cederholm T, et al. Clin Nutr. 2019;38(1):1-9.
    """
    if height_cm <= 0.0 or weight_kg < 0.0:
        raise ValueError("height_cm must be > 0 and weight_kg must be >= 0.")
    height_m = height_cm / 100.0
    return weight_kg / (height_m ** 2)


def compute_skeletal_muscle_mass_kg(weight_kg: float, muscle_pct: float) -> float:
    """Computes total Skeletal Muscle Mass (SMM) in kg: M * (%muscle / 100).

    Source:
        Janssen I, et al. Am J Epidemiol. 2004;159(4):413-421.
    """
    return weight_kg * (muscle_pct / 100.0)


def compute_smi(weight_kg: float, muscle_pct: float, height_cm: float) -> float:
    """Computes total Skeletal Muscle Index: SMI = SMM (kg) / height (m)^2.

    Source:
        Janssen I, et al. Skeletal muscle cutpoints associated with elevated
        physical disability risk. Am J Epidemiol. 2004;159(4):413-421.
    """
    if height_cm <= 0.0:
        raise ValueError("height_cm must be greater than 0.")
    smm_kg = compute_skeletal_muscle_mass_kg(weight_kg, muscle_pct)
    height_m = height_cm / 100.0
    return smm_kg / (height_m ** 2)


def compute_asmi(appendicular_muscle_kg: float, height_cm: float) -> float:
    """Computes Appendicular Skeletal Muscle Index: ASMI = ASM (kg) / height (m)^2.

    Source:
        Cruz-Jentoft AJ, et al. Sarcopenia: revised European consensus (EWGSOP2).
        Age Ageing. 2019;48(1):16-31.
    """
    if height_cm <= 0.0:
        raise ValueError("height_cm must be greater than 0.")
    height_m = height_cm / 100.0
    return appendicular_muscle_kg / (height_m ** 2)


def compute_bmr_mifflin(
    weight_kg: float,
    height_cm: float,
    age_years: int | float,
    sex: int,
) -> float:
    """Computes Resting Energy Expenditure / BMR (kcal/day) via Mifflin-St Jeor.

    Formula:
        Men (sex=1):   10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        Women (sex=0): 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    Source:
        Mifflin MD, et al. A new predictive equation for resting energy expenditure.
        Am J Clin Nutr. 1990;51(2):241-247.
    """
    sex_constant = 5.0 if sex == 1 else -161.0
    return (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age_years) + sex_constant


def compute_glim_malnutrition_risk(
    delta_mass_pct: float,
    bmi: float,
    mission_months: float = 6.0,
) -> int:
    """Evaluates GLIM malnutrition stage (0 = None, 1 = Moderate, 2 = Severe).

    Source:
        Cederholm T, et al. Clin Nutr. 2019;38(1):1-9.
    """
    if mission_months <= 6.0:
        if delta_mass_pct > 10.0 or bmi < 18.5:
            return 2
        if 5.0 <= delta_mass_pct <= 10.0 or bmi < 20.0:
            return 1
    else:
        if delta_mass_pct > 20.0 or bmi < 18.5:
            return 2
        if 10.0 <= delta_mass_pct <= 20.0 or bmi < 20.0:
            return 1
    return 0


# =====================================================================
# 3. PROBLEM F: CIRCADIAN RHYTHM, SLEEP & HPA AXIS
# =====================================================================

def compute_car(
    cortisol_wake_0m_nmol_l: float,
    cortisol_wake_30m_nmol_l: float,
) -> tuple[float, float]:
    """Computes Cortisol Awakening Response: absolute delta (nmol/L) and percentage (%).

    Formulas:
        delta_CAR = C_30m - C_0m
        CAR_pct = ((C_30m - C_0m) / C_0m) * 100

    Source:
        Wüst S, et al. The cortisol awakening response: normal values and confounds.
        Noise Health. 2000;2(7):79-88.
    """
    delta_car = cortisol_wake_30m_nmol_l - cortisol_wake_0m_nmol_l
    if cortisol_wake_0m_nmol_l <= 0.0:
        return delta_car, 0.0
    car_pct = (delta_car / cortisol_wake_0m_nmol_l) * 100.0
    return delta_car, car_pct


def compute_cortisol_diurnal_slope(
    cortisol_morning_peak_nmol_l: float,
    cortisol_bedtime_nmol_l: float,
    delta_hours: float,
) -> float:
    """Computes Diurnal Cortisol Slope: (C_night - C_morning_peak) / delta_t_hours.

    Note: Healthy diurnal slopes are negative (cortisol drops from morning to night).

    Source:
        Medicina 2021;57(10):1079 (Salivary cortisol reference values).
    """
    if delta_hours <= 0.0:
        raise ValueError("delta_hours must be greater than 0.")
    return (cortisol_bedtime_nmol_l - cortisol_morning_peak_nmol_l) / delta_hours


def compute_aucg(
    measurements: Sequence[float],
    time_points_hours: Sequence[float],
) -> float:
    """Computes Area Under the Curve with respect to ground (AUCg) using trapezoidal rule.

    Formula:
        AUCg = sum(((m_i + m_{i+1}) / 2) * delta_t_i)

    Source:
        Pruessner JC, et al. Two formulas for computation of the area under the curve.
        Psychoneuroendocrinology. 2003;28(7):916-931.
    """
    if len(measurements) != len(time_points_hours) or len(measurements) < 2:
        raise ValueError("measurements and time_points_hours must have same length >= 2.")

    aucg = 0.0
    for i in range(len(measurements) - 1):
        delta_t = time_points_hours[i + 1] - time_points_hours[i]
        if delta_t < 0.0:
            raise ValueError("time_points_hours must be in strictly ascending order.")
        aucg += ((measurements[i] + measurements[i + 1]) / 2.0) * delta_t
    return aucg


def convert_dhea_pg_ml_to_nmol_l(dhea_pg_ml: float) -> float:
    """Converts salivary DHEA from pg/mL to nmol/L using molecular weight (288.42 g/mol)."""
    return dhea_pg_ml * DHEA_PG_ML_TO_NMOL_L


def compute_cortisol_dhea_molar_ratio(
    cortisol_nmol_l: float,
    dhea_nmol_l: float | None = None,
    dhea_pg_ml: float | None = None,
) -> float:
    """Computes Catabolic/Anabolic Molar Ratio: [Cortisol (nmol/L)] / [DHEA (nmol/L)].

    Source:
        Mehta SK, et al. npj Microgravity. 2017;3:11.
    """
    if dhea_nmol_l is None:
        if dhea_pg_ml is None:
            raise ValueError("Either dhea_nmol_l or dhea_pg_ml must be provided.")
        dhea_nmol_l = convert_dhea_pg_ml_to_nmol_l(dhea_pg_ml)

    if dhea_nmol_l <= 0.0:
        return 0.0
    return cortisol_nmol_l / dhea_nmol_l


def compute_sns_hpa_asymmetry_index(aucg_saa: float, aucg_cortisol: float) -> float:
    """Computes SNS/HPA Asymmetry Index (AOCg): AUCg(sAA) / AUCg(Cortisol).

    Source:
        Ali N, Pruessner JC. Psychoneuroendocrinology. 2012;37(1):126-135.
    """
    if aucg_cortisol <= 0.0:
        return 0.0
    return aucg_saa / aucg_cortisol


# =====================================================================
# 4. PROBLEM G: EARLY INFECTION, AUTONOMIC OVERLOAD & HRV
# =====================================================================

def compute_rmssd(rr_intervals_ms: Sequence[float]) -> float:
    """Computes Root Mean Square of Successive Differences (RMSSD) of RR intervals (ms).

    Formula:
        RMSSD = sqrt((1 / (N - 1)) * sum((RR_{i+1} - RR_i)^2))

    Source:
        Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    n = len(rr_intervals_ms)
    if n < 2:
        return 0.0
    diff_sq_sum = sum(
        (rr_intervals_ms[i + 1] - rr_intervals_ms[i]) ** 2 for i in range(n - 1)
    )
    return math.sqrt(diff_sq_sum / (n - 1))


def compute_sdnn(rr_intervals_ms: Sequence[float]) -> float:
    """Computes Standard Deviation of NN (RR) intervals (SDNN) in ms.

    Source:
        Task Force of the ESC and NASPE. Circulation. 1996;93(5):1043-1065.
    """
    n = len(rr_intervals_ms)
    if n < 2:
        return 0.0
    mean_rr = sum(rr_intervals_ms) / n
    variance = sum((rr - mean_rr) ** 2 for rr in rr_intervals_ms) / (n - 1)
    return math.sqrt(variance)


def compute_skin_temp_delta(
    nocturnal_skin_temp_c: float,
    baseline_28d_temp_c: float,
) -> float:
    """Computes nocturnal skin temperature deviation from 28-day personal baseline.

    Source:
        Oura Body Temperature deviation from personal baseline.
    """
    return nocturnal_skin_temp_c - baseline_28d_temp_c


def evaluate_liebermeister_rule(
    resting_hr_bpm: float,
    baseline_hr_bpm: float,
    temp_delta_c: float,
    tolerance_bpm: float = 5.0,
) -> tuple[float, int]:
    """Computes expected HR under fever (+8.5 bpm per +1 °C) and checks dissociation.

    Returns:
        (expected_hr_bpm, dissociation_flag): flag is 1 if observed HR exceeds
        expected fever-adjusted HR by more than tolerance_bpm (autonomic/sepsis sign).

    Source:
        Karjalainen J, Viitasalo M. Fever and cardiac rhythm.
        Arch Intern Med. 1986;146(6):1169-1171.
    """
    expected_hr = baseline_hr_bpm + (8.5 * max(temp_delta_c, 0.0))
    dissociation_flag = 1 if (resting_hr_bpm - expected_hr) > tolerance_bpm else 0
    return expected_hr, dissociation_flag


# =====================================================================
# 5. GLYCEMIC, CARDIOVASCULAR, RADIATION & TEAM MODELS
# =====================================================================

def compute_glucose_cv_pct(glucose_series_mg_dl: Sequence[float]) -> float:
    """Computes Glycemic Coefficient of Variation: CV% = (sigma / mu) * 100.

    Target threshold: CV <= 36%.

    Source:
        Battelino T, et al. International consensus on time in range.
        Diabetes Care. 2019;42(8):1593-1603.
    """
    n = len(glucose_series_mg_dl)
    if n < 2:
        return 0.0
    mean_g = sum(glucose_series_mg_dl) / n
    if mean_g <= 0.0:
        return 0.0
    std_g = math.sqrt(sum((g - mean_g) ** 2 for g in glucose_series_mg_dl) / (n - 1))
    return (std_g / mean_g) * 100.0


def compute_glucose_tir_pct(
    glucose_series_mg_dl: Sequence[float],
    low_mg_dl: float = 70.0,
    high_mg_dl: float = 180.0,
) -> float:
    """Computes Time in Range (TIR %) for glucose readings between 70 and 180 mg/dL.

    Source:
        Battelino T, et al. Diabetes Care. 2019;42(8):1593-1603.
    """
    if not glucose_series_mg_dl:
        return 0.0
    in_range = sum(1 for g in glucose_series_mg_dl if low_mg_dl <= g <= high_mg_dl)
    return (in_range / len(glucose_series_mg_dl)) * 100.0


def compute_gmi_pct(mean_glucose_mg_dl: float) -> float:
    """Computes Glucose Management Indicator (GMI %): 3.31 + 0.02392 * mean_glucose.

    Source:
        Bergenstal RM, et al. Glucose Management Indicator (GMI).
        Diabetes Care. 2018;41(11):2275-2280.
    """
    return 3.31 + (0.02392 * mean_glucose_mg_dl)


def compute_max_hr_tanaka(age_years: int | float) -> float:
    """Computes age-predicted Maximal Heart Rate: HR_max = 208 - (0.7 * age).

    Source:
        Tanaka H, Monahan KD, Seals DR. Age-predicted maximal heart rate revisited.
        J Am Coll Cardiol. 2001;37(1):153-156.
    """
    return 208.0 - (0.7 * age_years)


def compute_enmo_mg(ax_g: float, ay_g: float, az_g: float) -> float:
    """Computes Euclidean Norm Minus One (ENMO) in milli-g (mg) from triaxial accelerometer.

    Formula:
        ENMO (mg) = max(sqrt(x^2 + y^2 + z^2) - 1.0, 0.0) * 1000

    Source:
        van Hees VT, et al. PLoS ONE. 2013;8(4):e61691.
    """
    magnitude_g = math.sqrt((ax_g ** 2) + (ay_g ** 2) + (az_g ** 2))
    return max(magnitude_g - 1.0, 0.0) * 1000.0


def compute_oxygen_delivery_do2(
    heart_rate_bpm: float,
    hemoglobin_g_dl: float,
    spo2_pct: float,
    stroke_volume_ml: float = 70.0,
) -> float:
    """Computes Oxygen Delivery (DO2 in mL O2/min): DO2 = CO * CaO2.

    Formulas:
        Cardiac Output (L/min) = (HR * SV_mL) / 1000
        CaO2 (mL O2/dL) ≈ 1.34 * Hb (g/dL) * (SpO2 / 100)
        DO2 (mL/min) = CO (L/min) * CaO2 (mL/dL) * 10

    Source:
        Tomić Mahečić T, et al. J Clin Med. 2022;11(20):6154.
    """
    cardiac_output_l_min = (heart_rate_bpm * stroke_volume_ml) / 1000.0
    cao2_ml_dl = 1.34 * hemoglobin_g_dl * (spo2_pct / 100.0)
    return cardiac_output_l_min * cao2_ml_dl * 10.0


def compute_cumulative_radiation_msv(daily_doses_msv: Sequence[float]) -> float:
    """Computes cumulative mission radiation dose: D_cum = sum(daily_dose_msv).

    Source:
        Zhang S, et al. Sci Adv. 2020;6(39):eaaz1334.
        Hassler DM, et al. Science. 2014;343(6169):1244797.
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
    """Computes custom team Anemia Suspicion Index (IA = sum(w_i * z_i)) and flag (0|1).

    Combines compensatory tachycardia (HR Z-score), SpO2 drop, and reduced physical
    activity (space anemia compensation model). If blood hemoglobin is available
    and drops >= 1.5 g/dL below pre-mission baseline, triggers flag directly.

    Source:
        Team Custom Design (based on Tomić Mahečić 2022 & space anemia physiology).
    """
    index_score = (0.5 * max(resting_hr_z, 0.0)) + (0.3 * max(spo2_drop_z, 0.0)) + (
        0.2 * max(enmo_fatigue_z, 0.0)
    )
    hb_drop_flag = (
        baseline_hb_g_dl is not None
        and current_hb_g_dl is not None
        and (baseline_hb_g_dl - current_hb_g_dl) >= 1.5
    )
    flag = 1 if (index_score >= threshold or hb_drop_flag) else 0
    return round(index_score, 3), flag


def compute_readiness_score(
    z_penalties: dict[str, float],
    weights: dict[str, float] | None = None,
) -> float:
    """Computes Daily Operational Readiness Score (0-100): P = 100 - sum(w_i * max(z_i, 0)).

    Args:
        z_penalties: Dictionary mapping metric names to positive deviation magnitudes
            (where higher positive values represent worse physiological deviation).
        weights: Optional weight dictionary per metric. Defaults to balanced weights.

    Source:
        Team Custom Design (Space Apps 2026).
    """
    default_weights: dict[str, float] = {
        "sleep_deficit": 8.0,
        "hrv_drop": 7.0,
        "resting_hr_elevation": 6.0,
        "skin_temp_deviation": 7.0,
        "cortisol_imbalance": 5.0,
        "muscle_loss": 5.0,
        "glycemic_instability": 4.0,
    }
    active_weights = weights if weights is not None else default_weights

    total_penalty = 0.0
    for key, z_val in z_penalties.items():
        w = active_weights.get(key, 5.0)
        total_penalty += w * max(z_val, 0.0)

    score = max(0.0, min(100.0, 100.0 - total_penalty))
    return round(score, 1)


# =====================================================================
# QUICK SELF-TEST (Run with: python core/equations.py)
# =====================================================================
if __name__ == "__main__":
    print("--- Astro-Health Equations Verification ---")
    print("BMI (75 kg, 178 cm):", round(compute_bmi(75.0, 178.0), 2))
    print("SMI Male (75 kg, 42% muscle, 178 cm):", round(compute_smi(75.0, 42.0, 178.0), 2))
    print("BMR Mifflin (Female, 62 kg, 165 cm, 34 y):", round(compute_bmr_mifflin(62, 165, 34, sex=0), 1))
    car_abs, car_pct = compute_car(14.0, 22.0)
    print(f"CAR (14 -> 22 nmol/L): +{car_abs:.1f} nmol/L (+{car_pct:.1f}%)")
    print("Cortisol AUCg:", round(compute_aucg([15.0, 22.5, 6.0, 2.0], [0.0, 0.5, 8.5, 15.5]), 2))
    print("RMSSD ([800, 820, 790, 830] ms):", round(compute_rmssd([800, 820, 790, 830]), 2))
    print("Max HR Tanaka (35 y):", compute_max_hr_tanaka(35))
    print("DO2 (HR=65, Hb=14.5, SpO2=98%):", round(compute_oxygen_delivery_do2(65, 14.5, 98.0), 1))
