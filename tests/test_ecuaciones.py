"""Unit test suite for clinical equations (`core/ecuaciones.py`) and readiness (`core/puntaje.py`).

Validates all 17 mathematical models against known reference values from peer-reviewed
literature (Bergenstal 2018, Tanaka 2001, Mifflin 1990, GLIM 2019, Janssen 2004,
EWGSOP2 2019, Wüst 2000, Pruessner 2003, Task Force 1996, Karjalainen 1986,
Battelino 2019, van Hees 2013, Tomić Mahečić 2022) and team custom designs.
"""

from __future__ import annotations

import math
import pytest

try:
    from core.ecuaciones import (
        DHEA_PG_ML_TO_NMOL_L,
        compute_anemia_suspicion_index,
        compute_asmi,
        compute_aucg,
        compute_bmi,
        compute_bmr_mifflin,
        compute_car,
        compute_cortisol_dhea_molar_ratio,
        compute_cortisol_diurnal_slope,
        compute_cumulative_radiation_msv,
        compute_delta_mass_pct,
        compute_enmo_mg,
        compute_glim_malnutrition_risk,
        compute_glucose_cv_pct,
        compute_glucose_tir_pct,
        compute_gmi_pct,
        compute_hrv_log_z_score,
        compute_max_hr_tanaka,
        compute_oxygen_delivery_do2,
        compute_readiness_score,
        compute_rmssd,
        compute_sdnn,
        compute_skeletal_muscle_mass_kg,
        compute_skin_temp_delta,
        compute_smi,
        compute_sns_hpa_asymmetry_index,
        compute_z_score,
        convert_dhea_pg_ml_to_nmol_l,
        evaluate_liebermeister_rule,
    )
except ImportError:
    from core.equations import (  # type: ignore[no-redef]
        DHEA_PG_ML_TO_NMOL_L,
        compute_anemia_suspicion_index,
        compute_asmi,
        compute_aucg,
        compute_bmi,
        compute_bmr_mifflin,
        compute_car,
        compute_cortisol_dhea_molar_ratio,
        compute_cortisol_diurnal_slope,
        compute_cumulative_radiation_msv,
        compute_delta_mass_pct,
        compute_enmo_mg,
        compute_glim_malnutrition_risk,
        compute_glucose_cv_pct,
        compute_glucose_tir_pct,
        compute_gmi_pct,
        compute_hrv_log_z_score,
        compute_max_hr_tanaka,
        compute_oxygen_delivery_do2,
        compute_readiness_score,
        compute_rmssd,
        compute_sdnn,
        compute_skeletal_muscle_mass_kg,
        compute_skin_temp_delta,
        compute_smi,
        compute_sns_hpa_asymmetry_index,
        compute_z_score,
        convert_dhea_pg_ml_to_nmol_l,
        evaluate_liebermeister_rule,
    )

from core.puntaje import (
    build_z_penalties,
    calculate_readiness_from_report,
    evaluate_daily_readiness,
)


# =====================================================================
# 1. BASELINE & STATISTICAL EQUATIONS (Mishra 2020, Task Force 1996)
# =====================================================================

def test_compute_z_score_standard_and_zero_std() -> None:
    """Mishra 2020: Z = (x - mu) / sigma, with safe zero-division protection."""
    assert compute_z_score(value=75.0, mean=65.0, std=5.0) == pytest.approx(2.0)
    assert compute_z_score(value=60.0, mean=65.0, std=5.0) == pytest.approx(-1.0)
    assert compute_z_score(value=75.0, mean=65.0, std=0.0) == 0.0
    assert compute_z_score(value=75.0, mean=65.0, std=-2.0) == 0.0


def test_compute_hrv_log_z_score() -> None:
    """Task Force 1996: Log-transformed RMSSD Z-score against personal baseline."""
    baseline = [math.exp(3.5), math.exp(3.7), math.exp(3.9)]  # ln values: 3.5, 3.7, 3.9 (mu=3.7, sd=0.2)
    rmssd_today = math.exp(3.3)  # ln = 3.3 -> Z = (3.3 - 3.7) / 0.2 = -2.0
    assert compute_hrv_log_z_score(rmssd_today, baseline) == pytest.approx(-2.0, rel=1e-5)
    assert compute_hrv_log_z_score(0.0, baseline) == 0.0
    assert compute_hrv_log_z_score(40.0, [42.0]) == 0.0


# =====================================================================
# 2. PROBLEM C: BODY COMPOSITION & METABOLISM (GLIM, Janssen, Mifflin)
# =====================================================================

