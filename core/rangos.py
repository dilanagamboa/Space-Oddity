"""Physiological thresholds, NASA radiation limits, and clinical rule evaluation.

Implements risk detection logic for the following health domains:
- Problem C: Muscle atrophy, malnutrition, and visceral fat (GLIM, Janssen, Tanita).
- Problem F: Circadian rhythm, sleep, and HPA axis (AASM/SRS, Wüst, Pruessner).
- Problem G: Early infection and autonomic overload (ACCP/SCCM, Mishra, Oura).
- Safety (S1-S4): Glucose (ADA), Blood Pressure (ACC/AHA), SpO2, Radiation (NASA/NOAA).

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from operator import ge, gt, le, lt
from typing import Any, Literal

Severity = Literal["normal", "yellow", "red"]
AlertCategory = Literal["C", "F", "G", "S"]
SamplingMode = Literal["standard", "alert_infection", "alert_atrophy", "alert_circadian"]
Compare = Callable[[float, float], bool]


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
    "sleep_hours": {"yellow": 6.0, "red": 5.0, "yellow_nights": 3, "red_nights": 2,
                    "source": "AASM/SRS Watson 2015 / Team [9]"},
    "car_delta_nmol_l": {"yellow": 2.5, "red_weeks": 2, "source": "Wüst 2000 / Team [10]"},
    "cortisol_morning_nmol_l": {"min": 13.0, "max": 24.0, "source": "Medicina 2021 [12]"},
    "cortisol_afternoon_nmol_l": {"min": 3.0, "max": 8.0, "source": "Medicina 2021 [12]"},
    "cortisol_night_nmol_l": {"yellow": 4.0, "source": "Medicina 2021 [12]"},
    "aucg_cortisol_z": {"yellow": 1.5, "red": 3.0, "source": "Pruessner 2003 [11]"},
    "cortisol_dhea_ratio_z": {"yellow": 1.5, "red": 3.0, "source": "Mehta 2017 / Team"},
    "saa_z": {"yellow": 2.0, "source": "Nater & Rohleder 2009 [14]"},
    "hrv_rmssd_z_circadian": {"yellow": -1.5, "red": -3.0,
                              "source": "Task Force 1996 / Nunan 2010 [15][16]"},
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


def _graded(
    rules: list[TriggeredRule], metric: str, cat: AlertCategory, value: float | None, key: str,
    notif: str, *, red: Compare, yellow: Compare | None = None, ref: str, digits: int = 2,
) -> Severity | None:
    """Appends a red or yellow rule when ``value`` crosses ``THRESHOLDS[key]``.

    ``red`` / ``yellow`` are comparisons such as ``operator.gt`` (``yellow`` defaults
    to ``red``). ``ref`` is formatted with ``{limit}``. Returns the severity added.
    """
    if value is None:
        return None
    for sev, compare in (("red", red), ("yellow", yellow or red)):
        limit = THRESHOLDS[key].get(sev)
        if limit is not None and compare(value, limit):
            rules.append(_rule(metric, cat, sev, value, ref.format(limit=limit), key, notif, digits))
            return sev
    return None


def _outside(value: float | None, key: str) -> bool:
    cfg = THRESHOLDS[key]
    return value is not None and not (cfg["min"] <= value <= cfg["max"])


def _streak(history: Sequence[float], nights: int, crossed: Callable[[float], bool]) -> bool:
    """True if the last ``nights`` values all cross the limit."""
    return len(history) >= nights and all(crossed(h) for h in history[-nights:])


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
    _graded(rules, "delta_mass_pct", "C", delta_mass_pct, "delta_mass_pct", "C1",
            red=gt, yellow=ge, ref=">= {limit}% loss in <= 6 months (GLIM)")
    _graded(rules, "bmi", "C", bmi, "bmi", "C1", red=lt, ref="< {limit} kg/m²")

    smi_key = "smi_male" if sex == 1 else "smi_female"
    is_low_smi = _graded(rules, "smi_total_kg_m2", "C", smi_total_kg_m2, smi_key, "C2",
                         red=le, ref="<= {limit} kg/m²") is not None
    if asmi_kg_m2 is not None:
        limit = THRESHOLDS["asmi_bia"]["male" if sex == 1 else "female"]
        if asmi_kg_m2 < limit:
            is_low_smi = True
            rules.append(_rule("asmi_kg_m2", "C", "red", asmi_kg_m2, f"< {limit} kg/m² (BIA)",
                               "asmi_bia", "C2"))

    # Visceral fat counts as red when muscle is also low (sarcopenic obesity)
    area_limit = THRESHOLDS["visceral_fat_area_cm2"]["yellow"]
    if visceral_fat_area_cm2 is not None and visceral_fat_area_cm2 >= area_limit:
        sev: Severity = "red" if is_low_smi else "yellow"
        ref = f">= {area_limit:g} cm²" + (" + low SMI (sarcopenic obesity)" if is_low_smi else "")
        rules.append(_rule("visceral_fat_area_cm2", "C", sev, visceral_fat_area_cm2, ref,
                           "visceral_fat_area_cm2", "C3", 1))
    tanita = THRESHOLDS["visceral_fat_tanita"]
    if visceral_fat_level is not None and visceral_fat_level >= tanita["yellow"]:
        red = visceral_fat_level >= tanita["red"] or is_low_smi
        ref = f">= {tanita['red']:g} (or >= {tanita['yellow']:g} with low SMI)" if red else "Tanita 13-19"
        rules.append(_rule("visceral_fat_level", "C", "red" if red else "yellow", visceral_fat_level, ref,
                           "visceral_fat_tanita", "C3"))
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

    sleep = THRESHOLDS["sleep_hours"]
    for sev in ("red", "yellow") if sleep_hours_history else ():
        nights = sleep[f"{sev}_nights"]
        if _streak(sleep_hours_history, nights, lambda h, s=sev: h < sleep[s]):
            ref = f"< {sleep[sev]:g} h for {nights} consecutive nights"
            last = sleep_hours_history[-1]
            rules.append(_rule("sleep_total_min", "F", sev, last, ref, "sleep_hours", "F1"))
            break

    car = THRESHOLDS["car_delta_nmol_l"]
    if car_absolute_nmol_l is not None and car_absolute_nmol_l < car["yellow"]:
        blunted = car_blunted_consecutive_weeks >= car["red_weeks"]
        ref = f"CAR < +{car['yellow']} nmol/L" + (" for consecutive weeks" if blunted else "")
        rules.append(_rule("car_absolute_nmol_l", "F", "red" if blunted else "yellow", car_absolute_nmol_l,
                           ref, "car_delta_nmol_l", "F1"))

    for metric, value, key in (("cortisol_07_08h", cortisol_morning_nmol_l, "cortisol_morning_nmol_l"),
                               ("cortisol_16_17h", cortisol_afternoon_nmol_l, "cortisol_afternoon_nmol_l")):
        if _outside(value, key):
            cfg = THRESHOLDS[key]
            ref = f"Outside {cfg['min']:g}-{cfg['max']:g} nmol/L"
            rules.append(_rule(metric, "F", "yellow", value, ref, key, "F1"))

    night_limit = THRESHOLDS["cortisol_night_nmol_l"]["yellow"]
    if cortisol_night_nmol_l is not None and cortisol_night_nmol_l > night_limit:
        rising = cortisol_dhea_ratio_rising or (
            cortisol_dhea_ratio_z is not None
            and cortisol_dhea_ratio_z > THRESHOLDS["cortisol_dhea_ratio_z"]["yellow"]
        )
        ref = f"> {night_limit:g} nmol/L (23-24 h)" + (" + rising C/DHEA ratio" if rising else "")
        rules.append(_rule("cortisol_23_24h", "F", "red" if rising else "yellow", cortisol_night_nmol_l,
                           ref, "cortisol_night_nmol_l", "F1"))

    z_up, z_down = "z > +{limit} vs. baseline", "z < {limit} vs. baseline"
    _graded(rules, "aucg_cortisol", "F", aucg_cortisol_z, "aucg_cortisol_z", "F1", red=gt, ref=z_up)
    if cortisol_diurnal_slope_flattened:
        rules.append(_rule("cortisol_diurnal_slope", "F", "yellow", "Flattened",
                           "Flattened slope vs. baseline", "cortisol_night_nmol_l", "F1"))
    _graded(rules, "cortisol_dhea_molar_ratio", "F", cortisol_dhea_ratio_z, "cortisol_dhea_ratio_z", "F1",
            red=gt, ref=z_up)
    _graded(rules, "salivary_alpha_amylase_u_ml", "F", saa_z, "saa_z", "F1", red=gt, ref=z_up)
    _graded(rules, "hrv_rmssd_daily_ms", "F", hrv_rmssd_z, "hrv_rmssd_z_circadian", "F1", red=lt, ref=z_down)
    _graded(rules, "hrv_sdnn_daily_ms", "F", hrv_sdnn_z, "hrv_sdnn_z", "F1", red=lt, ref=z_down)
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

    Mandatory combined rule (Section 5.3): alert G triggers ONLY if a temperature
    abnormality (nocturnal skin or core) coincides with at least one accompanying
    sign (HR, HRV or RR).
    """
    temp_rules: list[TriggeredRule] = []
    sign_rules: list[TriggeredRule] = []

    skin = THRESHOLDS["skin_temp_delta_c"]
    luteal = compensate_luteal_in_code and sex == 0 and cycle_phase == "luteal"
    limit = skin["threshold"] + (skin["luteal_offset"] if luteal else 0.0)
    for sev, nights in (("red", 3), ("yellow", 2)) if skin_temp_deltas_history else ():
        if _streak(skin_temp_deltas_history, nights, lambda dt: dt >= limit):
            ref = f">= +{limit:.1f} °C for {nights} consecutive nights"
            temp_rules.append(_rule("skin_temp_delta_c", "G", sev, skin_temp_deltas_history[-1], ref,
                                    "skin_temp_delta_c", "G1"))
            break
    if _outside(core_temp_c, "core_temp_c"):
        core = THRESHOLDS["core_temp_c"]
        temp_rules.append(_rule("core_temp_c", "G", "red", core_temp_c,
                                f"> {core['max']} °C or < {core['min']} °C", "core_temp_c", "G1"))

    hr = THRESHOLDS["resting_hr"]
    if resting_hr_bpm is not None and resting_hr_bpm > hr["red_bpm"]:
        sign_rules.append(_rule("resting_hr_bpm", "G", "red", resting_hr_bpm, f"> {hr['red_bpm']:g} bpm",
                                "resting_hr", "G1", 1))
    elif resting_hr_z is not None and resting_hr_z > hr["yellow_z"]:
        sign_rules.append(_rule("resting_hr_bpm", "G", "yellow", resting_hr_z,
                                f"z > +{hr['yellow_z']} vs. baseline", "resting_hr", "G1"))
    _graded(sign_rules, "hrv_z_score", "G", hrv_z_score, "hrv_z_infection", "G1",
            red=lt, ref="Z_HRV < {limit}")
    rr_limit = THRESHOLDS["respiratory_rate_rpm"]["red"]
    if respiratory_rate_rpm is not None and respiratory_rate_rpm > rr_limit:
        sign_rules.append(_rule("respiratory_rate_daily_rpm", "G", "red", respiratory_rate_rpm,
                                f"> {rr_limit:g} rpm", "respiratory_rate_rpm", "G1", 1))
    elif respiratory_rate_rising:
        sign_rules.append(_rule("respiratory_rate_daily_rpm", "G", "yellow", respiratory_rate_rpm or 0.0,
                                "Rising vs. personal baseline", "respiratory_rate_rpm", "G1", 1))

    # Anti-false-positive filter: temperature + at least one accompanying sign
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

    g = THRESHOLDS["glucose_mg_dl"]
    for sev in ("red", "yellow") if blood_glucose_mg_dl is not None else ():
        low, high = g[f"{sev}_low"], g[f"{sev}_high"]
        if blood_glucose_mg_dl < low or blood_glucose_mg_dl > high:
            rules.append(_rule("blood_glucose_mg_dl", "S", sev, blood_glucose_mg_dl,
                               f"< {low:g} or > {high:g} mg/dL", "glucose_mg_dl", "S1", 1))
            break

    bp = THRESHOLDS["blood_pressure_mmhg"]
    has_bp = systolic_bp_mmhg is not None and diastolic_bp_mmhg is not None
    for sev in ("red", "yellow") if has_bp else ():
        sys_limit, dia_limit = bp[f"{sev}_sys"], bp[f"{sev}_dia"]
        if systolic_bp_mmhg >= sys_limit or diastolic_bp_mmhg >= dia_limit:
            reading = f"{int(systolic_bp_mmhg)}/{int(diastolic_bp_mmhg)}"
            rules.append(_rule("blood_pressure_mmhg", "S", sev, reading,
                               f">= {sys_limit:g}/{dia_limit:g} mmHg", "blood_pressure_mmhg", "S2"))
            break

    spo2 = THRESHOLDS["spo2_pct"]
    if (spo2_pct is not None and spo2_pct < spo2["red"]) or crew_spo2_drop_count >= 2:
        rules.append(_rule("spo2_pct", "S", "red", spo2_pct or 0.0,
                           f"< {spo2['red']:g}% or drop in 2+ crew members", "spo2_pct", "S3", 1))
    elif spo2_pct is not None and spo2_pct < spo2["yellow"]:
        rules.append(_rule("spo2_pct", "S", "yellow", spo2_pct, f"< {spo2['yellow']:g}% sustained",
                           "spo2_pct", "S3", 1))

    rad = THRESHOLDS["radiation"]
    if solar_event_flag or (proton_flux_pfu is not None and proton_flux_pfu >= rad["proton_pfu"]):
        value = proton_flux_pfu if proton_flux_pfu is not None else "Active SPE"
        ref = f"Solar event: >= 10 MeV protons at >= {rad['proton_pfu']:g} pfu"
        rules.append(_rule("radiation_spe", "S", "red", value, ref, "radiation", "S4"))
    elif cumulative_radiation_msv is not None:
        limit = mission_radiation_limit_msv * rad["yellow_ratio"]
        if cumulative_radiation_msv >= limit:
            rules.append(_rule("cumulative_radiation_msv", "S", "yellow", cumulative_radiation_msv,
                               f"Cumulative >= {rad['yellow_ratio']:.0%} of limit ({limit:.0f} mSv)",
                               "radiation", "S4"))
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
    triage: Literal["green", "yellow", "red"] = (
        "red" if "red" in vals else ("yellow" if "yellow" in vals else "green")
    )

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
