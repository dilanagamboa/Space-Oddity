"""Physiological thresholds, NASA radiation limits, and clinical rule evaluation.

Implements risk detection logic for the following health domains:
- Problem C: Muscle atrophy, malnutrition, and visceral fat (GLIM, Janssen, Tanita).
- Problem F: Circadian rhythm, sleep, and HPA axis (AASM/SRS, Wüst, Pruessner).
- Problem G: Early infection and autonomic overload (ACCP/SCCM, Mishra, Oura).
- Safety (S1-S4): Glucose (ADA), Blood Pressure (ACC/AHA), SpO2, Radiation (NASA/NOAA).

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

Severity = Literal["normal", "yellow", "red"]
AlertCategory = Literal["C", "F", "G", "S"]
SamplingMode = Literal["standard", "alert_infection", "alert_atrophy", "alert_circadian"]


@dataclass(frozen=True)
class TriggeredRule:
    """Represents an individual physiological metric that exceeded its normal range."""

    metric: str
    category: AlertCategory
    severity: Severity
    current_value: float | str
    reference_threshold: str
    source: str
    notification_id: str


@dataclass(frozen=True)
class EvaluationResult:
    """Consolidated result after evaluating all metrics for a mission day."""

    triage_status: Literal["green", "yellow", "red"]
    recommended_sampling_mode: SamplingMode
    triggered_rules: list[TriggeredRule]
    category_severities: dict[AlertCategory, Severity]

    def to_dict(self) -> dict[str, Any]:
        """Converts the evaluation result into a JSON-serializable dictionary."""
        return asdict(self)


# =====================================================================
# 1. CLINICAL CONSTANTS AND THRESHOLDS (Documented Sources)
# =====================================================================

THRESHOLDS: dict[str, dict[str, Any]] = {
    # --- PROBLEM C: Muscle, malnutrition, and visceral fat ---
    "delta_mass_pct": {"yellow": 5.0, "red": 10.0, "source": "GLIM 2019 [1]"},
    "bmi": {"yellow": 20.0, "red": 18.5, "source": "GLIM 2019 [1]"},
    "smi_male": {"yellow": 10.75, "red": 8.50, "source": "Janssen et al. 2004 [2]"},
    "smi_female": {"yellow": 6.75, "red": 5.75, "source": "Janssen et al. 2004 [2]"},
    "asmi_bia": {"male": 7.0, "female": 5.7, "source": "EWGSOP2 2019 [3]"},
    "visceral_fat_area_cm2": {"yellow": 100.0, "source": "JSSO 2002 / Team [4]"},
    "visceral_fat_tanita": {"yellow": 13.0, "red": 20.0, "source": "Tanita / Team [5]"},
    # --- PROBLEM F: Circadian rhythm, sleep, and HPA axis ---
    "sleep_hours": {"yellow": 6.0, "red": 5.0, "source": "AASM/SRS Watson 2015 / Team [9]"},
    "car_delta_nmol_l": {"yellow": 2.5, "source": "Wüst 2000 / Team [10]"},
    "cortisol_morning_nmol_l": {"min": 13.0, "max": 24.0, "source": "Medicina 2021 [12]"},
    "cortisol_afternoon_nmol_l": {"min": 3.0, "max": 8.0, "source": "Medicina 2021 [12]"},
    "cortisol_night_nmol_l": {"yellow": 4.0, "source": "Medicina 2021 [12]"},
    "aucg_cortisol_z": {"yellow": 1.5, "red": 3.0, "source": "Pruessner 2003 [11]"},
    "cortisol_dhea_ratio_z": {"yellow": 1.5, "red": 3.0, "source": "Mehta 2017 / Team"},
    "saa_z": {"yellow": 2.0, "source": "Nater & Rohleder 2009 [14]"},
    "hrv_rmssd_z_circadian": {"yellow": -1.5, "red": -3.0, "source": "Task Force 1996 / Nunan 2010 [15][16]"},
    "hrv_sdnn_z": {"yellow": -1.5, "source": "Nunan 2010 [16]"},
    # --- PROBLEM G: Early infection ---
    "skin_temp_delta_c": {"threshold": 0.5, "luteal_offset": 0.4, "source": "Oura / Team [19]"},
    "core_temp_c": {"min": 36.0, "max": 38.0, "source": "ACCP/SCCM Bone 1992 [17]"},
    "resting_hr": {"yellow_z": 1.5, "red_bpm": 90.0, "source": "Bone 1992 / Mishra 2020 [17][18]"},
    "hrv_z_infection": {"yellow": -1.5, "red": -2.5, "source": "Task Force 1996 / Team [15]"},
    "respiratory_rate_rpm": {"red": 20.0, "source": "ACCP/SCCM Bone 1992 [17]"},
    # --- SAFETY (S1 - S4: Dashboard / Immediate Action) ---
    "glucose_mg_dl": {"red_low": 54.0, "yellow_low": 70.0, "yellow_high": 180.0, "red_high": 250.0,
                      "source": "ADA / Battelino 2019 [21][22]"},
    "blood_pressure_mmhg": {"yellow_sys": 140.0, "yellow_dia": 90.0, "red_sys": 180.0, "red_dia": 120.0,
                            "source": "ACC/AHA 2017 [23]"},
    "spo2_pct": {"yellow": 95.0, "red": 90.0, "source": "Standard clinical criteria"},
    "radiation": {"yellow_ratio": 0.50, "proton_pfu": 10.0, "source": "NASA-STD-3001 / NOAA SWPC [26][27]"},
}


def _rule(metric: str, cat: AlertCategory, sev: Severity, val: float | str,
          ref: str, key: str, notif: str, digits: int = 2) -> TriggeredRule:
    """Builds a TriggeredRule rounding numeric values and resolving the source string."""
    v = round(val, digits) if isinstance(val, float) else val
    return TriggeredRule(metric, cat, sev, v, ref, THRESHOLDS[key]["source"], notif)


# =====================================================================
# 2. SUBSYSTEM CLINICAL EVALUATORS
# =====================================================================

def evaluate_problem_c(
    delta_mass_pct: float | None = None,
    bmi: float | None = None,
    smi_total_kg_m2: float | None = None,
    sex: int = 1,  # 0 = Female, 1 = Male
    asmi_kg_m2: float | None = None,
    visceral_fat_area_cm2: float | None = None,
    visceral_fat_level: float | None = None,
) -> list[TriggeredRule]:
    """Evaluates Problem C: Muscle loss, malnutrition, and visceral fat (C1-C3)."""
    rules: list[TriggeredRule] = []

    # 1. Body mass loss percentage (GLIM criteria)
    if delta_mass_pct is not None:
        if delta_mass_pct > THRESHOLDS["delta_mass_pct"]["red"]:
            rules.append(_rule("delta_mass_pct", "C", "red", delta_mass_pct, "> 10% in <= 6 months (GLIM Stage 2)", "delta_mass_pct", "C1"))
        elif delta_mass_pct >= THRESHOLDS["delta_mass_pct"]["yellow"]:
            rules.append(_rule("delta_mass_pct", "C", "yellow", delta_mass_pct, "5-10% in <= 6 months (GLIM Stage 1)", "delta_mass_pct", "C1"))

    # 2. Body Mass Index (BMI)
    if bmi is not None:
        if bmi < THRESHOLDS["bmi"]["red"]:
            rules.append(_rule("bmi", "C", "red", bmi, "< 18.5 kg/m²", "bmi", "C1"))
        elif bmi < THRESHOLDS["bmi"]["yellow"]:
            rules.append(_rule("bmi", "C", "yellow", bmi, "< 20.0 kg/m²", "bmi", "C1"))

    # 3. Skeletal Muscle Index (SMI) by sex
    is_low_smi = False
    if smi_total_kg_m2 is not None:
        smi_key = "smi_male" if sex == 1 else "smi_female"
        cfg = THRESHOLDS[smi_key]
        if smi_total_kg_m2 <= cfg["red"]:
            is_low_smi = True
            rules.append(_rule("smi_total_kg_m2", "C", "red", smi_total_kg_m2, f"<= {cfg['red']} kg/m²", smi_key, "C2"))
        elif smi_total_kg_m2 <= cfg["yellow"]:
            is_low_smi = True
            rules.append(_rule("smi_total_kg_m2", "C", "yellow", smi_total_kg_m2, f"<= {cfg['yellow']} kg/m²", smi_key, "C2"))

    # 4. Appendicular Skeletal Muscle Index (ASMI) optional
    if asmi_kg_m2 is not None:
        limit = THRESHOLDS["asmi_bia"]["male" if sex == 1 else "female"]
        if asmi_kg_m2 < limit:
            is_low_smi = True
            rules.append(_rule("asmi_kg_m2", "C", "red", asmi_kg_m2, f"< {limit} kg/m² (BIA)", "asmi_bia", "C2"))

    # 5. Visceral fat area (cm2 + sarcopenic obesity combination)
    if visceral_fat_area_cm2 is not None and visceral_fat_area_cm2 >= THRESHOLDS["visceral_fat_area_cm2"]["yellow"]:
        sev: Severity = "red" if is_low_smi else "yellow"
        ref = ">= 100 cm² + low SMI (Sarcopenic obesity)" if is_low_smi else ">= 100 cm²"
        rules.append(_rule("visceral_fat_area_cm2", "C", sev, visceral_fat_area_cm2, ref, "visceral_fat_area_cm2", "C3", 1))

    # 6. Visceral fat level (Tanita scale 1-59)
    if visceral_fat_level is not None:
        t_cfg = THRESHOLDS["visceral_fat_tanita"]
        if visceral_fat_level >= t_cfg["red"] or (visceral_fat_level >= t_cfg["yellow"] and is_low_smi):
            rules.append(_rule("visceral_fat_level", "C", "red", visceral_fat_level, ">= 20 (or >= 13 with low SMI)", "visceral_fat_tanita", "C3"))
        elif visceral_fat_level >= t_cfg["yellow"]:
            rules.append(_rule("visceral_fat_level", "C", "yellow", visceral_fat_level, "13 - 19 (Tanita scale)", "visceral_fat_tanita", "C3"))

    return rules


def evaluate_problem_f(
    sleep_hours_history: Sequence[float] | None = None,
    car_absolute_nmol_l: float | None = None,
    car_blunted_consecutive_weeks: int = 0,
    cortisol_morning_nmol_l: float | None = None,
    cortisol_afternoon_nmol_l: float | None = None,
    cortisol_night_nmol_l: float | None = None,
    cortisol_dhea_ratio_z: float | None = None,
    cortisol_dhea_ratio_rising: bool = False,
    aucg_cortisol_z: float | None = None,
    cortisol_diurnal_slope_flattened: bool = False,
    saa_z: float | None = None,
    hrv_rmssd_z: float | None = None,
    hrv_sdnn_z: float | None = None,
) -> list[TriggeredRule]:
    """Evaluates Problem F: Circadian misalignment, sleep, and HPA axis (F1)."""
    rules: list[TriggeredRule] = []

    # 1. Consecutive nights of sleep deprivation
    if sleep_hours_history:
        if len(sleep_hours_history) >= 2 and all(h < 5.0 for h in sleep_hours_history[-2:]):
            rules.append(_rule("sleep_total_min", "F", "red", sleep_hours_history[-1], "< 5 h for 2 consecutive nights", "sleep_hours", "F1"))
        elif len(sleep_hours_history) >= 3 and all(h < 6.0 for h in sleep_hours_history[-3:]):
            rules.append(_rule("sleep_total_min", "F", "yellow", sleep_hours_history[-1], "< 6 h for 3 consecutive nights", "sleep_hours", "F1"))

    # 2. Cortisol Awakening Response (CAR)
    if car_absolute_nmol_l is not None and car_absolute_nmol_l < THRESHOLDS["car_delta_nmol_l"]["yellow"]:
        sev: Severity = "red" if car_blunted_consecutive_weeks >= 2 else "yellow"
        ref = "Blunted CAR (< +2.5 nmol/L) for 2 consecutive weeks" if sev == "red" else "Increase < +2.5 nmol/L at +30 min"
        rules.append(_rule("car_absolute_nmol_l", "F", sev, car_absolute_nmol_l, ref, "car_delta_nmol_l", "F1"))

    # 3. Salivary cortisol by time slot
    if cortisol_morning_nmol_l is not None and not (13.0 <= cortisol_morning_nmol_l <= 24.0):
        rules.append(_rule("cortisol_07_08h", "F", "yellow", cortisol_morning_nmol_l, "Outside 13-24 nmol/L range", "cortisol_morning_nmol_l", "F1"))
    if cortisol_afternoon_nmol_l is not None and not (3.0 <= cortisol_afternoon_nmol_l <= 8.0):
        rules.append(_rule("cortisol_16_17h", "F", "yellow", cortisol_afternoon_nmol_l, "Outside 3-8 nmol/L range", "cortisol_afternoon_nmol_l", "F1"))
    if cortisol_night_nmol_l is not None and cortisol_night_nmol_l > 4.0:
        rising = cortisol_dhea_ratio_rising or (cortisol_dhea_ratio_z is not None and cortisol_dhea_ratio_z > 1.5)
        sev = "red" if rising else "yellow"
        ref = "> 4 nmol/L + rising C/DHEA ratio" if rising else "> 4 nmol/L (23-24 h)"
        rules.append(_rule("cortisol_23_24h", "F", sev, cortisol_night_nmol_l, ref, "cortisol_night_nmol_l", "F1"))

    # 4. Total daily cortisol output (AUCg Z-score)
    if aucg_cortisol_z is not None:
        if aucg_cortisol_z > 3.0:
            rules.append(_rule("aucg_cortisol", "F", "red", aucg_cortisol_z, "z > +3.0", "aucg_cortisol_z", "F1"))
        elif aucg_cortisol_z > 1.5:
            rules.append(_rule("aucg_cortisol", "F", "yellow", aucg_cortisol_z, "z > +1.5", "aucg_cortisol_z", "F1"))

    # 5. Diurnal cortisol slope
    if cortisol_diurnal_slope_flattened:
        rules.append(_rule("cortisol_diurnal_slope", "F", "yellow", "Flattened", "Flattened slope vs. baseline", "cortisol_night_nmol_l", "F1"))

    # 6. Cortisol/DHEA molar ratio (Z-score)
    if cortisol_dhea_ratio_z is not None:
        if cortisol_dhea_ratio_z > 3.0:
            rules.append(_rule("cortisol_dhea_molar_ratio", "F", "red", cortisol_dhea_ratio_z, "z > +3.0 vs. baseline", "cortisol_dhea_ratio_z", "F1"))
        elif cortisol_dhea_ratio_z > 1.5:
            rules.append(_rule("cortisol_dhea_molar_ratio", "F", "yellow", cortisol_dhea_ratio_z, "z > +1.5 vs. baseline", "cortisol_dhea_ratio_z", "F1"))

    # 7. Salivary alpha-amylase (sAA Z-score)
    if saa_z is not None and saa_z > 2.0:
        rules.append(_rule("salivary_alpha_amylase_u_ml", "F", "yellow", saa_z, "z > +2.0 vs. baseline", "saa_z", "F1"))

    # 8. Resting HRV RMSSD and SDNN (Autonomic/Circadian axis)
    if hrv_rmssd_z is not None:
        if hrv_rmssd_z < -3.0:
            rules.append(_rule("hrv_rmssd_daily_ms", "F", "red", hrv_rmssd_z, "z < -3.0", "hrv_rmssd_z_circadian", "F1"))
        elif hrv_rmssd_z < -1.5:
            rules.append(_rule("hrv_rmssd_daily_ms", "F", "yellow", hrv_rmssd_z, "z < -1.5", "hrv_rmssd_z_circadian", "F1"))
    if hrv_sdnn_z is not None and hrv_sdnn_z < -1.5:
        rules.append(_rule("hrv_sdnn_daily_ms", "F", "yellow", hrv_sdnn_z, "z < -1.5", "hrv_sdnn_z", "F1"))

    return rules


def evaluate_problem_g(
    skin_temp_deltas_history: Sequence[float] | None = None,
    core_temp_c: float | None = None,
    resting_hr_bpm: float | None = None,
    resting_hr_z: float | None = None,
    hrv_z_score: float | None = None,
    respiratory_rate_rpm: float | None = None,
    respiratory_rate_rising: bool = False,
    sex: int = 1,
    cycle_phase: str | None = None,
    compensate_luteal_in_code: bool = False,
) -> list[TriggeredRule]:
    """Evaluates Problem G: Early infection and sepsis risk (G1).

    Mandatory combined rule (Section 5.3):
    Alert G triggers ONLY if temperature abnormality (nocturnal skin temp or core temp)
    coincides with at least one accompanying autonomic/vital sign (HR, HRV, or RR).
    """
    temp_rules: list[TriggeredRule] = []
    sign_rules: list[TriggeredRule] = []

    # 1. Nocturnal skin temperature deviation (with optional luteal phase offset in females)
    if skin_temp_deltas_history:
        offset = THRESHOLDS["skin_temp_delta_c"]["luteal_offset"] if (compensate_luteal_in_code and sex == 0 and cycle_phase == "luteal") else 0.0
        limit = THRESHOLDS["skin_temp_delta_c"]["threshold"] + offset
        if len(skin_temp_deltas_history) >= 3 and all(dt >= limit for dt in skin_temp_deltas_history[-3:]):
            temp_rules.append(_rule("skin_temp_delta_c", "G", "red", skin_temp_deltas_history[-1], f">= +{limit:.1f} °C for 3 consecutive nights", "skin_temp_delta_c", "G1"))
        elif len(skin_temp_deltas_history) >= 2 and all(dt >= limit for dt in skin_temp_deltas_history[-2:]):
            temp_rules.append(_rule("skin_temp_delta_c", "G", "yellow", skin_temp_deltas_history[-1], f">= +{limit:.1f} °C for 2 consecutive nights", "skin_temp_delta_c", "G1"))

    # 2. Core body temperature (Thermometer)
    if core_temp_c is not None and (core_temp_c > 38.0 or core_temp_c < 36.0):
        temp_rules.append(_rule("core_temp_c", "G", "red", core_temp_c, "> 38.0 °C or < 36.0 °C", "core_temp_c", "G1"))

    # 3. Accompanying sign: Resting Heart Rate
    if resting_hr_bpm is not None and resting_hr_bpm > 90.0:
        sign_rules.append(_rule("resting_hr_bpm", "G", "red", resting_hr_bpm, "> 90 bpm", "resting_hr", "G1", 1))
    elif resting_hr_z is not None and resting_hr_z > 1.5:
        sign_rules.append(_rule("resting_hr_bpm", "G", "yellow", resting_hr_z, "z > +1.5 vs. baseline", "resting_hr", "G1"))

    # 4. Accompanying sign: HRV (Z_HRV = (ln RMSSD_today - mu_ln) / sigma_ln)
    if hrv_z_score is not None:
        if hrv_z_score < -2.5:
            sign_rules.append(_rule("hrv_z_score", "G", "red", hrv_z_score, "Z_HRV < -2.5", "hrv_z_infection", "G1"))
        elif hrv_z_score < -1.5:
            sign_rules.append(_rule("hrv_z_score", "G", "yellow", hrv_z_score, "Z_HRV < -1.5", "hrv_z_infection", "G1"))

    # 5. Accompanying sign: Respiratory Rate
    if respiratory_rate_rpm is not None and respiratory_rate_rpm > 20.0:
        sign_rules.append(_rule("respiratory_rate_daily_rpm", "G", "red", respiratory_rate_rpm, "> 20 rpm", "respiratory_rate_rpm", "G1", 1))
    elif respiratory_rate_rising:
        sign_rules.append(_rule("respiratory_rate_daily_rpm", "G", "yellow", respiratory_rate_rpm or 0.0, "Rising vs. personal baseline", "respiratory_rate_rpm", "G1", 1))

    # Anti-false-positive filter: requires Temperature + at least 1 other sign (HR, HRV, or RR)
    return (temp_rules + sign_rules) if (temp_rules and sign_rules) else []


def evaluate_safety(
    blood_glucose_mg_dl: float | None = None,
    systolic_bp_mmhg: float | None = None,
    diastolic_bp_mmhg: float | None = None,
    spo2_pct: float | None = None,
    crew_spo2_drop_count: int = 0,
    cumulative_radiation_msv: float | None = None,
    mission_radiation_limit_msv: float = 300.0,
    solar_event_flag: int = 0,
    proton_flux_pfu: float | None = None,
) -> list[TriggeredRule]:
    """Evaluates direct dashboard safety indicators (S1-S4)."""
    rules: list[TriggeredRule] = []

    # S1: Blood glucose
    if blood_glucose_mg_dl is not None:
        if blood_glucose_mg_dl < 54.0 or blood_glucose_mg_dl > 250.0:
            rules.append(_rule("blood_glucose_mg_dl", "S", "red", blood_glucose_mg_dl, "< 54 mg/dL (Level 2) or > 250 mg/dL", "glucose_mg_dl", "S1", 1))
        elif blood_glucose_mg_dl < 70.0 or blood_glucose_mg_dl > 180.0:
            rules.append(_rule("blood_glucose_mg_dl", "S", "yellow", blood_glucose_mg_dl, "< 70 mg/dL (Level 1) or > 180 mg/dL", "glucose_mg_dl", "S1", 1))

    # S2: Estimated blood pressure
    if systolic_bp_mmhg is not None and diastolic_bp_mmhg is not None:
        bp = f"{int(systolic_bp_mmhg)}/{int(diastolic_bp_mmhg)}"
        if systolic_bp_mmhg >= 180.0 or diastolic_bp_mmhg >= 120.0:
            rules.append(_rule("blood_pressure_mmhg", "S", "red", bp, ">= 180/120 mmHg (Crisis)", "blood_pressure_mmhg", "S2"))
        elif systolic_bp_mmhg >= 140.0 or diastolic_bp_mmhg >= 90.0:
            rules.append(_rule("blood_pressure_mmhg", "S", "yellow", bp, ">= 140/90 mmHg (Stage 2)", "blood_pressure_mmhg", "S2"))

    # S3: Oxygen saturation (Individual SpO2 or simultaneous drop in >= 2 crew members)
    if (spo2_pct is not None and spo2_pct < 90.0) or crew_spo2_drop_count >= 2:
        rules.append(_rule("spo2_pct", "S", "red", spo2_pct or 0.0, "< 90% or simultaneous drop in 2+ crew members", "spo2_pct", "S3", 1))
    elif spo2_pct is not None and spo2_pct < 95.0:
        rules.append(_rule("spo2_pct", "S", "yellow", spo2_pct, "< 95% sustained", "spo2_pct", "S3", 1))

    # S4: Space radiation (Cumulative dose vs. Solar Particle Event SPE)
    if solar_event_flag or (proton_flux_pfu is not None and proton_flux_pfu >= 10.0):
        rules.append(_rule("radiation_spe", "S", "red", proton_flux_pfu if proton_flux_pfu is not None else "Active SPE", "Solar event: protons >= 10 MeV at >= 10 pfu", "radiation", "S4"))
    elif cumulative_radiation_msv is not None:
        limit = mission_radiation_limit_msv * THRESHOLDS["radiation"]["yellow_ratio"]
        if cumulative_radiation_msv >= limit:
            rules.append(_rule("cumulative_radiation_msv", "S", "yellow", cumulative_radiation_msv, f"Cumulative >= 50% of limit ({limit:.0f} mSv)", "radiation", "S4"))

    return rules


# =====================================================================
# 3. TRIAGE & SAMPLING MODE ORCHESTRATOR (For daily_health_reports)
# =====================================================================

def _max_sev(rules: Sequence[TriggeredRule], category: AlertCategory) -> Severity:
    severities = {r.severity for r in rules if r.category == category}
    return "red" if "red" in severities else ("yellow" if "yellow" in severities else "normal")


def evaluate_overall_health(
    rules_c: Sequence[TriggeredRule],
    rules_f: Sequence[TriggeredRule],
    rules_g: Sequence[TriggeredRule],
    rules_s: Sequence[TriggeredRule],
) -> EvaluationResult:
    """Consolidates all active rules, determines triage status and sampling mode.

    Sampling mode priority according to database schema:
    1. 'alert_infection' (If G is active)
    2. 'alert_atrophy'   (If C is active)
    3. 'alert_circadian' (If F is active)
    4. 'standard'        (Nominal state)
    """
    all_rules = [*rules_c, *rules_f, *rules_g, *rules_s]
    cat_sev: dict[AlertCategory, Severity] = {c: _max_sev(all_rules, c) for c in ("C", "F", "G", "S")}

    # Determine overall triage_status (green | yellow | red)
    vals = set(cat_sev.values())
    triage: Literal["green", "yellow", "red"] = "red" if "red" in vals else ("yellow" if "yellow" in vals else "green")

    # Determine the monitoring/sampling mode triggered by the alert priority
    if cat_sev["G"] != "normal":
        mode: SamplingMode = "alert_infection"
    elif cat_sev["C"] != "normal":
        mode = "alert_atrophy"
    elif cat_sev["F"] != "normal":
        mode = "alert_circadian"
    else:
        mode = "standard"

    return EvaluationResult(triage, mode, all_rules, cat_sev)