def test_compute_delta_mass_pct() -> None:
    """GLIM 2019: ((M0 - M_actual) / M0) * 100 where positive means weight loss."""
    assert compute_delta_mass_pct(80.0, 74.0) == pytest.approx(7.5)
    assert compute_delta_mass_pct(80.0, 82.0) == pytest.approx(-2.5)
    with pytest.raises(ValueError, match="initial_weight_kg"):
        compute_delta_mass_pct(0.0, 75.0)


def test_compute_bmi_and_smi_and_asmi() -> None:
    """GLIM 2019, Janssen 2004, EWGSOP2 2019: BMI, SMM, SMI, and ASMI."""
    # 80 kg, 200 cm (2.0 m) -> BMI = 80 / 4 = 20.0 kg/m2
    assert compute_bmi(80.0, 200.0) == pytest.approx(20.0)
    # 80 kg, 40% muscle -> SMM = 32 kg; height = 2.0 m -> SMI = 32 / 4 = 8.0 kg/m2
    assert compute_skeletal_muscle_mass_kg(80.0, 40.0) == pytest.approx(32.0)
    assert compute_smi(80.0, 40.0, 200.0) == pytest.approx(8.0)
    # Appendicular muscle = 24 kg, height = 2.0 m -> ASMI = 6.0 kg/m2
    assert compute_asmi(24.0, 200.0) == pytest.approx(6.0)


@pytest.mark.parametrize(
    ("weight_kg", "height_cm", "age", "sex", "expected_kcal"),
    [
        # Male (sex=1): 10(80) + 6.25(180) - 5(35) + 5 = 800 + 1125 - 175 + 5 = 1755.0 kcal
        (80.0, 180.0, 35, 1, 1755.0),
        # Female (sex=0): 10(60) + 6.25(165) - 5(30) - 161 = 600 + 1031.25 - 150 - 161 = 1320.25 kcal
        (60.0, 165.0, 30, 0, 1320.25),
    ],
)
def test_compute_bmr_mifflin_published_formula(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: int,
    expected_kcal: float,
) -> None:
    """Mifflin et al. 1990 (AJCN): Resting energy expenditure for males and females."""
    assert compute_bmr_mifflin(weight_kg, height_cm, age, sex) == pytest.approx(expected_kcal)


def test_compute_glim_malnutrition_risk_stages() -> None:
    """Cederholm et al. 2019: GLIM Stage 0 (Normal), Stage 1 (Moderate), Stage 2 (Severe)."""
    assert compute_glim_malnutrition_risk(delta_mass_pct=3.0, bmi=22.5, mission_months=4.0) == 0
    assert compute_glim_malnutrition_risk(delta_mass_pct=6.5, bmi=22.5, mission_months=4.0) == 1
    assert compute_glim_malnutrition_risk(delta_mass_pct=2.0, bmi=19.2, mission_months=4.0) == 1
    assert compute_glim_malnutrition_risk(delta_mass_pct=11.0, bmi=21.0, mission_months=4.0) == 2
    assert compute_glim_malnutrition_risk(delta_mass_pct=2.0, bmi=18.1, mission_months=4.0) == 2


# =====================================================================
# 3. PROBLEM F: CIRCADIAN RHYTHM & HPA AXIS (Wüst 2000, Pruessner 2003)
# =====================================================================

def test_compute_car_wust_2000_reference() -> None:
    """Wüst et al. 2000: Normal CAR exhibits ~50% increase 30 minutes post-awakening."""
    delta_car, car_pct = compute_car(cortisol_wake_0m_nmol_l=15.0, cortisol_wake_30m_nmol_l=22.5)
    assert delta_car == pytest.approx(7.5)
    assert car_pct == pytest.approx(50.0)


def test_compute_cortisol_diurnal_slope() -> None:
    """Medicina 2021: Negative slope from morning peak to bedtime."""
    # Peak 22.0 nmol/L at 07:30, bedtime 2.0 nmol/L at 23:30 (delta = 16 h) -> -20 / 16 = -1.25 nmol/L/h
    slope = compute_cortisol_diurnal_slope(22.0, 2.0, delta_hours=16.0)
    assert slope == pytest.approx(-1.25)


def test_compute_aucg_pruessner_2003_trapezoidal_rule() -> None:
    """Pruessner et al. 2003: Trapezoidal area under the curve with respect to ground."""
    measurements = [14.0, 22.0, 6.0, 2.0]
    time_points_h = [0.0, 0.5, 8.5, 15.5]
    # Segment 1: ((14+22)/2)*0.5 = 9.0
    # Segment 2: ((22+6)/2)*8.0  = 112.0
    # Segment 3: ((6+2)/2)*7.0   = 28.0 -> Total = 149.0
    assert compute_aucg(measurements, time_points_h) == pytest.approx(149.0)


