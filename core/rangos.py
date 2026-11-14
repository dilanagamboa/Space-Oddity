"""Physiological thresholds, NASA radiation limits, and clinical rule evaluation.

Implements risk detection logic for the following health domains:
- Problem C: Muscle atrophy, malnutrition, and visceral fat (GLIM, Janssen, Tanita).
- Problem F: Circadian rhythm, sleep, and HPA axis (AASM/SRS, Wüst, Pruessner).
- Problem G: Early infection and autonomic overload (ACCP/SCCM, Mishra, Oura).
- Safety (S1-S4): Glucose (ADA), Blood Pressure (ACC/AHA), SpO2, Radiation (NASA/NOAA).

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from dataclasses import dataclass, asdict
from typing import Any, Literal

Severity = Literal["normal", "yellow", "red"]
AlertCategory = Literal["C", "F", "G", "S"]
SamplingMode = Literal[
    "standard",
    "alert_infection",
    "alert_atrophy",
    "alert_circadian",
]


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
        return {
            "triage_status": self.triage_status,
            "recommended_sampling_mode": self.recommended_sampling_mode,
            "category_severities": self.category_severities,
            "triggered_rules": [asdict(rule) for rule in self.triggered_rules],
        }


# =====================================================================
# 1. CLINICAL CONSTANTS AND THRESHOLDS (Documented Sources)
# =====================================================================

THRESHOLDS: dict[str, Any] = {
    # --- PROBLEM C: Muscle, malnutrition, and visceral fat ---
    "delta_mass_pct": {
        "yellow_min": 5.0,   # 5-10% in <= 6 months (GLIM Stage 1) [1]
        "red_min": 10.0,     # > 10% in <= 6 months (GLIM Stage 2) [1]
        "source": "GLIM 2019 [1]",
    },
    "bmi": {
        "yellow_max": 20.0,  # < 20 kg/m2 (< 70 years) [1]
        "red_max": 18.5,     # < 18.5 kg/m2 [1]
        "source": "GLIM 2019 [1]",
    },
    "smi_male": {  # sex == 1 (Male)
        "yellow_max": 10.75,  # 8.51 - 10.75 kg/m2 [2]
        "red_max": 8.50,      # <= 8.50 kg/m2 [2]
        "source": "Janssen et al. 2004 [2]",
    },
    "smi_female": {  # sex == 0 (Female)
        "yellow_max": 6.75,   # 5.76 - 6.75 kg/m2 [2]
        "red_max": 5.75,      # <= 5.75 kg/m2 [2]
        "source": "Janssen et al. 2004 [2]",
    },
    "asmi_bia": {  # Appendicular skeletal muscle index (if provided by BIA chair)
        "red_male_max": 7.0,    # < 7.0 kg/m2 [3]
        "red_female_max": 5.7,  # < 5.7 kg/m2 [3]
        "source": "EWGSOP2 2019 [3]",
    },
    "visceral_fat_area_cm2": {
        "yellow_min": 100.0,  # >= 100 cm2 (Red if combined with low SMI) [4]
        "source": "JSSO 2002 / Team [4]",
    },
    "visceral_fat_tanita": {
        "yellow_min": 13.0,   # 13 - 19 [5]
        "red_min": 20.0,      # >= 20 [5]
        "source": "Tanita / Team [5]",
    },

    # --- PROBLEM F: Circadian rhythm, sleep, and HPA axis ---
    "sleep_hours": {
        "normal_min": 7.0,
        "yellow_max": 6.0,    # < 6 h for 3 consecutive nights [9]
        "yellow_nights": 3,
        "red_max": 5.0,       # < 5 h for 2 consecutive nights [9]
        "red_nights": 2,
        "source": "AASM/SRS Watson 2015 / Team [9]",
    },
    "car_delta_nmol_l": {
        "yellow_max": 2.5,    # Increase < +2.5 nmol/L (blunted) [10]
        "red_weeks": 2,       # Blunted for 2 consecutive weeks [10]
        "source": "Wüst 2000 / Team [10]",
    },
    "cortisol_morning_nmol_l": {
        "min": 13.0,          # 07-08 h: 13-24 nmol/L [12]
        "max": 24.0,
        "source": "Medicina 2021 [12]",
    },
    "cortisol_afternoon_nmol_l": {
        "min": 3.0,           # 16-17 h: 3-8 nmol/L [12]
        "max": 8.0,
        "source": "Medicina 2021 [12]",
    },
    "cortisol_night_nmol_l": {
        "yellow_min": 4.0,    # 23-24 h: > 4 nmol/L (Red if C/DHEA ratio is rising) [12]
        "source": "Medicina 2021 [12]",
    },
    "aucg_cortisol_z": {
        "yellow_min": 1.5,    # z > +1.5 [11]
        "red_min": 3.0,       # z > +3.0 [11]
        "source": "Pruessner 2003 [11]",
    },
    "cortisol_dhea_ratio_z": {
        "yellow_min": 1.5,    # z > +1.5 vs. baseline
        "red_min": 3.0,       # z > +3.0 vs. baseline
        "source": "Mehta 2017 / Team",
    },
    "saa_z": {
        "yellow_min": 2.0,    # z > +2.0 vs. baseline [13][14]
        "source": "Nater & Rohleder 2009 [14]",
    },
    "hrv_rmssd_z_circadian": {
        "yellow_max": -1.5,   # z < -1.5 [15][16]
        "red_max": -3.0,      # z < -3.0 [15][16]
        "source": "Task Force 1996 / Nunan 2010 [15][16]",
    },
    "hrv_sdnn_z": {
        "yellow_max": -1.5,   # z < -1.5 [16]
        "source": "Nunan 2010 [16]",
    },

    # --- PROBLEM G: Early infection ---
    "skin_temp_delta_c": {
        "threshold": 0.5,     # >= +0.5 °C vs. 28-day baseline [19]
        "luteal_offset": 0.4, # Physiological luteal phase compensation (+0.3 to +0.5 °C)
        "yellow_nights": 2,
        "red_nights": 3,
        "source": "Oura / Team [19]",
    },
    "core_temp_c": {
        "red_min": 36.0,      # < 36.0 °C or > 38.0 °C [17]
        "red_max": 38.0,
        "source": "ACCP/SCCM Bone 1992 [17]",
    },
    "resting_hr": {
        "yellow_z_min": 1.5,  # z > +1.5 [18]
        "red_bpm_min": 90.0,  # > 90 bpm [17]
        "source": "Bone 1992 / Mishra 2020 [17][18]",
    },
    "hrv_z_infection": {
        "yellow_max": -1.5,   # Z_HRV < -1.5 [15]
        "red_max": -2.5,      # Z_HRV < -2.5 [15]
        "source": "Task Force 1996 / Team [15]",
    },
    "respiratory_rate_rpm": {
        "normal_min": 12.0,
        "red_min": 20.0,      # > 20 rpm (Yellow if rising vs. baseline) [17]
        "source": "ACCP/SCCM Bone 1992 [17]",
    },

    # --- SAFETY (S1 - S4: Dashboard / Immediate Action) ---
    "glucose_mg_dl": {
        "red_low": 54.0,      # < 54 mg/dL (Level 2 hypoglycemia) [21]
        "yellow_low": 70.0,   # < 70 mg/dL (Level 1 hypoglycemia) [21]
        "yellow_high": 180.0, # > 180 mg/dL [22]
        "red_high": 250.0,    # > 250 mg/dL [22]
        "source": "ADA / Battelino 2019 [21][22]",
    },
    "blood_pressure_mmhg": {
        "yellow_sys": 140.0,  # >= 140/90 mmHg (Stage 2) [23]
        "yellow_dia": 90.0,
        "red_sys": 180.0,     # >= 180/120 mmHg (Hypertensive crisis) [23]
        "red_dia": 120.0,
        "source": "ACC/AHA 2017 [23]",
    },
    "spo2_pct": {
        "yellow_max": 95.0,   # < 95% sustained
        "red_max": 90.0,      # < 90% or simultaneous drop in 2+ crew members
        "source": "Standard clinical criteria",
    },
    "radiation": {
        "limit_msv": 300.0,       # 50% of NASA career limit (600 mSv) [26]
        "yellow_ratio": 0.50,     # Cumulative >= 50% of mission limit
        "proton_mev_min": 10.0,   # Solar particle event: protons >= 10 MeV at >= 10 pfu [27]
        "proton_pfu_min": 10.0,
        "source": "NASA-STD-3001 / NOAA SWPC [26][27]",
    },
}


# =====================================================================
# 2. SUBSYSTEM CLINICAL EVALUATORS
# =====================================================================

def evaluate_problem_c(
    delta_mass_pct: float | None = None,
    bmi: float | None = None,
    smi_total_kg_m2: float | None = None,
    sex: int = 1,  # 0 = Female, 1 = Male (matches users table)
    asmi_kg_m2: float | None = None,
    visceral_fat_area_cm2: float | None = None,
    visceral_fat_level: float | None = None,
) -> list[TriggeredRule]:
    """Evaluates Problem C: Muscle loss, malnutrition, and visceral fat (C1-C3)."""
    rules: list[TriggeredRule] = []

    # 1. Body mass loss percentage (GLIM criteria)
    if delta_mass_pct is not None:
        if delta_mass_pct > THRESHOLDS["delta_mass_pct"]["red_min"]:
            rules.append(TriggeredRule(
                metric="delta_mass_pct", category="C", severity="red",
                current_value=round(delta_mass_pct, 2),
                reference_threshold="> 10% in <= 6 months (GLIM Stage 2)",
                source=THRESHOLDS["delta_mass_pct"]["source"], notification_id="C1"
            ))
        elif delta_mass_pct >= THRESHOLDS["delta_mass_pct"]["yellow_min"]:
            rules.append(TriggeredRule(
                metric="delta_mass_pct", category="C", severity="yellow",
                current_value=round(delta_mass_pct, 2),
                reference_threshold="5-10% in <= 6 months (GLIM Stage 1)",
                source=THRESHOLDS["delta_mass_pct"]["source"], notification_id="C1"
            ))

    # 2. Body Mass Index (BMI)
    if bmi is not None:
        if bmi < THRESHOLDS["bmi"]["red_max"]:
            rules.append(TriggeredRule(
                metric="bmi", category="C", severity="red",
                current_value=round(bmi, 2), reference_threshold="< 18.5 kg/m²",
                source=THRESHOLDS["bmi"]["source"], notification_id="C1"
            ))
        elif bmi < THRESHOLDS["bmi"]["yellow_max"]:
            rules.append(TriggeredRule(
                metric="bmi", category="C", severity="yellow",
                current_value=round(bmi, 2), reference_threshold="< 20.0 kg/m²",
                source=THRESHOLDS["bmi"]["source"], notification_id="C1"
            ))

    # 3. Skeletal Muscle Index (SMI) by sex
    is_low_smi = False
    if smi_total_kg_m2 is not None:
        smi_cfg = THRESHOLDS["smi_male"] if sex == 1 else THRESHOLDS["smi_female"]
        if smi_total_kg_m2 <= smi_cfg["red_max"]:
            is_low_smi = True
            rules.append(TriggeredRule(
                metric="smi_total_kg_m2", category="C", severity="red",
                current_value=round(smi_total_kg_m2, 2),
                reference_threshold=f"<= {smi_cfg['red_max']} kg/m²",
                source=smi_cfg["source"], notification_id="C2"
            ))
        elif smi_total_kg_m2 <= smi_cfg["yellow_max"]:
            is_low_smi = True
            rules.append(TriggeredRule(
                metric="smi_total_kg_m2", category="C", severity="yellow",
                current_value=round(smi_total_kg_m2, 2),
                reference_threshold=f"<= {smi_cfg['yellow_max']} kg/m²",
                source=smi_cfg["source"], notification_id="C2"
            ))

    # 4. Appendicular Skeletal Muscle Index (ASMI) optional
    if asmi_kg_m2 is not None:
        asmi_limit = (
            THRESHOLDS["asmi_bia"]["red_male_max"]
            if sex == 1
            else THRESHOLDS["asmi_bia"]["red_female_max"]
        )
        if asmi_kg_m2 < asmi_limit:
            is_low_smi = True
            rules.append(TriggeredRule(
                metric="asmi_kg_m2", category="C", severity="red",
                current_value=round(asmi_kg_m2, 2),
                reference_threshold=f"< {asmi_limit} kg/m² (BIA)",
                source=THRESHOLDS["asmi_bia"]["source"], notification_id="C2"
            ))

    # 5. Visceral fat area (cm2 + sarcopenic obesity combination)
    if visceral_fat_area_cm2 is not None and visceral_fat_area_cm2 >= 100.0:
        area_severity: Severity = "red" if is_low_smi else "yellow"
        area_ref = ">= 100 cm² + low SMI (Sarcopenic obesity)" if is_low_smi else ">= 100 cm²"
        rules.append(TriggeredRule(
            metric="visceral_fat_area_cm2", category="C", severity=area_severity,
            current_value=round(visceral_fat_area_cm2, 1),
            reference_threshold=area_ref,
            source=THRESHOLDS["visceral_fat_area_cm2"]["source"], notification_id="C3"
        ))

    # 6. Visceral fat level (Tanita scale 1-59)
    if visceral_fat_level is not None:
        if visceral_fat_level >= THRESHOLDS["visceral_fat_tanita"]["red_min"] or (
            visceral_fat_level >= THRESHOLDS["visceral_fat_tanita"]["yellow_min"] and is_low_smi
        ):
            rules.append(TriggeredRule(
                metric="visceral_fat_level", category="C", severity="red",
                current_value=visceral_fat_level,
                reference_threshold=">= 20 (or >= 13 with low SMI)",
                source=THRESHOLDS["visceral_fat_tanita"]["source"], notification_id="C3"
            ))
        elif visceral_fat_level >= THRESHOLDS["visceral_fat_tanita"]["yellow_min"]:
            rules.append(TriggeredRule(
                metric="visceral_fat_level", category="C", severity="yellow",
                current_value=visceral_fat_level,
                reference_threshold="13 - 19 (Tanita scale)",
                source=THRESHOLDS["visceral_fat_tanita"]["source"], notification_id="C3"
            ))

    return rules


def evaluate_problem_f(
    sleep_hours_history: list[float] | None = None,
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
        last_2 = sleep_hours_history[-2:] if len(sleep_hours_history) >= 2 else []
        last_3 = sleep_hours_history[-3:] if len(sleep_hours_history) >= 3 else []
        if len(last_2) == 2 and all(h < 5.0 for h in last_2):
            rules.append(TriggeredRule(
                metric="sleep_total_min", category="F", severity="red",
                current_value=round(sleep_hours_history[-1], 2),
                reference_threshold="< 5 h for 2 consecutive nights",
                source=THRESHOLDS["sleep_hours"]["source"], notification_id="F1"
            ))
        elif len(last_3) == 3 and all(h < 6.0 for h in last_3):
            rules.append(TriggeredRule(
                metric="sleep_total_min", category="F", severity="yellow",
                current_value=round(sleep_hours_history[-1], 2),
                reference_threshold="< 6 h for 3 consecutive nights",
                source=THRESHOLDS["sleep_hours"]["source"], notification_id="F1"
            ))

    # 2. Cortisol Awakening Response (CAR)
    if car_absolute_nmol_l is not None:
        if car_blunted_consecutive_weeks >= 2 and car_absolute_nmol_l < 2.5:
            rules.append(TriggeredRule(
                metric="car_absolute_nmol_l", category="F", severity="red",
                current_value=round(car_absolute_nmol_l, 2),
                reference_threshold="Blunted CAR (< +2.5 nmol/L) for 2 consecutive weeks",
                source=THRESHOLDS["car_delta_nmol_l"]["source"], notification_id="F1"
            ))
        elif car_absolute_nmol_l < 2.5:
            rules.append(TriggeredRule(
                metric="car_absolute_nmol_l", category="F", severity="yellow",
                current_value=round(car_absolute_nmol_l, 2),
                reference_threshold="Increase < +2.5 nmol/L at +30 min",
                source=THRESHOLDS["car_delta_nmol_l"]["source"], notification_id="F1"
            ))

    # 3. Salivary cortisol by time slot
    if cortisol_morning_nmol_l is not None and not (13.0 <= cortisol_morning_nmol_l <= 24.0):
        rules.append(TriggeredRule(
            metric="cortisol_07_08h", category="F", severity="yellow",
            current_value=round(cortisol_morning_nmol_l, 2),
            reference_threshold="Outside 13-24 nmol/L range",
            source=THRESHOLDS["cortisol_morning_nmol_l"]["source"], notification_id="F1"
        ))

    if cortisol_afternoon_nmol_l is not None and not (3.0 <= cortisol_afternoon_nmol_l <= 8.0):
        rules.append(TriggeredRule(
            metric="cortisol_16_17h", category="F", severity="yellow",
            current_value=round(cortisol_afternoon_nmol_l, 2),
            reference_threshold="Outside 3-8 nmol/L range",
            source=THRESHOLDS["cortisol_afternoon_nmol_l"]["source"], notification_id="F1"
        ))

    if cortisol_night_nmol_l is not None and cortisol_night_nmol_l > 4.0:
        is_c_dhea_rising = cortisol_dhea_ratio_rising or (
            cortisol_dhea_ratio_z is not None and cortisol_dhea_ratio_z > 1.5
        )
        night_severity: Severity = "red" if is_c_dhea_rising else "yellow"
        night_ref = "> 4 nmol/L + rising C/DHEA ratio" if is_c_dhea_rising else "> 4 nmol/L (23-24 h)"
        rules.append(TriggeredRule(
            metric="cortisol_23_24h", category="F", severity=night_severity,
            current_value=round(cortisol_night_nmol_l, 2),
            reference_threshold=night_ref,
            source=THRESHOLDS["cortisol_night_nmol_l"]["source"], notification_id="F1"
        ))

    # 4. Total daily cortisol output (AUCg Z-score)
    if aucg_cortisol_z is not None:
        if aucg_cortisol_z > 3.0:
            rules.append(TriggeredRule(
                metric="aucg_cortisol", category="F", severity="red",
                current_value=round(aucg_cortisol_z, 2), reference_threshold="z > +3.0",
                source=THRESHOLDS["aucg_cortisol_z"]["source"], notification_id="F1"
            ))
        elif aucg_cortisol_z > 1.5:
            rules.append(TriggeredRule(
                metric="aucg_cortisol", category="F", severity="yellow",
                current_value=round(aucg_cortisol_z, 2), reference_threshold="z > +1.5",
                source=THRESHOLDS["aucg_cortisol_z"]["source"], notification_id="F1"
            ))

    # 5. Diurnal cortisol slope
    if cortisol_diurnal_slope_flattened:
        rules.append(TriggeredRule(
            metric="cortisol_diurnal_slope", category="F", severity="yellow",
            current_value="Flattened", reference_threshold="Flattened slope vs. baseline",
            source="Medicina 2021 / Team [12]", notification_id="F1"
        ))

    # 6. Cortisol/DHEA molar ratio (Z-score)
    if cortisol_dhea_ratio_z is not None:
        if cortisol_dhea_ratio_z > 3.0:
            rules.append(TriggeredRule(
                metric="cortisol_dhea_molar_ratio", category="F", severity="red",
                current_value=round(cortisol_dhea_ratio_z, 2), reference_threshold="z > +3.0 vs. baseline",
                source=THRESHOLDS["cortisol_dhea_ratio_z"]["source"], notification_id="F1"
            ))
        elif cortisol_dhea_ratio_z > 1.5:
            rules.append(TriggeredRule(
                metric="cortisol_dhea_molar_ratio", category="F", severity="yellow",
                current_value=round(cortisol_dhea_ratio_z, 2), reference_threshold="z > +1.5 vs. baseline",
                source=THRESHOLDS["cortisol_dhea_ratio_z"]["source"], notification_id="F1"
            ))

    # 7. Salivary alpha-amylase (sAA Z-score)
    if saa_z is not None and saa_z > 2.0:
        rules.append(TriggeredRule(
            metric="salivary_alpha_amylase_u_ml", category="F", severity="yellow",
            current_value=round(saa_z, 2), reference_threshold="z > +2.0 vs. baseline",
            source=THRESHOLDS["saa_z"]["source"], notification_id="F1"
        ))

    # 8. Resting HRV RMSSD and SDNN (Autonomic/Circadian axis)
    if hrv_rmssd_z is not None:
        if hrv_rmssd_z < -3.0:
            rules.append(TriggeredRule(
                metric="hrv_rmssd_daily_ms", category="F", severity="red",
                current_value=round(hrv_rmssd_z, 2), reference_threshold="z < -3.0",
                source=THRESHOLDS["hrv_rmssd_z_circadian"]["source"], notification_id="F1"
            ))
        elif hrv_rmssd_z < -1.5:
            rules.append(TriggeredRule(
                metric="hrv_rmssd_daily_ms", category="F", severity="yellow",
                current_value=round(hrv_rmssd_z, 2), reference_threshold="z < -1.5",
                source=THRESHOLDS["hrv_rmssd_z_circadian"]["source"], notification_id="F1"
            ))

    if hrv_sdnn_z is not None and hrv_sdnn_z < -1.5:
        rules.append(TriggeredRule(
            metric="hrv_sdnn_daily_ms", category="F", severity="yellow",
            current_value=round(hrv_sdnn_z, 2), reference_threshold="z < -1.5",
            source=THRESHOLDS["hrv_sdnn_z"]["source"], notification_id="F1"
        ))

    return rules


def evaluate_problem_g(
    skin_temp_deltas_history: list[float] | None = None,
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
    temp_candidates: list[TriggeredRule] = []
    vital_sign_candidates: list[TriggeredRule] = []

    # 1. Nocturnal skin temperature deviation (with optional luteal phase offset in females)
    if skin_temp_deltas_history:
        offset = 0.0
        if compensate_luteal_in_code and sex == 0 and cycle_phase == "luteal":
            offset = THRESHOLDS["skin_temp_delta_c"]["luteal_offset"]

        temp_limit = THRESHOLDS["skin_temp_delta_c"]["threshold"] + offset
        last_2 = skin_temp_deltas_history[-2:] if len(skin_temp_deltas_history) >= 2 else []
        last_3 = skin_temp_deltas_history[-3:] if len(skin_temp_deltas_history) >= 3 else []

        if len(last_3) == 3 and all(dt >= temp_limit for dt in last_3):
            temp_candidates.append(TriggeredRule(
                metric="skin_temp_delta_c", category="G", severity="red",
                current_value=round(skin_temp_deltas_history[-1], 2),
                reference_threshold=f">= +{temp_limit:.1f} °C for 3 consecutive nights",
                source=THRESHOLDS["skin_temp_delta_c"]["source"], notification_id="G1"
            ))
        elif len(last_2) == 2 and all(dt >= temp_limit for dt in last_2):
            temp_candidates.append(TriggeredRule(
                metric="skin_temp_delta_c", category="G", severity="yellow",
                current_value=round(skin_temp_deltas_history[-1], 2),
                reference_threshold=f">= +{temp_limit:.1f} °C for 2 consecutive nights",
                source=THRESHOLDS["skin_temp_delta_c"]["source"], notification_id="G1"
            ))

    # 2. Core body temperature (Thermometer)
    if core_temp_c is not None:
        if core_temp_c > 38.0 or core_temp_c < 36.0:
            temp_candidates.append(TriggeredRule(
                metric="core_temp_c", category="G", severity="red",
                current_value=round(core_temp_c, 2),
                reference_threshold="> 38.0 °C or < 36.0 °C",
                source=THRESHOLDS["core_temp_c"]["source"], notification_id="G1"
            ))

    # 3. Accompanying sign: Resting Heart Rate
    if resting_hr_bpm is not None and resting_hr_bpm > 90.0:
        vital_sign_candidates.append(TriggeredRule(
            metric="resting_hr_bpm", category="G", severity="red",
            current_value=round(resting_hr_bpm, 1), reference_threshold="> 90 bpm",
            source=THRESHOLDS["resting_hr"]["source"], notification_id="G1"
        ))
    elif resting_hr_z is not None and resting_hr_z > 1.5:
        vital_sign_candidates.append(TriggeredRule(
            metric="resting_hr_bpm", category="G", severity="yellow",
            current_value=round(resting_hr_z, 2), reference_threshold="z > +1.5 vs. baseline",
            source=THRESHOLDS["resting_hr"]["source"], notification_id="G1"
        ))

    # 4. Accompanying sign: HRV (Z_HRV = (ln RMSSD_today - mu_ln) / sigma_ln)
    if hrv_z_score is not None:
        if hrv_z_score < -2.5:
            vital_sign_candidates.append(TriggeredRule(
                metric="hrv_z_score", category="G", severity="red",
                current_value=round(hrv_z_score, 2), reference_threshold="Z_HRV < -2.5",
                source=THRESHOLDS["hrv_z_infection"]["source"], notification_id="G1"
            ))
        elif hrv_z_score < -1.5:
            vital_sign_candidates.append(TriggeredRule(
                metric="hrv_z_score", category="G", severity="yellow",
                current_value=round(hrv_z_score, 2), reference_threshold="Z_HRV < -1.5",
                source=THRESHOLDS["hrv_z_infection"]["source"], notification_id="G1"
            ))

    # 5. Accompanying sign: Respiratory Rate
    if respiratory_rate_rpm is not None and respiratory_rate_rpm > 20.0:
        vital_sign_candidates.append(TriggeredRule(
            metric="respiratory_rate_daily_rpm", category="G", severity="red",
            current_value=round(respiratory_rate_rpm, 1), reference_threshold="> 20 rpm",
            source=THRESHOLDS["respiratory_rate_rpm"]["source"], notification_id="G1"
        ))
    elif respiratory_rate_rising:
        vital_sign_candidates.append(TriggeredRule(
            metric="respiratory_rate_daily_rpm", category="G", severity="yellow",
            current_value=round(respiratory_rate_rpm or 0.0, 1),
            reference_threshold="Rising vs. personal baseline",
            source=THRESHOLDS["respiratory_rate_rpm"]["source"], notification_id="G1"
        ))

    # Anti-false-positive filter: requires Temperature + at least 1 other sign (HR, HRV, or RR)
    if temp_candidates and vital_sign_candidates:
        return temp_candidates + vital_sign_candidates
    return []


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
            rules.append(TriggeredRule(
                metric="blood_glucose_mg_dl", category="S", severity="red",
                current_value=round(blood_glucose_mg_dl, 1),
                reference_threshold="< 54 mg/dL (Level 2) or > 250 mg/dL",
                source=THRESHOLDS["glucose_mg_dl"]["source"], notification_id="S1"
            ))
        elif blood_glucose_mg_dl < 70.0 or blood_glucose_mg_dl > 180.0:
            rules.append(TriggeredRule(
                metric="blood_glucose_mg_dl", category="S", severity="yellow",
                current_value=round(blood_glucose_mg_dl, 1),
                reference_threshold="< 70 mg/dL (Level 1) or > 180 mg/dL",
                source=THRESHOLDS["glucose_mg_dl"]["source"], notification_id="S1"
            ))

    # S2: Estimated blood pressure
    if systolic_bp_mmhg is not None and diastolic_bp_mmhg is not None:
        bp_str = f"{int(systolic_bp_mmhg)}/{int(diastolic_bp_mmhg)}"
        if systolic_bp_mmhg >= 180.0 or diastolic_bp_mmhg >= 120.0:
            rules.append(TriggeredRule(
                metric="blood_pressure_mmhg", category="S", severity="red",
                current_value=bp_str, reference_threshold=">= 180/120 mmHg (Crisis)",
                source=THRESHOLDS["blood_pressure_mmhg"]["source"], notification_id="S2"
            ))
        elif systolic_bp_mmhg >= 140.0 or diastolic_bp_mmhg >= 90.0:
            rules.append(TriggeredRule(
                metric="blood_pressure_mmhg", category="S", severity="yellow",
                current_value=bp_str, reference_threshold=">= 140/90 mmHg (Stage 2)",
                source=THRESHOLDS["blood_pressure_mmhg"]["source"], notification_id="S2"
            ))

    # S3: Oxygen saturation (Individual SpO2 or simultaneous drop in >= 2 crew members)
    if (spo2_pct is not None and spo2_pct < 90.0) or crew_spo2_drop_count >= 2:
        rules.append(TriggeredRule(
            metric="spo2_pct", category="S", severity="red",
            current_value=round(spo2_pct or 0.0, 1),
            reference_threshold="< 90% or simultaneous drop in 2+ crew members",
            source=THRESHOLDS["spo2_pct"]["source"], notification_id="S3"
        ))
    elif spo2_pct is not None and spo2_pct < 95.0:
        rules.append(TriggeredRule(
            metric="spo2_pct", category="S", severity="yellow",
            current_value=round(spo2_pct, 1), reference_threshold="< 95% sustained",
            source=THRESHOLDS["spo2_pct"]["source"], notification_id="S3"
        ))

    # S4: Space radiation (Cumulative dose vs. Solar Particle Event SPE)
    is_spe_active = bool(solar_event_flag) or (
        proton_flux_pfu is not None and proton_flux_pfu >= 10.0
    )
    if is_spe_active:
        rules.append(TriggeredRule(
            metric="radiation_spe", category="S", severity="red",
            current_value=proton_flux_pfu if proton_flux_pfu is not None else "Active SPE",
            reference_threshold="Solar event: protons >= 10 MeV at >= 10 pfu",
            source=THRESHOLDS["radiation"]["source"], notification_id="S4"
        ))
    elif cumulative_radiation_msv is not None:
        cumulative_limit = mission_radiation_limit_msv * THRESHOLDS["radiation"]["yellow_ratio"]
        if cumulative_radiation_msv >= cumulative_limit:
            rules.append(TriggeredRule(
                metric="cumulative_radiation_msv", category="S", severity="yellow",
                current_value=round(cumulative_radiation_msv, 2),
                reference_threshold=f"Cumulative >= 50% of limit ({cumulative_limit:.0f} mSv)",
                source=THRESHOLDS["radiation"]["source"], notification_id="S4"
            ))

    return rules


# =====================================================================
# 3. TRIAGE & SAMPLING MODE ORCHESTRATOR (For daily_health_reports)
# =====================================================================

def _get_max_severity(rules: list[TriggeredRule], category: AlertCategory) -> Severity:
    severities = [r.severity for r in rules if r.category == category]
    if "red" in severities:
        return "red"
    if "yellow" in severities:
        return "yellow"
    return "normal"


def evaluate_overall_health(
    rules_c: list[TriggeredRule],
    rules_f: list[TriggeredRule],
    rules_g: list[TriggeredRule],
    rules_s: list[TriggeredRule],
) -> EvaluationResult:
    """Consolidates all active rules, determines triage status and sampling mode.

    Sampling mode priority according to database schema:
    1. 'alert_infection' (If G is active)
    2. 'alert_atrophy'   (If C is active)
    3. 'alert_circadian' (If F is active)
    4. 'standard'        (Nominal state)
    """
    all_rules = rules_c + rules_f + rules_g + rules_s

    category_severities: dict[AlertCategory, Severity] = {
        "C": _get_max_severity(all_rules, "C"),
        "F": _get_max_severity(all_rules, "F"),
        "G": _get_max_severity(all_rules, "G"),
        "S": _get_max_severity(all_rules, "S"),
    }

    # Determine overall triage_status (green | yellow | red)
    if "red" in category_severities.values():
        triage: Literal["green", "yellow", "red"] = "red"
    elif "yellow" in category_severities.values():
        triage = "yellow"
    else:
        triage = "green"

    # Determine the monitoring/sampling mode triggered by the alert
    if category_severities["G"] in ("yellow", "red"):
        sampling_mode: SamplingMode = "alert_infection"
    elif category_severities["C"] in ("yellow", "red"):
        sampling_mode = "alert_atrophy"
    elif category_severities["F"] in ("yellow", "red"):
        sampling_mode = "alert_circadian"
    else:
        sampling_mode = "standard"

    return EvaluationResult(
        triage_status=triage,
        recommended_sampling_mode=sampling_mode,
        triggered_rules=all_rules,
        category_severities=category_severities,
    )


# =====================================================================
# QUICK USAGE EXAMPLE (Run with: python core/ranges.py)
# =====================================================================
if __name__ == "__main__":
    import json

    # Simulated case: Astronaut with suspected early infection (Problem G)
    # and mild stress-induced hyperglycemia.
    demo_rules_g = evaluate_problem_g(
        skin_temp_deltas_history=[0.6, 0.7, 0.8],  # 3 nights >= +0.5 °C -> Red
        core_temp_c=38.3,                          # Fever > 38 °C -> Red
        resting_hr_bpm=94.0,                       # > 90 bpm -> Red
        hrv_z_score=-2.7,                          # Z_HRV < -2.5 -> Red
        respiratory_rate_rpm=22.0,                 # > 20 rpm -> Red
    )
    demo_rules_s = evaluate_safety(blood_glucose_mg_dl=195.0)  # Yellow (> 180 mg/dL)

    evaluation = evaluate_overall_health(
        rules_c=[],
        rules_f=[],
        rules_g=demo_rules_g,
        rules_s=demo_rules_s,
    )

    print(json.dumps(evaluation.to_dict(), indent=2))