def test_cortisol_dhea_molar_ratio_and_asymmetry() -> None:
    """Mehta 2017 & Ali/Pruessner 2012: Molar conversion (MW=288.42) and AOCg ratio."""
    dhea_nmol = convert_dhea_pg_ml_to_nmol_l(1000.0)
    assert dhea_nmol == pytest.approx(1000.0 * DHEA_PG_ML_TO_NMOL_L)
    assert compute_cortisol_dhea_molar_ratio(20.0, dhea_nmol_l=4.0) == pytest.approx(5.0)
    assert compute_sns_hpa_asymmetry_index(aucg_saa=300.0, aucg_cortisol=150.0) == pytest.approx(2.0)


# =====================================================================
# 4. PROBLEM G: HRV & INFECTION (Task Force 1996, Karjalainen 1986)
# =====================================================================

def test_compute_rmssd_and_sdnn_task_force_1996() -> None:
    """Task Force 1996: Standard time-domain HRV metrics RMSSD and SDNN."""
    rr_ms = [800.0, 830.0, 790.0, 820.0]
    # Successive diffs: +30, -40, +30 -> squared: 900, 1600, 900 -> mean = 3400/3 -> sqrt = 33.665016
    assert compute_rmssd(rr_ms) == pytest.approx(math.sqrt(3400.0 / 3.0))
    # Mean RR = 810.0 -> diffs from mean: -10, +20, -20, +10 -> sum sq = 1000 -> var = 1000/3
    assert compute_sdnn(rr_ms) == pytest.approx(math.sqrt(1000.0 / 3.0))


def test_skin_temp_delta_and_liebermeister_rule_karjalainen_1986() -> None:
    """Karjalainen 1986: +8.5 bpm per +1 °C fever elevation."""
    delta_t = compute_skin_temp_delta(37.2, 36.2)
    assert delta_t == pytest.approx(1.0)

    # Baseline HR = 60 bpm, +1.0 °C -> expected HR = 68.5 bpm
    expected_hr, flag_normal = evaluate_liebermeister_rule(
        resting_hr_bpm=70.0, baseline_hr_bpm=60.0, temp_delta_c=1.0, tolerance_bpm=5.0
    )
    assert expected_hr == pytest.approx(68.5)
    assert flag_normal == 0

    # Disproportionate tachycardia (82 bpm vs 68.5 expected -> flag = 1)
    _, flag_dissociation = evaluate_liebermeister_rule(
        resting_hr_bpm=82.0, baseline_hr_bpm=60.0, temp_delta_c=1.0, tolerance_bpm=5.0
    )
    assert flag_dissociation == 1


# =====================================================================
# 5. GLYCEMIC, CARDIO, ENMO, DO2 & READINESS (Bergenstal, Tanaka, etc.)
# =====================================================================

def test_compute_gmi_bergenstal_2018_known_reference_values() -> None:
    """Bergenstal et al. 2018 (Diabetes Care Table 1): Known GMI reference values.

    - Mean glucose 154 mg/dL -> GMI = 3.31 + 0.02392 * 154 = 6.99368% (~7.0%).
    - Mean glucose 126 mg/dL -> GMI = 3.31 + 0.02392 * 126 = 6.32392% (~6.3%).
    - Mean glucose 200 mg/dL -> GMI = 3.31 + 0.02392 * 200 = 8.094% (~8.1%).
    """
    gmi_154 = compute_gmi_pct(154.0)
    assert round(gmi_154, 1) == 7.0
    assert gmi_154 == pytest.approx(6.99368, rel=1e-5)
    assert round(compute_gmi_pct(126.0), 1) == 6.3
    assert round(compute_gmi_pct(200.0), 1) == 8.1


def test_compute_glucose_cv_and_tir_battelino_2019() -> None:
    """Battelino et al. 2019: Glycemic CV% (sigma/mu * 100) and Time in Range (70-180 mg/dL)."""
    readings = [65.0, 100.0, 120.0, 140.0, 195.0]  # 3 out of 5 within [70, 180] -> 60% TIR
    assert compute_glucose_tir_pct(readings) == pytest.approx(60.0)

    # Readings with known mean = 100, sample std = 36 -> CV = 36.0%
    stable_series = [100.0 - 36.0, 100.0, 100.0 + 36.0]
    assert compute_glucose_cv_pct(stable_series) == pytest.approx(36.0)


def test_compute_max_hr_tanaka_2001() -> None:
    """Tanaka et al. 2001 (JACC): HR_max = 208 - 0.7 * age."""
    assert compute_max_hr_tanaka(40) == pytest.approx(180.0)
    assert compute_max_hr_tanaka(30) == pytest.approx(187.0)


def test_compute_enmo_van_hees_2013() -> None:
    """van Hees et al. 2013 (PLoS ONE): max(sqrt(x^2 + y^2 + z^2) - 1, 0) * 1000 mg."""
    # Static 1g gravity along Z-axis -> 0 mg
    assert compute_enmo_mg(0.0, 0.0, 1.0) == pytest.approx(0.0)
    # Sub-1g microgravity float -> clamped to 0 mg
    assert compute_enmo_mg(0.2, 0.2, 0.2) == pytest.approx(0.0)
    # Active movement: vector magnitude = 1.25 g -> ENMO = 250.0 mg
    assert compute_enmo_mg(0.0, 0.75, 1.0) == pytest.approx(250.0)


def test_compute_oxygen_delivery_do2_and_radiation_and_anemia() -> None:
    """Tomić Mahečić 2022 & Zhang 2020: DO2 (mL O2/min), cumulative radiation, and anemia index."""
    # CO = 60 bpm * 70 mL / 1000 = 4.2 L/min; CaO2 = 1.34 * 15.0 * 1.0 = 20.1 mL/dL -> DO2 = 844.2 mL/min
    assert compute_oxygen_delivery_do2(60.0, 15.0, 100.0, stroke_volume_ml=70.0) == pytest.approx(844.2)
    assert compute_cumulative_radiation_msv([0.67, 0.67, 1.36]) == pytest.approx(2.70)

    idx_score, flag = compute_anemia_suspicion_index(
        resting_hr_z=2.0, spo2_drop_z=1.5, enmo_fatigue_z=1.0,
        baseline_hb_g_dl=15.0, current_hb_g_dl=13.2,
    )
    assert idx_score == pytest.approx(1.65)
    assert flag == 1


# =====================================================================
# 6. INTEGRATION WITH `core/puntaje.py` (Directional Penalties & Score)
# =====================================================================

def test_build_z_penalties_directional_inversion_and_thresholds() -> None:
    """Verifies that `core/puntaje.py` orients all 7 metrics so positive = worse."""
    # Case 1: Optimal astronaut (sleeping 8h, positive HRV Z-score, low HR, CV < 36%, weight gain)
    optimal_penalties = build_z_penalties(
        hrv_z_score=1.2,            # Good HRV -> 0.0 penalty
        resting_hr_z=-0.8,          # Low resting HR -> 0.0 penalty
        sleep_total_min=480,        # 8.0 hours (> 7.0 h) -> 0.0 penalty
        skin_temp_delta_c=-0.1,     # Below baseline -> 0.0 penalty
        aucg_cortisol_z=-0.5,       # Low cortisol -> 0.0 penalty
        cortisol_dhea_ratio_z=0.0,
        delta_mass_pct=-1.0,        # Gained 1% weight -> 0.0 penalty
        glucose_cv_pct=28.0,        # Below 36% threshold -> 0.0 penalty
    )
    assert all(val == 0.0 for val in optimal_penalties.values())
    assert compute_readiness_score(optimal_penalties) == 100.0

    # Case 2: Degraded astronaut metrics (matches screenshot specification)
    degraded_report = {
        "sleep_total_min": 348,       # 5.8 h -> deficit = 7.0 - 5.8 = 1.2
        "hrv_z_score": -2.0,          # Inverted -> hrv_drop = 2.0
        "resting_hr_z": 1.5,          # Kept -> resting_hr_elevation = 1.5
        "skin_temp_delta_c": 0.5,     # skin_temp_deviation = 0.5
        "aucg_cortisol_z": 1.8,       # cortisol_imbalance = 1.8
        "delta_mass_pct": 1.0,        # muscle_loss = 1.0
        "glucose_cv_pct": 38.0,       # 38.0 - 36.0 -> glycemic_instability = 2.0
    }
    breakdown = evaluate_daily_readiness(degraded_report)
    assert breakdown.z_penalties["sleep_deficit"] == pytest.approx(1.2)
    assert breakdown.z_penalties["hrv_drop"] == pytest.approx(2.0)
    assert breakdown.z_penalties["resting_hr_elevation"] == pytest.approx(1.5)
    assert breakdown.z_penalties["skin_temp_deviation"] == pytest.approx(0.5)
    assert breakdown.z_penalties["cortisol_imbalance"] == pytest.approx(1.8)
    assert breakdown.z_penalties["muscle_loss"] == pytest.approx(1.0)
    assert breakdown.z_penalties["glycemic_instability"] == pytest.approx(2.0)

    # Expected penalty: 8(1.2) + 7(2.0) + 6(1.5) + 7(0.5) + 5(1.8) + 5(1.0) + 4(2.0) = 58.1 -> Score = 41.9
    assert breakdown.readiness_score == pytest.approx(41.9)
    assert calculate_readiness_from_report(degraded_report) == pytest.approx(41.9)
